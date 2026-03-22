"""
License: MIT
Description: Workflow executor — runs multi-step YAML workflows with per-step progress.
Extracted from run_prompt_with_context.py for use by both the CLI and the workflow server.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import Callable

import yaml

from common.http_client import http_client
from common.skill_lifecycle import find_live_worker
from common.timeouts import get as _timeout

REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "application" / "promptwithcontext" / "reports"
CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "application" / "promptwithcontext" / "configuration"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_MAX_CONTEXT_CHARS = 150_000


class WorkflowExecutor:
    """Runs a multi-step YAML workflow, calling a progress callback after each step."""

    def __init__(
        self,
        registry_url: str = "http://127.0.0.1:7002",
        ai_timeout: float | None = None,
        skill_timeout: float | None = None,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        base_dir: Path | None = None,
        aiserver_url: str | None = None,
        worker_url: str | None = None,
    ) -> None:
        self.registry_url = registry_url.rstrip("/")
        self.ai_timeout = ai_timeout if ai_timeout is not None else _timeout("ai_generate")
        self.skill_timeout = skill_timeout if skill_timeout is not None else _timeout("skill_call")
        self.max_context_chars = max_context_chars
        self.base_dir = base_dir or PROJECT_ROOT
        self._aiserver_url = aiserver_url
        self._worker_url = worker_url

    def _get_ai_url(self) -> str:
        if self._aiserver_url:
            return self._aiserver_url
        with http_client("registry") as client:
            r = client.get(f"{self.registry_url}/servers/aiserver")
            r.raise_for_status()
            url = (r.json() or {}).get("url")
            if not url:
                raise ValueError("Registry missing url for aiserver")
            self._aiserver_url = str(url).rstrip("/")
            return self._aiserver_url

    def _get_worker_url(self) -> str:
        if self._worker_url:
            return self._worker_url
        url = find_live_worker(self.registry_url)
        if not url:
            raise ValueError("No live worker found in registry")
        self._worker_url = url.rstrip("/")
        return self._worker_url

    def load_config(self, config_path: Path) -> dict[str, Any]:
        if not config_path.is_file():
            raise FileNotFoundError(f"Config not found: {config_path}")
        text = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("Config must be a YAML object")
        return data

    def resolve_config_path(self, config: str) -> Path:
        p = Path(config)
        if p.is_absolute() and p.is_file():
            return p.resolve()
        if (CONFIG_DIR / p).is_file():
            return (CONFIG_DIR / p).resolve()
        if (CONFIG_DIR / p.name).is_file():
            return (CONFIG_DIR / p.name).resolve()
        return Path(config).resolve()

    def normalize_steps(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        steps_cfg = cfg.get("steps")
        if isinstance(steps_cfg, list) and steps_cfg:
            out: list[dict[str, Any]] = []
            for s in steps_cfg:
                if not isinstance(s, dict):
                    continue
                step = dict(s)
                if step.get("type") not in ("ai", "skill"):
                    step["type"] = "ai"
                out.append(step)
            return out
        prompt = (cfg.get("prompt") or "").strip()
        if not prompt:
            return []
        return [{
            "id": cfg.get("id") or "1",
            "type": "ai",
            "prompt": prompt,
            "profile": (cfg.get("profile") or "agent").strip().lower(),
            "provider": cfg.get("provider"),
            "files": cfg.get("files") if isinstance(cfg.get("files"), list) else [],
        }]

    def run(
        self,
        config_path: Path,
        var_overrides: dict[str, str] | None = None,
        on_step_progress: Callable[[int, str, str, str | None], None] | None = None,
    ) -> WorkflowResult:
        """
        Execute the workflow.

        on_step_progress(step_num, step_id, status, error) is called when a step
        starts, completes, is skipped, or fails.

        Returns WorkflowResult with step outputs, final output, and report path.
        """
        cfg = self.load_config(config_path)

        if var_overrides:
            if "vars" not in cfg or not isinstance(cfg["vars"], dict):
                cfg["vars"] = {}
            cfg["vars"].update(var_overrides)

        steps = self.normalize_steps(cfg)
        if not steps:
            raise ValueError("Config must include 'prompt' or a non-empty 'steps' list")

        run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        outputs: dict[str, str] = {}
        if isinstance(cfg.get("vars"), dict):
            for k, v in cfg["vars"].items():
                if k and isinstance(k, str):
                    outputs[k] = str(v) if v is not None else ""

        previous_output = ""
        step_responses: dict[str, Any] = {}
        step_outputs: list[StepOutput] = []

        for i, step in enumerate(steps):
            step_num = i + 1
            step_id = str(step.get("id") or step_num).strip()
            step_type = (step.get("type") or "ai").strip().lower()

            placeholders = dict(outputs)
            placeholders["previous_output"] = previous_output

            skip_if_empty = step.get("skip_if_previous_empty") is True
            run_when = step.get("run_when")
            if isinstance(run_when, dict):
                run_when = {k: v for k, v in run_when.items() if v is not None}
            else:
                run_when = None

            if skip_if_empty and _is_empty_output(previous_output):
                if on_step_progress:
                    on_step_progress(step_num, step_id, "skipped", "previous output empty")
                so = StepOutput(step_num=step_num, step_id=step_id, status="skipped",
                                skipped_reason="previous output empty")
                step_outputs.append(so)
                outputs[f"step_{step_num}_output"] = previous_output
                outputs[f"{step_id}_output"] = previous_output
                _write_step_report(config_path.stem, run_ts, step_num, step_id, previous_output)
                continue

            if run_when and not _run_when_met(step_responses, run_when):
                if on_step_progress:
                    on_step_progress(step_num, step_id, "skipped", "run_when condition not met")
                so = StepOutput(step_num=step_num, step_id=step_id, status="skipped",
                                skipped_reason="run_when condition not met")
                step_outputs.append(so)
                outputs[f"step_{step_num}_output"] = previous_output
                outputs[f"{step_id}_output"] = previous_output
                _write_step_report(config_path.stem, run_ts, step_num, step_id, previous_output)
                continue

            if on_step_progress:
                on_step_progress(step_num, step_id, "running", None)

            start = time.monotonic()
            text = ""
            error = None

            try:
                if step_type == "skill":
                    text = self._run_skill_step(step, step_id, placeholders, step_responses)
                else:
                    text = self._run_ai_step(step, placeholders)
            except Exception as e:
                error = str(e)
                elapsed = (time.monotonic() - start) * 1000
                if on_step_progress:
                    on_step_progress(step_num, step_id, "failed", error)
                so = StepOutput(step_num=step_num, step_id=step_id, status="failed",
                                error=error, elapsed_ms=elapsed)
                step_outputs.append(so)
                raise WorkflowStepError(step_num, step_id, error) from e

            elapsed = (time.monotonic() - start) * 1000
            previous_output = text
            outputs[f"step_{step_num}_output"] = text
            outputs[f"{step_id}_output"] = text

            _write_step_report(config_path.stem, run_ts, step_num, step_id, text)

            if on_step_progress:
                on_step_progress(step_num, step_id, "completed", None)

            so = StepOutput(step_num=step_num, step_id=step_id, status="completed",
                            output_chars=len(text), elapsed_ms=elapsed)
            step_outputs.append(so)

        report_path = REPORTS_DIR / f"{config_path.stem}_{run_ts}.txt"
        report_path.write_text(previous_output, encoding="utf-8")

        return WorkflowResult(
            final_output=previous_output,
            report_path=str(report_path),
            step_outputs=step_outputs,
        )

    def _run_skill_step(
        self, step: dict[str, Any], step_id: str,
        placeholders: dict[str, str], step_responses: dict[str, Any],
    ) -> str:
        skill_name = (step.get("skill") or "").strip()
        if not skill_name:
            raise ValueError("skill step missing 'skill'")
        endpoint = (step.get("endpoint") or "").strip()
        if not endpoint:
            raise ValueError("skill step missing 'endpoint'")

        params = step.get("params")
        if not isinstance(params, dict):
            params = {}
        params = _apply_placeholders_to_value(params, placeholders)
        output_path = step.get("output_path")
        if output_path is not None:
            output_path = str(output_path).strip() or None

        wurl = self._get_worker_url()

        step_timeout = step.get("timeout")
        if isinstance(step_timeout, (int, float)) and step_timeout > 0:
            timeout = float(step_timeout)
        else:
            timeout = self.skill_timeout
        timeout += 30.0

        path = endpoint.strip().lstrip("/")
        url = f"{wurl}/skills/{skill_name}/{path}"
        with http_client("skill_call", timeout=timeout) as client:
            r = client.post(url, json=params)
            r.raise_for_status()
        response = r.json()
        step_responses[step_id] = response
        return _extract_output_from_response(response, output_path)

    def _run_ai_step(self, step: dict[str, Any], placeholders: dict[str, str]) -> str:
        prompt_cfg = (step.get("prompt") or "").strip()
        if not prompt_cfg:
            raise ValueError("AI step missing 'prompt'")

        profile = (step.get("profile") or "agent").strip().lower()
        provider = step.get("provider")
        if provider is not None:
            provider = str(provider).strip() or None
        files = step.get("files")
        if not isinstance(files, list):
            files = []

        context = _build_context(files, self.base_dir)

        if self.max_context_chars > 0 and len(placeholders.get("previous_output", "")) > self.max_context_chars:
            placeholders = dict(placeholders)
            prev = placeholders["previous_output"]
            placeholders["previous_output"] = prev[:self.max_context_chars] + "\n\n[... truncated ...]"

        prompt_resolved = _apply_placeholders(prompt_cfg, placeholders)
        full_prompt = _build_prompt(prompt_resolved, context)

        ai_url = self._get_ai_url()
        payload: dict[str, Any] = {"prompt": full_prompt, "profile": profile}
        if provider:
            payload["provider"] = provider
        with http_client("ai_generate", timeout=self.ai_timeout) as client:
            r = client.post(f"{ai_url}/generate", json=payload)
            r.raise_for_status()
        data = r.json()
        out = data.get("output")
        if isinstance(out, dict) and "text" in out:
            return str(out["text"]).strip()
        if isinstance(out, str):
            return out.strip()
        return str(data.get("output", data)).strip()


class StepOutput:
    __slots__ = ("step_num", "step_id", "status", "output_chars", "elapsed_ms", "error", "skipped_reason")

    def __init__(self, *, step_num: int, step_id: str, status: str,
                 output_chars: int = 0, elapsed_ms: float = 0,
                 error: str | None = None, skipped_reason: str | None = None):
        self.step_num = step_num
        self.step_id = step_id
        self.status = status
        self.output_chars = output_chars
        self.elapsed_ms = elapsed_ms
        self.error = error
        self.skipped_reason = skipped_reason


class WorkflowResult:
    __slots__ = ("final_output", "report_path", "step_outputs")

    def __init__(self, *, final_output: str, report_path: str, step_outputs: list[StepOutput]):
        self.final_output = final_output
        self.report_path = report_path
        self.step_outputs = step_outputs


class WorkflowStepError(Exception):
    def __init__(self, step_num: int, step_id: str, message: str):
        self.step_num = step_num
        self.step_id = step_id
        super().__init__(f"Step {step_num} ({step_id}) failed: {message}")


def _write_step_report(config_stem: str, run_ts: str, step_num: int, step_id: str, text: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{config_stem}_{run_ts}_step{step_num}_{step_id}.txt"
    path.write_text(text, encoding="utf-8")


def _build_context(files: list[str], base_dir: Path) -> str:
    parts: list[str] = []
    for rel in files:
        if not isinstance(rel, str) or not rel.strip():
            continue
        p = (base_dir / rel.strip()).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Context file not found: {p}")
        content = p.read_text(encoding="utf-8", errors="replace")
        parts.append(f"--- File: {p.name} ---\n{content}")
    return "\n\n".join(parts) if parts else ""


def _build_prompt(user_prompt: str, context: str) -> str:
    if not context.strip():
        return user_prompt.strip()
    return (
        "Use the following context when answering.\n\n"
        "--- Context ---\n"
        f"{context.strip()}\n\n"
        "--- End context ---\n\n"
        f"{user_prompt.strip()}"
    )


def _apply_placeholders(text: str, placeholders: dict[str, str]) -> str:
    for key, value in placeholders.items():
        text = text.replace("{" + key + "}", value)
    return text


def _apply_placeholders_to_value(val: Any, placeholders: dict[str, str]) -> Any:
    if isinstance(val, str):
        return _apply_placeholders(val, placeholders)
    if isinstance(val, dict):
        return {k: _apply_placeholders_to_value(v, placeholders) for k, v in val.items()}
    if isinstance(val, list):
        return [_apply_placeholders_to_value(v, placeholders) for v in val]
    return val


def _extract_output_from_response(response: dict[str, Any], output_path: str | None) -> str:
    if not output_path or not output_path.strip():
        return json.dumps(response, indent=2)
    obj = _get_path(response, output_path.strip())
    if obj is None and isinstance(response, dict) and not output_path.strip().startswith("data."):
        obj = _get_path(response, f"data.{output_path.strip()}")
    if obj is None:
        return json.dumps(response, indent=2)
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, indent=2)


def _is_empty_output(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return True
    if s in ("[]", "{}", "null"):
        return True
    try:
        parsed = json.loads(s)
        if isinstance(parsed, (list, dict)) and len(parsed) == 0:
            return True
    except (json.JSONDecodeError, TypeError):
        pass
    return False


def _get_path(obj: Any, path: str) -> Any:
    for part in path.strip().split("."):
        if part == "":
            continue
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        elif isinstance(obj, list) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(obj):
                obj = obj[idx]
            else:
                return None
        else:
            return None
    return obj


def _run_when_met(step_responses: dict[str, Any], run_when: dict[str, Any]) -> bool:
    step_id = (run_when.get("step") or "").strip()
    path = (run_when.get("path") or "").strip()
    if not step_id or not path:
        return True
    resp = step_responses.get(step_id)
    if resp is None:
        return False
    val = _get_path(resp, path)
    if val is None and isinstance(resp, dict) and not path.startswith("data."):
        val = _get_path(resp, f"data.{path}")
    if "min" in run_when:
        try:
            return (val is not None) and (int(val) >= int(run_when["min"]))
        except (TypeError, ValueError):
            return False
    if run_when.get("not_empty"):
        if isinstance(val, (list, dict)):
            return len(val) > 0
        return bool(val is not None and val != "")
    if "eq" in run_when:
        return val == run_when["eq"]
    return True
