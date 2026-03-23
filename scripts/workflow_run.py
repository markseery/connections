#!/usr/bin/env python3
"""
Submit a workflow YAML to the workflow server (POST /workflows/submit), or run it locally.

Resolves the config path on this machine when possible so full paths work;
otherwise sends the basename for server-side lookup in configuration/ or data/workflows/.

Usage:
  python3 scripts/workflow_run.py data/workflows/webscrape_create_facts.yaml
  python3 scripts/workflow_run.py webscrape_create_facts.yaml --wait --set sitename=nebius
  python3 scripts/workflow_run.py webscrape_create_facts.yaml --local --set sitename=nebius
  python3 scripts/workflow_run.py my.yaml --vars '{"topic":"products"}' --subprocess-timeout 7200

Environment:
  WORKFLOW_SERVER_URL — base URL (default http://127.0.0.1:7026)
  REGISTRY_SERVER_URL — used by --local for aiserver/worker discovery (default http://127.0.0.1:7002)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _ensure_repo_on_path() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _run_local(
    config_str: str,
    vars_merged: dict[str, str],
    *,
    ai_timeout: float,
    skill_timeout: float,
    subprocess_timeout: float | None,
    max_context_chars: int,
    verbose_steps: bool,
) -> int:
    _ensure_repo_on_path()
    from servers.workflow.executor import WorkflowExecutor, WorkflowStepError

    reg = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
    ex = WorkflowExecutor(
        registry_url=reg,
        ai_timeout=ai_timeout,
        skill_timeout=skill_timeout,
        subprocess_timeout=subprocess_timeout,
        max_context_chars=max_context_chars,
    )
    p = Path(config_str).expanduser()
    if not p.is_absolute():
        cand = (ROOT / p).resolve()
        if cand.is_file():
            cfg_path = ex.resolve_config_path(str(cand))
        else:
            cfg_path = ex.resolve_config_path(p.name)
    elif p.is_file():
        cfg_path = ex.resolve_config_path(str(p.resolve()))
    else:
        cfg_path = ex.resolve_config_path(p.name)

    def _on_progress(step_num: int, step_id: str, status: str, err: str | None) -> None:
        if not verbose_steps:
            return
        extra = f" — {err}" if err else ""
        print(f"  step {step_num} ({step_id}): {status}{extra}", flush=True)

    try:
        result = ex.run(cfg_path, var_overrides=vars_merged, on_step_progress=_on_progress if verbose_steps else None)
    except WorkflowStepError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Workflow failed: {e}", file=sys.stderr)
        return 1

    print("status: completed")
    fo = (result.final_output or "").strip()
    if fo:
        print("--- final_output ---")
        print(result.final_output)
    if result.report_path:
        print(f"report_path: {result.report_path}")
    return 0


def _default_base_url() -> str:
    return os.environ.get("WORKFLOW_SERVER_URL", "http://127.0.0.1:7026").rstrip("/")


def _resolve_config_arg(arg: str) -> str:
    """Return absolute path string if file exists, else basename for server resolution."""
    p = Path(arg).expanduser()
    if not p.is_absolute():
        cand = (ROOT / p).resolve()
        if cand.is_file():
            return str(cand)
    elif p.is_file():
        return str(p.resolve())
    return p.name


def _parse_set_pairs(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in pairs:
        if "=" not in raw:
            raise argparse.ArgumentTypeError(f"--set expects key=value, got {raw!r}")
        k, _, v = raw.partition("=")
        k = k.strip()
        if not k:
            raise argparse.ArgumentTypeError(f"empty key in --set {raw!r}")
        out[k] = v
    return out


def _poll_job(base: str, job_id: str, interval: float, max_wait: float | None) -> dict[str, Any]:
    deadline = time.monotonic() + max_wait if max_wait else None
    url = f"{base}/workflows/jobs/{job_id}"
    while True:
        r = httpx.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        if status in ("completed", "failed"):
            return data
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f"job {job_id} still {status!r} after {max_wait}s")
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a workflow YAML via the workflow server or in-process (--local)",
    )
    parser.add_argument(
        "config",
        help="Workflow file (path or basename, e.g. data/workflows/foo.yaml or foo.yaml)",
    )
    parser.add_argument(
        "--url",
        default=_default_base_url(),
        help="Workflow server base URL (default: WORKFLOW_SERVER_URL or http://127.0.0.1:7026)",
    )
    parser.add_argument(
        "--vars",
        default="{}",
        help='JSON object merged into workflow vars, e.g. \'{"sitename":"nebius"}\'',
    )
    parser.add_argument(
        "--set",
        dest="set_pairs",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Workflow var (repeatable); merged after --vars",
    )
    parser.add_argument("--wait", action="store_true", help="Poll until job completes or fails")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between polls when --wait")
    parser.add_argument(
        "--max-wait",
        type=float,
        default=None,
        help="Max seconds to poll (default: no limit)",
    )
    parser.add_argument("--skill-timeout", type=float, default=300, help="Submit skill_timeout")
    parser.add_argument("--ai-timeout", type=float, default=300, help="Submit ai_timeout")
    parser.add_argument(
        "--subprocess-timeout",
        type=float,
        default=None,
        help="Per-step subprocess cap in seconds (omit: server defaults to 7200; --local uses timeouts.workflow_subprocess, default 7200)",
    )
    parser.add_argument("--max-context-chars", type=int, default=150_000, help="Submit max_context_chars")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run workflow in-process via WorkflowExecutor (no workflow HTTP server required)",
    )
    parser.add_argument(
        "--verbose-steps",
        action="store_true",
        help="With --local, print each step as it runs",
    )
    args = parser.parse_args()

    try:
        vars_obj = json.loads(args.vars)
    except json.JSONDecodeError as e:
        print(f"Invalid --vars JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(vars_obj, dict):
        print("--vars must be a JSON object", file=sys.stderr)
        return 2
    vars_merged: dict[str, str] = {str(k): str(v) for k, v in vars_obj.items()}
    try:
        vars_merged.update(_parse_set_pairs(args.set_pairs))
    except argparse.ArgumentTypeError as e:
        print(str(e), file=sys.stderr)
        return 2

    config_str = _resolve_config_arg(args.config)

    if args.local:
        return _run_local(
            args.config,
            vars_merged,
            ai_timeout=args.ai_timeout,
            skill_timeout=args.skill_timeout,
            subprocess_timeout=args.subprocess_timeout,
            max_context_chars=args.max_context_chars,
            verbose_steps=args.verbose_steps,
        )

    base = args.url.rstrip("/")

    payload: dict[str, Any] = {
        "config": config_str,
        "vars": vars_merged,
        "skill_timeout": args.skill_timeout,
        "ai_timeout": args.ai_timeout,
        "max_context_chars": args.max_context_chars,
    }
    if args.subprocess_timeout is not None:
        payload["subprocess_timeout"] = args.subprocess_timeout

    submit_url = f"{base}/workflows/submit"
    try:
        r = httpx.post(submit_url, json=payload, timeout=60)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            detail = e.response.text or str(e)
        print(f"Submit failed: {detail}", file=sys.stderr)
        return 1
    except httpx.RequestError as e:
        print(f"Request failed: {e}", file=sys.stderr)
        err_s = str(e).lower()
        if "refused" in err_s or "errno 61" in err_s or "errno 111" in err_s:
            print(
                "\nNo workflow server is listening (start it in another terminal):\n"
                "  python3 -m servers.workflow.run\n\n"
                "Or run the workflow in-process without HTTP:\n"
                "  python3 scripts/workflow_run.py ... --local\n",
                file=sys.stderr,
            )
        return 1

    out = r.json()
    job_id = out.get("job_id", "")
    print(f"job_id: {job_id}")
    print(f"poll_url: {base}{out.get('poll_url', '')}")

    if not args.wait:
        return 0

    try:
        job = _poll_job(base, job_id, args.poll_interval, args.max_wait)
    except TimeoutError as e:
        print(str(e), file=sys.stderr)
        return 1

    status = job.get("status")
    print(f"status: {status}")
    if job.get("message"):
        print(f"message: {job['message']}")
    if job.get("error"):
        print(f"error: {job['error']}", file=sys.stderr)
    fo = job.get("final_output") or ""
    if fo.strip():
        print("--- final_output ---")
        print(fo)
    if job.get("report_path"):
        print(f"report_path: {job['report_path']}")

    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
