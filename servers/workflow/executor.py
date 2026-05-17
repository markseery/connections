"""
License: MIT
Description: Workflow executor — runs multi-step YAML workflows with per-step progress.
Step types: ai (aiserver /generate), skill (worker HTTP), subprocess (local argv, blocks until exit).
Extracted from run_prompt_with_context.py for use by both the CLI and the workflow server.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import threading
from threading import Lock
from typing import Any

import yaml

from common.compound.aiserver_discovery import get_aiserver_base_url
from common.compound.http_client import http_client
from common.complex.skill_lifecycle import find_live_worker
from common.simple.timeouts import get as _timeout
from common.simple.user_dir import repo_root, user_dir, resolve_workflow, resolve_workflows_dir

REPORTS_DIR = user_dir() / "data" / "reports"
CONFIG_DIR = user_dir() / "config" / "workflows"
REPO_CONFIG_WORKFLOWS_DIR = repo_root() / "config" / "workflows"
WORKFLOWS_DATA_DIR = repo_root() / "data" / "workflows"
PROJECT_ROOT = repo_root()

DEFAULT_MAX_CONTEXT_CHARS = 150_000

# $step.<step_id>.<dotted.json.path> — step_id is alphanumeric / hyphen (and numeric strings).
_STEP_REF_FULL = re.compile(r"^\$step\.([^.\s]+)\.(.+)$")
_STEP_REF_ANY = re.compile(r"\$step\.([^.\s]+)\.([\w.]+)")


def _lookup_step_path(step_responses: dict[str, Any], sid: str, path: str) -> Any:
    root = step_responses.get(sid)
    if root is None:
        return None
    v = _get_path(root, path)
    if v is None and isinstance(root, dict) and not path.startswith("data."):
        v = _get_path(root, f"data.{path}")
    return v


def _substitute_step_refs_in_string(s: str, step_responses: dict[str, Any]) -> str:
    def repl(m: re.Match[str]) -> str:
        v = _lookup_step_path(step_responses, m.group(1), m.group(2))
        return "" if v is None else str(v)

    return _STEP_REF_ANY.sub(repl, s)


def _resolve_value_refs(val: Any, placeholders: dict[str, str], step_responses: dict[str, Any]) -> Any:
    if isinstance(val, str):
        s0 = _apply_placeholders(val, placeholders)
        fm = _STEP_REF_FULL.match(s0.strip())
        if fm:
            return _lookup_step_path(step_responses, fm.group(1), fm.group(2))
        return _substitute_step_refs_in_string(s0, step_responses)
    if isinstance(val, dict):
        return {k: _resolve_value_refs(v, placeholders, step_responses) for k, v in val.items()}
    if isinstance(val, list):
        return [_resolve_value_refs(v, placeholders, step_responses) for v in val]
    return val


def _scratchpad_for_ai_text(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    try:
        parsed = json.loads(t)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return {"text": t}


def _order_steps_by_dependency(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Topological order when depends_on is used; otherwise preserve YAML order."""
    if not steps:
        return steps
    has_deps = any(isinstance(s.get("depends_on"), list) and len(s["depends_on"]) > 0 for s in steps)
    if not has_deps:
        return steps

    ids: list[str] = []
    for i, s in enumerate(steps):
        sid = str(s.get("id") or (i + 1)).strip()
        ids.append(sid)

    if len(ids) != len(set(ids)):
        raise ValueError("duplicate step id; each id must be unique when using depends_on")

    id_set = set(ids)
    prereq: dict[str, set[str]] = {}
    orig_idx: dict[str, int] = {}
    for i, s in enumerate(steps):
        sid = ids[i]
        orig_idx[sid] = i
        deps: set[str] = set()
        for d in s.get("depends_on") or []:
            deps.add(str(d).strip())
        unknown = deps - id_set
        if unknown:
            raise ValueError(f"Step {sid!r} depends_on unknown step ids: {sorted(unknown)}")
        prereq[sid] = deps

    children: dict[str, list[str]] = {sid: [] for sid in id_set}
    in_degree: dict[str, int] = {}
    for sid in id_set:
        in_degree[sid] = len(prereq.get(sid, set()))
        for d in prereq.get(sid, set()):
            if d in children:
                children[d].append(sid)

    queue = [sid for sid in id_set if in_degree[sid] == 0]
    queue.sort(key=lambda x: orig_idx[x])
    ordered_ids: list[str] = []
    while queue:
        u = queue.pop(0)
        ordered_ids.append(u)
        for v in sorted(children[u], key=lambda x: orig_idx[x]):
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)
                queue.sort(key=lambda x: orig_idx[x])

    if len(ordered_ids) != len(id_set):
        raise ValueError("Workflow steps contain a cyclic depends_on graph")

    id_to_step = {ids[i]: steps[i] for i in range(len(steps))}
    return [id_to_step[sid] for sid in ordered_ids]


class WorkflowExecutor:
    """Runs a multi-step YAML workflow, calling a progress callback after each step."""

    def __init__(
        self,
        registry_url: str = "http://127.0.0.1:7002",
        ai_timeout: float | None = None,
        skill_timeout: float | None = None,
        subprocess_timeout: float | None = None,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        base_dir: Path | None = None,
        aiserver_url: str | None = None,
        worker_url: str | None = None,
    ) -> None:
        self.registry_url = registry_url.rstrip("/")
        self.ai_timeout = ai_timeout if ai_timeout is not None else _timeout("ai_generate")
        self.skill_timeout = skill_timeout if skill_timeout is not None else _timeout("skill_call")
        self.subprocess_timeout = (
            subprocess_timeout if subprocess_timeout is not None else _timeout("workflow_subprocess")
        )
        self.max_context_chars = max_context_chars
        self.base_dir = base_dir or PROJECT_ROOT
        self._aiserver_url = aiserver_url
        self._worker_url = worker_url
        self._active_configs: threading.local = threading.local()

    def _get_ai_url(self) -> str:
        if self._aiserver_url:
            return self._aiserver_url
        self._aiserver_url = get_aiserver_base_url(
            registry_override=self.registry_url.strip().rstrip("/"),
        )
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
        # Check user workflows directory first
        user_wf = resolve_workflow(p.name)
        if user_wf:
            return user_wf.resolve()
        if (CONFIG_DIR / p).is_file():
            return (CONFIG_DIR / p).resolve()
        if (CONFIG_DIR / p.name).is_file():
            return (CONFIG_DIR / p.name).resolve()
        if (REPO_CONFIG_WORKFLOWS_DIR / p).is_file():
            return (REPO_CONFIG_WORKFLOWS_DIR / p).resolve()
        if (REPO_CONFIG_WORKFLOWS_DIR / p.name).is_file():
            return (REPO_CONFIG_WORKFLOWS_DIR / p.name).resolve()
        if (WORKFLOWS_DATA_DIR / p).is_file():
            return (WORKFLOWS_DATA_DIR / p).resolve()
        if (WORKFLOWS_DATA_DIR / p.name).is_file():
            return (WORKFLOWS_DATA_DIR / p.name).resolve()
        return Path(config).resolve()

    def normalize_steps(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        steps_cfg = cfg.get("steps")
        if isinstance(steps_cfg, list) and steps_cfg:
            out: list[dict[str, Any]] = []
            for s in steps_cfg:
                if not isinstance(s, dict):
                    continue
                step = dict(s)
                if step.get("type") not in ("ai", "skill", "subprocess", "workflow"):
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
        on_subprocess_output: Callable[[int, str, str], None] | None = None,
    ) -> WorkflowResult:
        """
        Execute the workflow.

        on_step_progress(step_num, step_id, status, error) is called when a step
        starts, completes, is skipped, or fails.

        on_subprocess_output(step_num, step_id, line) is called for each line of
        stdout/stderr produced by a subprocess step while it runs.

        Returns WorkflowResult with step outputs, final output, and report path.
        """
        resolved = config_path.resolve()
        active = getattr(self._active_configs, "paths", None)
        if active is None:
            active = set()
            self._active_configs.paths = active
        if resolved in active:
            raise ValueError(f"Recursive workflow detected: {config_path.name} is already running")
        active.add(resolved)

        try:
            return self._run_inner(config_path, var_overrides, on_step_progress, on_subprocess_output)
        finally:
            active.discard(resolved)

    def _run_inner(
        self,
        config_path: Path,
        var_overrides: dict[str, str] | None = None,
        on_step_progress: Callable[[int, str, str, str | None], None] | None = None,
        on_subprocess_output: Callable[[int, str, str], None] | None = None,
    ) -> WorkflowResult:
        cfg = self.load_config(config_path)

        if var_overrides:
            if "vars" not in cfg or not isinstance(cfg["vars"], dict):
                cfg["vars"] = {}
            cfg["vars"].update(var_overrides)

        steps = self.normalize_steps(cfg)
        if not steps:
            raise ValueError("Config must include 'prompt' or a non-empty 'steps' list")

        try:
            steps = _order_steps_by_dependency(steps)
        except ValueError as e:
            raise ValueError(f"Invalid workflow step ordering: {e}") from e

        run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        parallel = cfg.get("parallel") is True

        outputs: dict[str, str] = {}
        if isinstance(cfg.get("vars"), dict):
            for k, v in cfg["vars"].items():
                if k and isinstance(k, str):
                    outputs[k] = str(v) if v is not None else ""

        if parallel:
            return self._run_parallel(
                steps, outputs, config_path, run_ts,
                on_step_progress, on_subprocess_output,
            )

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
                elif step_type == "subprocess":
                    text = self._run_subprocess_step(
                        step, step_id, placeholders, step_responses,
                        on_output=lambda line, _sn=step_num, _si=step_id: (
                            on_subprocess_output(_sn, _si, line) if on_subprocess_output else None
                        ),
                    )
                elif step_type == "workflow":
                    text = self._run_workflow_step(
                        step, step_id, placeholders, step_responses,
                        on_step_progress=on_step_progress,
                        on_subprocess_output=on_subprocess_output,
                    )
                else:
                    text = self._run_ai_step(step, placeholders, step_responses)
                    step_responses[step_id] = _scratchpad_for_ai_text(text)
            except Exception as e:
                error = str(e)
                elapsed = (time.monotonic() - start) * 1000
                if on_step_progress:
                    on_step_progress(step_num, step_id, "failed", error)
                so = StepOutput(step_num=step_num, step_id=step_id, status="failed",
                                error=error, elapsed_ms=elapsed)
                step_outputs.append(so)
                if step.get("continue_on_error") is True:
                    step_responses[step_id] = {"_workflow_error": error, "_failed": True}
                    text = f"[step {step_id} failed; continuing]\n{error}"
                    previous_output = text
                    outputs[f"step_{step_num}_output"] = text
                    outputs[f"{step_id}_output"] = text
                    _write_step_report(config_path.stem, run_ts, step_num, step_id, text)
                    continue
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

    def _run_parallel(
        self,
        steps: list[dict[str, Any]],
        outputs: dict[str, str],
        config_path: Path,
        run_ts: str,
        on_step_progress: Callable[[int, str, str, str | None], None] | None = None,
        on_subprocess_output: Callable[[int, str, str], None] | None = None,
    ) -> WorkflowResult:
        """Run all steps concurrently using a thread pool."""
        step_responses: dict[str, Any] = {}
        step_outputs: list[StepOutput] = []
        lock = Lock()
        first_error: list[Exception | None] = [None]

        def _run_one(i: int, step: dict[str, Any]) -> StepOutput:
            step_num = i + 1
            step_id = str(step.get("id") or step_num).strip()
            step_type = (step.get("type") or "ai").strip().lower()
            placeholders = dict(outputs)
            placeholders["previous_output"] = ""

            if on_step_progress:
                on_step_progress(step_num, step_id, "running", None)

            start = time.monotonic()
            try:
                if step_type == "skill":
                    text = self._run_skill_step(step, step_id, placeholders, step_responses)
                elif step_type == "subprocess":
                    text = self._run_subprocess_step(
                        step, step_id, placeholders, step_responses,
                        on_output=lambda line, _sn=step_num, _si=step_id: (
                            on_subprocess_output(_sn, _si, line) if on_subprocess_output else None
                        ),
                    )
                elif step_type == "workflow":
                    text = self._run_workflow_step(
                        step, step_id, placeholders, step_responses,
                        on_step_progress=on_step_progress,
                        on_subprocess_output=on_subprocess_output,
                    )
                else:
                    text = self._run_ai_step(step, placeholders, step_responses)
            except Exception as e:
                elapsed = (time.monotonic() - start) * 1000
                if on_step_progress:
                    on_step_progress(step_num, step_id, "failed", str(e))
                with lock:
                    if first_error[0] is None and step.get("continue_on_error") is not True:
                        first_error[0] = WorkflowStepError(step_num, step_id, str(e))
                _write_step_report(config_path.stem, run_ts, step_num, step_id, str(e))
                return StepOutput(step_num=step_num, step_id=step_id, status="failed",
                                  error=str(e), elapsed_ms=elapsed)

            elapsed = (time.monotonic() - start) * 1000
            with lock:
                step_responses[step_id] = {"stdout": text, "exit_code": 0}
            _write_step_report(config_path.stem, run_ts, step_num, step_id, text)

            if on_step_progress:
                on_step_progress(step_num, step_id, "completed", None)

            return StepOutput(step_num=step_num, step_id=step_id, status="completed",
                              output_chars=len(text), elapsed_ms=elapsed)

        max_workers = min(len(steps), 8)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run_one, i, step): i for i, step in enumerate(steps)}
            results: dict[int, StepOutput] = {}
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()

        step_outputs = [results[i] for i in sorted(results)]

        last_completed = next(
            (so for so in reversed(step_outputs) if so.status == "completed"), None
        )
        final_output = ""
        if last_completed:
            key = f"{last_completed.step_id}_output"
            final_output = outputs.get(key, "")

        report_path = REPORTS_DIR / f"{config_path.stem}_{run_ts}.txt"
        report_path.write_text(final_output, encoding="utf-8")

        if first_error[0] is not None:
            raise first_error[0]

        return WorkflowResult(
            final_output=final_output,
            report_path=str(report_path),
            step_outputs=step_outputs,
        )

    def _run_skill_step(
        self, step: dict[str, Any], step_id: str,
        placeholders: dict[str, str], step_responses: dict[str, Any],
    ) -> str:
        wurl = self._get_worker_url().rstrip("/")
        skill_name = (step.get("skill") or "").strip()
        route_tmpl = (step.get("route") or "").strip()
        endpoint = (step.get("endpoint") or "").strip()

        if route_tmpl:
            resolved_route = _substitute_step_refs_in_string(
                _apply_placeholders(route_tmpl, placeholders), step_responses
            )
            if not resolved_route.startswith("/"):
                raise ValueError("skill step 'route' must start with / (path on worker)")
            url = f"{wurl}{resolved_route}"
        else:
            if not skill_name:
                raise ValueError("skill step missing 'skill' (or use full worker 'route')")
            if not endpoint:
                raise ValueError("skill step missing 'endpoint' (or use full worker 'route')")
            ep = _substitute_step_refs_in_string(
                _apply_placeholders(endpoint.strip().lstrip("/"), placeholders), step_responses
            )
            url = f"{wurl}/skills/{skill_name}/{ep}"

        params = step.get("params") if isinstance(step.get("params"), dict) else step.get("args")
        if not isinstance(params, dict):
            params = {}
        params = _resolve_value_refs(params, placeholders, step_responses)

        output_path = step.get("output_path")
        if output_path is not None:
            output_path = str(output_path).strip() or None

        step_timeout = step.get("timeout")
        if isinstance(step_timeout, (int, float)) and step_timeout > 0:
            timeout = float(step_timeout)
        else:
            timeout = self.skill_timeout
        deadline = time.monotonic() + timeout + 30.0
        per_req = min(timeout + 30.0, 600.0)

        method = str(step.get("method", "POST")).upper()
        with http_client("skill_call", timeout=per_req) as client:
            if method == "GET":
                r = client.get(url, params=params or None)
            else:
                r = client.request(method, url, json=params if params else None)
            r.raise_for_status()
        try:
            response: Any = r.json()
        except Exception as exc:
            raise RuntimeError(f"skill step expected JSON from {url}: {exc}") from exc
        if not isinstance(response, dict):
            response = {"data": response}

        step_responses[step_id] = response

        poll = step.get("poll")
        if isinstance(poll, dict) and (poll.get("route") or "").strip():
            poll_route = _substitute_step_refs_in_string(
                _apply_placeholders(str(poll["route"]).strip(), placeholders), step_responses
            )
            if not poll_route.startswith("/"):
                raise ValueError("skill poll.route must start with /")
            poll_url = f"{wurl}{poll_route}"
            poll_method = str(poll.get("method", "GET")).upper()
            field = str(poll.get("field", "status"))
            target = str(poll.get("target", "completed"))
            interval = float(poll.get("interval", 2))
            max_attempts = int(poll.get("max_attempts", 60))
            poll_req_timeout = float(poll.get("request_timeout", 30))
            response = self._poll_skill_until(
                poll_url,
                poll_method,
                field,
                target,
                interval,
                max_attempts,
                deadline,
                poll_req_timeout,
            )
            step_responses[step_id] = response

        return _extract_output_from_response(response, output_path)

    def _poll_skill_until(
        self,
        poll_url: str,
        poll_method: str,
        field: str,
        target: str,
        interval: float,
        max_attempts: int,
        deadline: float,
        per_request_timeout: float,
    ) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for _ in range(1, max_attempts + 1):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"skill poll exceeded step timeout before {field}=={target!r} ({poll_url})"
                )
            time.sleep(interval)
            with http_client("skill_call", timeout=per_request_timeout + 10.0) as client:
                r = client.request(poll_method, poll_url)
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except Exception:
                continue
            last = data if isinstance(data, dict) else {"_raw": data}
            cur = _get_path(last, field) if "." in field else last.get(field)
            if str(cur) == target:
                return last
            if str(cur) == "failed":
                raise RuntimeError(f"skill poll reported failed status ({poll_url})")
        raise RuntimeError(
            f"skill poll exhausted {max_attempts} attempts without {field}=={target!r} ({poll_url})"
        )

    def _run_ai_step(
        self, step: dict[str, Any], placeholders: dict[str, str], step_responses: dict[str, Any],
    ) -> str:
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

        files_resolved: list[str] = []
        for f in files:
            if not isinstance(f, str) or not f.strip():
                continue
            fr = _substitute_step_refs_in_string(_apply_placeholders(f.strip(), placeholders), step_responses)
            files_resolved.append(fr)

        context = _build_context(files_resolved, self.base_dir)

        if self.max_context_chars > 0 and len(placeholders.get("previous_output", "")) > self.max_context_chars:
            placeholders = dict(placeholders)
            prev = placeholders["previous_output"]
            placeholders["previous_output"] = prev[:self.max_context_chars] + "\n\n[... truncated ...]"

        prompt_resolved = _substitute_step_refs_in_string(
            _apply_placeholders(prompt_cfg, placeholders), step_responses
        )
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

    def _run_subprocess_step(
        self,
        step: dict[str, Any],
        step_id: str,
        placeholders: dict[str, str],
        step_responses: dict[str, Any],
        on_output: Callable[[str], None] | None = None,
    ) -> str:
        """Run argv until the process exits; stdout+stderr become step output (and previous_output).

        When *on_output* is provided, each line of combined stdout/stderr is
        passed to the callback as it arrives (live streaming).
        """
        raw_cmd = step.get("command")
        if raw_cmd is None and isinstance(step.get("argv"), list):
            raw_cmd = step["argv"]
        if raw_cmd is None:
            raise ValueError("subprocess step missing 'command' (or 'argv' list)")

        argv = _parse_subprocess_command(raw_cmd)
        if not argv:
            raise ValueError("subprocess step: empty command after parsing")

        argv_resolved: list[str] = []
        for a in argv:
            s = _apply_placeholders(str(a), placeholders)
            m = _STEP_REF_FULL.match(s.strip())
            if m:
                v = _lookup_step_path(step_responses, m.group(1), m.group(2))
                argv_resolved.append("" if v is None else str(v))
            else:
                argv_resolved.append(_substitute_step_refs_in_string(s, step_responses))
        argv = argv_resolved

        _user = user_dir()
        for i, arg in enumerate(argv):
            if not arg or arg.startswith("-"):
                continue
            repo_candidate = self.base_dir / arg
            if not repo_candidate.is_file():
                user_candidate = _user / arg
                if user_candidate.is_file():
                    argv[i] = str(user_candidate)

        cwd_raw = step.get("cwd")
        if cwd_raw is not None and str(cwd_raw).strip():
            cwd_s = _substitute_step_refs_in_string(
                _apply_placeholders(str(cwd_raw).strip(), placeholders), step_responses
            )
            cwd = (self.base_dir / cwd_s).resolve()
            if not cwd.is_dir():
                raise FileNotFoundError(f"subprocess cwd is not a directory: {cwd}")
        else:
            cwd = self.base_dir.resolve()

        env: dict[str, str] | None = None
        step_env = step.get("env")
        if isinstance(step_env, dict) and step_env:
            env = {str(k): str(v) for k, v in os.environ.items()}
            for k, v in step_env.items():
                if k is None:
                    continue
                key = _substitute_step_refs_in_string(_apply_placeholders(str(k), placeholders), step_responses)
                if v is None:
                    env.pop(key, None)
                else:
                    env[key] = _substitute_step_refs_in_string(
                        _apply_placeholders(str(v), placeholders), step_responses
                    )

        step_timeout = step.get("timeout")
        if isinstance(step_timeout, (int, float)) and step_timeout > 0:
            timeout = float(step_timeout)
        else:
            timeout = float(self.subprocess_timeout)

        env_with_unbuffered = dict(env) if env else dict(os.environ)
        env_with_unbuffered["PYTHONUNBUFFERED"] = "1"

        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env_with_unbuffered,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as e:
            raise RuntimeError(f"Failed to start subprocess: {e}") from e

        collected: list[str] = []
        deadline = time.monotonic() + timeout

        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                collected.append(line)
                if on_output:
                    on_output(line.rstrip("\n"))
                if time.monotonic() > deadline:
                    proc.kill()
                    proc.wait()
                    raise subprocess.TimeoutExpired(argv, timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"subprocess timed out after {timeout}s (cmd: {argv[0]!r}, {len(argv)} args)"
            )
        finally:
            proc.wait()

        stdout_text = "".join(collected)
        returncode = proc.returncode

        text = f"exit_code: {returncode}\n\n{stdout_text}"

        step_responses[step_id] = {
            "exit_code": returncode,
            "stdout": stdout_text,
            "stderr": "",
        }

        if returncode != 0:
            tail = (text[-4000:] if len(text) > 4000 else text).strip()
            raise RuntimeError(
                f"subprocess exited with code {returncode}"
                + (f"\n{tail}" if tail else "")
            )

        return text.strip()

    def _run_workflow_step(
        self,
        step: dict[str, Any],
        step_id: str,
        placeholders: dict[str, str],
        step_responses: dict[str, Any],
        on_step_progress: Callable[[int, str, str, str | None], None] | None = None,
        on_subprocess_output: Callable[[int, str, str], None] | None = None,
    ) -> str:
        config_ref = (step.get("config") or "").strip()
        if not config_ref:
            raise ValueError("workflow step missing 'config'")
        config_ref = _substitute_step_refs_in_string(
            _apply_placeholders(config_ref, placeholders), step_responses
        )
        child_path = self.resolve_config_path(config_ref)

        child_vars: dict[str, str] = {}
        raw_vars = step.get("vars")
        if isinstance(raw_vars, dict):
            resolved = _resolve_value_refs(raw_vars, placeholders, step_responses)
            child_vars = {str(k): str(v) for k, v in resolved.items()}

        def _child_subprocess_output(child_step_num: int, child_step_id: str, line: str) -> None:
            if on_subprocess_output:
                on_subprocess_output(child_step_num, f"{step_id}/{child_step_id}", line)

        def _child_step_progress(child_step_num: int, child_step_id: str, status: str, error: str | None) -> None:
            if on_subprocess_output:
                prefix = f"{step_id}/{child_step_id}"
                if status == "running":
                    on_subprocess_output(child_step_num, prefix, f"[child step {child_step_id} started]")
                elif status == "completed":
                    on_subprocess_output(child_step_num, prefix, f"[child step {child_step_id} completed]")
                elif status == "failed":
                    msg = f"[child step {child_step_id} failed: {error}]" if error else f"[child step {child_step_id} failed]"
                    on_subprocess_output(child_step_num, prefix, msg)

        result = self.run(
            child_path,
            var_overrides=child_vars,
            on_step_progress=_child_step_progress,
            on_subprocess_output=_child_subprocess_output,
        )

        response: dict[str, Any] = {
            "final_output": result.final_output,
            "report_path": result.report_path,
            "steps": [
                {"step_id": so.step_id, "status": so.status, "elapsed_ms": so.elapsed_ms}
                for so in result.step_outputs
            ],
        }
        step_responses[step_id] = response

        output_path = step.get("output_path")
        if output_path:
            return _extract_output_from_response(response, str(output_path).strip())
        return result.final_output


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


def _parse_subprocess_command(raw: Any) -> list[str]:
    """Build argv from YAML: list of strings, or one string split with shlex (POSIX-aware on Unix)."""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        return shlex.split(s, posix=os.name != "nt")
    raise ValueError("subprocess 'command' must be a string or list of strings")


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
