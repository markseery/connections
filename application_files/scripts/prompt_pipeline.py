#!/usr/bin/env python3
"""
Run a multi-step AI prompt pipeline defined in a YAML configuration.

Supports two step types:
  - ai:    send a prompt (optionally with file context) to the AI server.
  - skill: call a skill endpoint on a worker server.

Placeholders in prompts and skill params:
  {previous_output}, {step_1_output}, {step_2_output}, ... {<id>_output}
  Plus any vars defined in the YAML or overridden via -V.

Conditional execution (optional per step):
  - skip_if_previous_empty: true  -- skip when previous_output is empty.
  - run_when: { step: "<id>", path: "field.path", min: N }
    Also: not_empty: true, or eq: value.

Modes:
  Local (default):  executes steps in-process.
  Async (--async):  submits the YAML to the workflow server for background
                    execution and polls for results.

Configuration files:  application_files/config/prompts/*.yaml
Reports (output):     application_files/data/reports/prompts/

Usage:
  python3 prompt_pipeline.py cloud_services_notify_multistep.yaml
  python3 prompt_pipeline.py example.yaml -V list_name=ai-news
  python3 prompt_pipeline.py cloud_services_notify_multistep.yaml --async
  python3 prompt_pipeline.py --resume JOB_ID
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = APP_ROOT / "config" / "prompts"
REPORTS_DIR = APP_ROOT / "data" / "reports" / "prompts"

DEFAULT_REGISTRY_URL = os.environ.get(
    "REGISTRY_SERVER_URL", "http://127.0.0.1:7002"
).rstrip("/")

DEFAULT_AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT", "660"))
DEFAULT_SKILL_TIMEOUT = float(os.environ.get("SKILL_TIMEOUT", "300"))
DEFAULT_MAX_CONTEXT_CHARS = 150_000


# ── service discovery ────────────────────────────────────────────────────

def _get_aiserver_url(registry_url: str) -> str:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{registry_url}/servers/aiserver")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError("Registry missing url for aiserver")
        return str(url).rstrip("/")


def _find_worker(registry_url: str) -> str:
    try:
        r = httpx.get(f"{registry_url}/servers", timeout=5.0)
        r.raise_for_status()
        servers = r.json().get("servers") or []
    except Exception:
        servers = []

    candidates: list[tuple[str, int]] = []
    for srv in servers:
        name = srv.get("name", "")
        if not name.startswith("worker"):
            continue
        url = srv.get("url", "").rstrip("/")
        if not url:
            continue
        try:
            hr = httpx.get(f"{url}/health", timeout=3.0)
            if hr.status_code != 200:
                continue
            active = hr.json().get("active_requests", 0)
            candidates.append((url, active))
        except Exception:
            continue

    if not candidates:
        raise ValueError("No healthy worker found via registry")

    idle = [u for u, a in candidates if a == 0]
    if idle:
        return random.choice(idle)
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


def _get_workflow_url(registry_url: str) -> str:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{registry_url}/servers/workflow")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError("Registry missing url for workflow server")
        return str(url).rstrip("/")


# ── config loading ───────────────────────────────────────────────────────

def _resolve_config(name: str) -> Path:
    p = Path(name)
    if p.is_absolute() and p.is_file():
        return p.resolve()
    if (PROMPTS_DIR / p).is_file():
        return (PROMPTS_DIR / p).resolve()
    if (PROMPTS_DIR / p.name).is_file():
        return (PROMPTS_DIR / p.name).resolve()
    return p.resolve()


def _load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        available = sorted(p.stem for p in PROMPTS_DIR.glob("*.yaml")) if PROMPTS_DIR.is_dir() else []
        raise FileNotFoundError(
            f"Config not found: {path}\n"
            f"Available: {', '.join(available) or 'none'}"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Config must be a YAML mapping")
    return data


def _normalize_steps(cfg: dict[str, Any]) -> list[dict[str, Any]]:
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


# ── AI and skill calls ──────────────────────────────────────────────────

def _call_ai(
    aiserver_url: str,
    prompt: str,
    profile: str,
    provider: str | None = None,
    timeout: float = 660.0,
) -> str:
    payload: dict[str, Any] = {"prompt": prompt, "profile": profile}
    if provider:
        payload["provider"] = provider
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{aiserver_url}/generate", json=payload)
        r.raise_for_status()
    out = r.json()
    output = out.get("output")
    if isinstance(output, dict) and "text" in output:
        return str(output["text"]).strip()
    if isinstance(output, str):
        return output.strip()
    return str(out.get("output", out)).strip()


def _call_skill(
    worker_url: str,
    skill_name: str,
    endpoint: str,
    params: dict[str, Any],
    timeout: float = 300.0,
) -> dict[str, Any]:
    path = endpoint.strip().lstrip("/")
    url = f"{worker_url}/skills/{skill_name}/{path}"
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=params)
        r.raise_for_status()
    data = r.json()

    poll_path = data.get("poll") if isinstance(data, dict) else None
    if poll_path and isinstance(poll_path, str) and data.get("job_id"):
        poll_url = f"{worker_url}{poll_path}"
        print(f"    Skill returned job {data['job_id']}, polling ...", file=sys.stderr)
        data = _poll_skill_job(poll_url)

    return data


def _poll_skill_job(poll_url: str, interval: float = 5.0) -> dict[str, Any]:
    """Poll a skill job endpoint until it reports completed or failed. No timeout."""
    while True:
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.get(poll_url)
                r.raise_for_status()
            data = r.json()
        except Exception:
            time.sleep(interval)
            continue
        status = data.get("status", "unknown")
        if status == "completed":
            return data.get("result", data)
        if status == "failed":
            error = data.get("error", "unknown error")
            raise RuntimeError(f"Skill job failed: {error}")
        time.sleep(interval)


# ── prompt / context helpers ─────────────────────────────────────────────

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


def _apply_placeholders(text: str, ph: dict[str, str]) -> str:
    for key, value in ph.items():
        text = text.replace("{" + key + "}", value)
    return text


def _apply_placeholders_deep(val: Any, ph: dict[str, str]) -> Any:
    if isinstance(val, str):
        return _apply_placeholders(val, ph)
    if isinstance(val, dict):
        return {k: _apply_placeholders_deep(v, ph) for k, v in val.items()}
    if isinstance(val, list):
        return [_apply_placeholders_deep(v, ph) for v in val]
    return val


def _extract_output(response: dict[str, Any], output_path: str | None) -> str:
    if not output_path or not output_path.strip():
        return json.dumps(response, indent=2)
    obj: Any = response
    for part in output_path.strip().split("."):
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            return json.dumps(response, indent=2)
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, indent=2)


# ── conditional helpers ──────────────────────────────────────────────────

def _is_empty_output(text: str) -> bool:
    s = (text or "").strip()
    if not s or s in ("[]", "{}", "null"):
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
        if not part:
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
    if "min" in run_when:
        try:
            return val is not None and int(val) >= int(run_when["min"])
        except (TypeError, ValueError):
            return False
    if run_when.get("not_empty"):
        if isinstance(val, (list, dict)):
            return len(val) > 0
        return val is not None and val != ""
    if "eq" in run_when:
        return val == run_when["eq"]
    return True


def _format_http_error(e: Exception) -> str:
    msg = str(e)
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            body = resp.text
            if body and len(body) < 2000:
                try:
                    j = json.loads(body)
                    if isinstance(j, dict) and "detail" in j:
                        msg = f"{msg}; detail: {j['detail']}"
                    else:
                        msg = f"{msg}; response: {body}"
                except (ValueError, TypeError):
                    msg = f"{msg}; response: {body[:500]}"
        except Exception:
            pass
    return msg


# ── async mode (workflow server) ─────────────────────────────────────────

def _run_async(args: argparse.Namespace) -> int:
    registry_url = args.registry.rstrip("/")

    try:
        wf_url = args.workflow_url or _get_workflow_url(registry_url)
    except Exception as e:
        print(f"Workflow server discovery failed: {e}", file=sys.stderr)
        return 1

    job_id = args.resume

    if not job_id:
        var_overrides: dict[str, str] = {}
        for s in (args.var_overrides or []):
            if "=" in s:
                k, v = s.split("=", 1)
                var_overrides[k.strip()] = v.strip()

        config_name = args.config
        config_path = _resolve_config(config_name)
        if not config_path.is_file():
            print(f"Config not found: {config_path}", file=sys.stderr)
            return 1

        payload: dict[str, Any] = {
            "config": str(config_path),
            "vars": var_overrides,
            "skill_timeout": args.skill_timeout,
            "ai_timeout": args.ai_timeout,
            "max_context_chars": args.max_context_chars,
        }

        print(f"Submitting to {wf_url}/workflows/submit ...", file=sys.stderr)
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.post(f"{wf_url}/workflows/submit", json=payload)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as e:
            print(f"Submit failed: {_format_http_error(e)}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"Submit failed: {e}", file=sys.stderr)
            return 1

        job_id = data.get("job_id", "")
        print(f"Job submitted: {job_id}", file=sys.stderr)
        print(f"  Config: {data.get('config', config_name)}", file=sys.stderr)
        print(f"  Steps:  {data.get('total_steps', '?')}", file=sys.stderr)
        print(f"  Poll:   {wf_url}/workflows/jobs/{job_id}", file=sys.stderr)
    else:
        print(f"Resuming job: {job_id}", file=sys.stderr)

    print("\nPolling ...", file=sys.stderr)
    announced: set[str] = set()
    poll_start = time.monotonic()
    poll_interval = 2.0

    while True:
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{wf_url}/workflows/jobs/{job_id}")
                r.raise_for_status()
                job = r.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                print(f"Job not found: {job_id}", file=sys.stderr)
                return 1
            time.sleep(poll_interval)
            continue
        except Exception:
            time.sleep(poll_interval)
            continue

        status = job.get("status", "unknown")
        total = job.get("total_steps", 0)
        wall = time.monotonic() - poll_start

        for sp in job.get("step_progress", []):
            sp_status = sp.get("status", "pending")
            sp_id = sp.get("step_id", "?")
            sp_num = sp.get("step_num", "?")
            sp_skill = sp.get("skill_name", "")
            key = f"{sp_id}:{sp_status}"

            if sp_status == "running":
                started = sp.get("started_at")
                if started and isinstance(started, str):
                    try:
                        st = datetime.fromisoformat(started.replace("Z", "+00:00"))
                        elapsed_s = (datetime.now(timezone.utc) - st).total_seconds()
                    except Exception:
                        elapsed_s = wall
                else:
                    elapsed_s = wall
                name = sp_skill or sp_id
                print(f"  [{sp_num}/{total}] {name}: running ({elapsed_s:.0f}s)", file=sys.stderr)

                for line in sp.get("log_tail", []):
                    if f"{sp_id}|{line}" not in announced:
                        announced.add(f"{sp_id}|{line}")
                        print(f"    {sp_id} | {line}", file=sys.stderr)

            elif key not in announced:
                announced.add(key)
                elapsed = sp.get("elapsed_ms", 0)
                if sp_status == "completed":
                    print(f"  [{sp_num}/{total}] {sp_id}: completed ({elapsed / 1000:.1f}s)", file=sys.stderr)
                elif sp_status == "failed":
                    print(f"  [{sp_num}/{total}] {sp_id}: FAILED — {sp.get('error', '?')}", file=sys.stderr)
                elif sp_status == "skipped":
                    print(f"  [{sp_num}/{total}] {sp_id}: skipped ({sp.get('skipped_reason', '')})", file=sys.stderr)

        if status == "completed":
            final = job.get("final_output", "")
            report = job.get("report_path", "")
            print(f"\nCompleted in {wall:.0f}s. Report: {report}", file=sys.stderr)
            if final:
                print(final)
            return 0

        if status == "failed":
            print(f"\nFailed after {wall:.0f}s: {job.get('error', '')}", file=sys.stderr)
            return 1

        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.2, 15.0)


# ── local execution ──────────────────────────────────────────────────────

def _run_local(args: argparse.Namespace) -> int:
    config_path = _resolve_config(args.config)
    registry_url = args.registry.rstrip("/")

    repo_root = Path(__file__).resolve().parents[2]
    base_dir = Path(args.base_dir).resolve() if args.base_dir else repo_root

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    print("Processing started.", file=sys.stderr)
    print(f"  Config: {config_path}", file=sys.stderr)

    try:
        cfg = _load_config(config_path)
    except Exception as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    if args.var_overrides:
        if "vars" not in cfg or not isinstance(cfg["vars"], dict):
            cfg["vars"] = {}
        for s in args.var_overrides:
            if "=" not in s:
                print(f"  Ignoring malformed -V (missing '='): {s!r}", file=sys.stderr)
                continue
            key, value = s.split("=", 1)
            key, value = key.strip(), value.strip()
            if key:
                cfg["vars"][key] = value
                print(f"  Var override: {key}={value!r}", file=sys.stderr)

    steps = _normalize_steps(cfg)
    if not steps:
        print("Config must include 'prompt' or a non-empty 'steps' list.", file=sys.stderr)
        return 1

    ai_url: str | None = None
    worker_url: str | None = None

    def _get_ai() -> str:
        nonlocal ai_url
        if ai_url is None:
            ai_url = args.aiserver_url or _get_aiserver_url(registry_url)
        return ai_url

    def _get_worker() -> str:
        nonlocal worker_url
        if worker_url is None:
            worker_url = (args.worker_url or _find_worker(registry_url)).rstrip("/")
        return worker_url

    try:
        _get_ai()
    except Exception as e:
        print(f"AI server discovery failed: {e}", file=sys.stderr)
        return 1

    print(f"  AI server: {ai_url}", file=sys.stderr)
    print(f"  Steps: {len(steps)}", file=sys.stderr)

    outputs: dict[str, str] = {}
    if isinstance(cfg.get("vars"), dict):
        for k, v in cfg["vars"].items():
            if k and isinstance(k, str):
                outputs[k] = str(v) if v is not None else ""
    previous_output = ""
    step_responses: dict[str, Any] = {}

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
            print(f"  Step {step_num} ({step_id}): skipped (previous output empty).", file=sys.stderr)
            outputs[f"step_{step_num}_output"] = previous_output
            outputs[f"{step_id}_output"] = previous_output
            _save_step_report(config_path.stem, run_ts, step_num, step_id, previous_output)
            continue

        if run_when and not _run_when_met(step_responses, run_when):
            print(f"  Step {step_num} ({step_id}): skipped (run_when not met).", file=sys.stderr)
            outputs[f"step_{step_num}_output"] = previous_output
            outputs[f"{step_id}_output"] = previous_output
            _save_step_report(config_path.stem, run_ts, step_num, step_id, previous_output)
            continue

        t0 = time.monotonic()

        if step_type == "skill":
            skill_name = (step.get("skill") or "").strip()
            endpoint = (step.get("endpoint") or "").strip()
            if not skill_name or not endpoint:
                print(f"  Step {step_num}: skill step missing 'skill' or 'endpoint', skipping.", file=sys.stderr)
                continue
            params = step.get("params")
            if not isinstance(params, dict):
                params = {}
            params = _apply_placeholders_deep(params, placeholders)
            output_path = step.get("output_path")
            if output_path is not None:
                output_path = str(output_path).strip() or None

            try:
                wurl = _get_worker()
            except Exception as e:
                print(f"Worker discovery failed (step {step_num}): {e}", file=sys.stderr)
                return 1

            print(f"  Step {step_num} ({step_id}, skill): {skill_name}/{endpoint}", file=sys.stderr)
            try:
                response = _call_skill(wurl, skill_name, endpoint, params, timeout=args.skill_timeout)
            except Exception as e:
                print(f"  Skill call failed (step {step_num}): {_format_http_error(e)}", file=sys.stderr)
                return 1

            step_responses[step_id] = response
            text = _extract_output(response, output_path)

            if isinstance(response, dict) and response.get("fetch_debug"):
                print("    --- fetch_debug ---", file=sys.stderr)
                for line in response["fetch_debug"]:
                    print(f"    {line}", file=sys.stderr)
                print("    ---", file=sys.stderr)
        else:
            prompt_cfg = (step.get("prompt") or "").strip()
            if not prompt_cfg:
                print(f"  Step {step_num}: AI step missing 'prompt', skipping.", file=sys.stderr)
                continue

            profile = (step.get("profile") or "agent").strip().lower()
            provider = step.get("provider")
            if provider is not None:
                provider = str(provider).strip() or None
            files = step.get("files")
            if not isinstance(files, list):
                files = []

            print(f"  Step {step_num} ({step_id}, ai): profile={profile}"
                  + (f", provider={provider}" if provider else ""), file=sys.stderr)

            try:
                context = _build_context(files, base_dir)
            except Exception as e:
                print(f"  Context error (step {step_num}): {e}", file=sys.stderr)
                return 1

            max_ctx = args.max_context_chars
            if max_ctx > 0 and len(previous_output) > max_ctx:
                placeholders = dict(placeholders)
                placeholders["previous_output"] = (
                    previous_output[:max_ctx] + "\n\n[... truncated for context limit ...]"
                )
                print(f"    Truncated previous_output to {max_ctx:,} chars (was {len(previous_output):,}).",
                      file=sys.stderr)

            prompt_resolved = _apply_placeholders(prompt_cfg, placeholders)
            full_prompt = _build_prompt(prompt_resolved, context)
            print(f"    Prompt: {len(full_prompt):,} chars", file=sys.stderr)

            try:
                text = _call_ai(_get_ai(), full_prompt, profile, provider, timeout=args.ai_timeout)
            except Exception as e:
                print(f"  AI request failed (step {step_num}): {_format_http_error(e)}", file=sys.stderr)
                return 1

        elapsed = time.monotonic() - t0
        previous_output = text
        outputs[f"step_{step_num}_output"] = text
        outputs[f"{step_id}_output"] = text

        _save_step_report(config_path.stem, run_ts, step_num, step_id, text)
        print(f"  Step {step_num} done ({elapsed:.1f}s). Output:", file=sys.stderr)
        print(text)
        if i < len(steps) - 1:
            print("", file=sys.stderr)

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = REPORTS_DIR / out_path
    else:
        out_path = REPORTS_DIR / f"{config_path.stem}_{run_ts}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(previous_output, encoding="utf-8")
    print(f"  Report saved to {out_path}", file=sys.stderr)

    return 0


def _save_step_report(config_stem: str, run_ts: str, step_num: int, step_id: str, text: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{config_stem}_{run_ts}_step{step_num}_{step_id}.txt"
    path.write_text(text, encoding="utf-8")


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run a multi-step AI prompt pipeline from a YAML config."
    )
    ap.add_argument("config", nargs="?", default=None,
                    help="Config YAML (name or path, e.g. cloud_services_notify_multistep.yaml)")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY_URL,
                    help="Registry URL")
    ap.add_argument("--aiserver-url", default=None,
                    help="AI server URL (overrides registry)")
    ap.add_argument("--worker-url", default=None,
                    help="Worker URL (overrides registry)")
    ap.add_argument("--out", default=None,
                    help="Report output path")
    ap.add_argument("--base-dir", default=None,
                    help="Base directory for relative file paths in config")
    ap.add_argument("-V", "--var", action="append", dest="var_overrides", default=[],
                    metavar="KEY=VALUE",
                    help="Override a config var (repeatable)")
    ap.add_argument("--skill-timeout", type=float, default=DEFAULT_SKILL_TIMEOUT,
                    metavar="SECONDS",
                    help=f"Timeout for skill calls (default: {DEFAULT_SKILL_TIMEOUT}s)")
    ap.add_argument("--ai-timeout", type=float, default=DEFAULT_AI_TIMEOUT,
                    metavar="SECONDS",
                    help=f"Timeout for AI calls (default: {DEFAULT_AI_TIMEOUT}s)")
    ap.add_argument("--max-context-chars", type=int, default=DEFAULT_MAX_CONTEXT_CHARS,
                    metavar="N",
                    help=f"Truncate previous_output beyond N chars (default: {DEFAULT_MAX_CONTEXT_CHARS}). 0=no limit")
    ap.add_argument("--async", action="store_true", dest="async_mode",
                    help="Submit to the workflow server for background execution")
    ap.add_argument("--resume", default=None, metavar="JOB_ID",
                    help="Resume polling a previously submitted async job")
    ap.add_argument("--workflow-url", default=None,
                    help="Workflow server URL (overrides registry). Used with --async/--resume.")
    ap.add_argument("--list", action="store_true", dest="list_configs",
                    help="List available prompt configs and exit")
    args = ap.parse_args()

    if args.list_configs:
        configs = sorted(p.name for p in PROMPTS_DIR.glob("*.yaml")) if PROMPTS_DIR.is_dir() else []
        if configs:
            print("Available prompt configs:")
            for c in configs:
                print(f"  {c}")
        else:
            print(f"No configs found in {PROMPTS_DIR}")
        return 0

    if not args.config and not args.resume:
        ap.error("config is required (or use --resume JOB_ID)")

    if args.async_mode or args.resume:
        return _run_async(args)

    return _run_local(args)


if __name__ == "__main__":
    sys.exit(main())
