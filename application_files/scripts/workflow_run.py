#!/usr/bin/env python3
"""
Submit a workflow YAML to the workflow server (POST /workflows/submit).

Resolves the config path on this machine when possible so full paths work;
otherwise sends the basename for server-side lookup in configuration/ or data/workflows/.

Usage:
  python3 scripts/workflow_run.py data/workflows/webscrape_create_facts.yaml
  python3 scripts/workflow_run.py webscrape_create_facts.yaml --set sitename=nebius
  python3 scripts/workflow_run.py my.yaml --vars '{"topic":"products"}' --subprocess-timeout 7200

Environment:
  WORKFLOW_SERVER_URL — base URL (default http://127.0.0.1:7026)
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

APP_ROOT = Path(__file__).resolve().parents[1]


def _default_base_url() -> str:
    return os.environ.get("WORKFLOW_SERVER_URL", "http://127.0.0.1:7026").rstrip("/")


def _resolve_config_arg(arg: str) -> str:
    """Return absolute path string if file exists, else basename for server resolution."""
    p = Path(arg).expanduser()
    if not p.is_absolute():
        for base in (APP_ROOT, APP_ROOT / "workflows"):
            cand = (base / p).resolve()
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


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s" if m else f"{s}s"


def _poll_job(base: str, job_id: str, interval: float, max_wait: float | None) -> dict[str, Any]:
    deadline = time.monotonic() + max_wait if max_wait else None
    url = f"{base}/workflows/jobs/{job_id}"
    poll_start = time.monotonic()
    seen_offsets: dict[str, int] = {}
    announced_running: set[str] = set()
    announced_done: set[str] = set()

    while True:
        r = httpx.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        total = data.get("total_steps", 0)
        completed = data.get("completed_steps", 0)
        elapsed = _fmt_elapsed(time.monotonic() - poll_start)
        steps = data.get("step_progress") or []

        for s in steps:
            sid = s.get("step_id", "?")
            s_status = s.get("status", "pending")

            if s_status == "running" and sid not in announced_running:
                step_type = s.get("step_type", "")
                label = f" ({step_type})" if step_type else ""
                print(f"\n  [{completed}/{total}] {sid}: running{label} [{elapsed}]", flush=True)
                announced_running.add(sid)

            if s_status in ("completed", "failed", "skipped") and sid not in announced_done:
                s_elapsed = s.get("elapsed_ms", 0)
                if s_status == "completed":
                    print(f"  [{completed}/{total}] {sid}: completed "
                          f"({_fmt_elapsed(s_elapsed / 1000)}) [{elapsed}]", flush=True)
                elif s_status == "failed":
                    err = s.get("error", "")
                    print(f"  [{completed}/{total}] {sid}: FAILED [{elapsed}] — {err}", flush=True)
                elif s_status == "skipped":
                    reason = s.get("skipped_reason", "")
                    print(f"  [{completed}/{total}] {sid}: skipped ({reason})", flush=True)
                announced_done.add(sid)

            if s_status == "running":
                log_tail = s.get("log_tail") or []
                log_offset = s.get("log_offset", 0)
                prev_offset = seen_offsets.get(sid, 0)
                if log_offset > prev_offset:
                    new_count = log_offset - prev_offset
                    new_lines = log_tail[-new_count:] if new_count <= len(log_tail) else log_tail
                    for line in new_lines:
                        print(f"    {sid} | {line}", flush=True)
                    seen_offsets[sid] = log_offset

        if status in ("completed", "failed"):
            return data
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f"job {job_id} still {status!r} after {max_wait}s")
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a workflow YAML via the workflow HTTP server",
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
    parser.add_argument("--no-wait", action="store_true", help="Submit and exit immediately without polling")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between status polls")
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
        help="Per-step subprocess cap in seconds (omit: server defaults to 7200)",
    )
    parser.add_argument("--max-context-chars", type=int, default=150_000, help="Submit max_context_chars")
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
                "\nNo workflow server is listening. Start it in another terminal:\n"
                "  python3 -m servers.workflow.run\n",
                file=sys.stderr,
            )
        return 1

    out = r.json()
    job_id = out.get("job_id", "")
    total_steps = out.get("total_steps", "?")
    config_name = out.get("config") or out.get("config_name") or args.config

    print(f"\nWorkflow: {config_name}", flush=True)
    print(f"Job:      {job_id}", flush=True)
    print(f"Steps:    {total_steps}", flush=True)
    print(f"Poll URL: {base}{out.get('poll_url', '')}", flush=True)

    if args.no_wait:
        return 0

    print(f"\nPolling (every {args.poll_interval}s) ...\n", flush=True)
    run_start = time.monotonic()

    try:
        job = _poll_job(base, job_id, args.poll_interval, args.max_wait)
    except TimeoutError as e:
        print(str(e), file=sys.stderr)
        return 1

    total_elapsed = time.monotonic() - run_start
    status = job.get("status")
    total = job.get("total_steps", 0)
    completed = job.get("completed_steps", 0)
    steps = job.get("step_progress") or []

    print(f"\n{'='*60}", flush=True)
    print(f"  Status: {status}  ({completed}/{total} steps)", flush=True)
    print(f"{'='*60}", flush=True)

    step_total_ms = 0.0
    for s in steps:
        sid = s.get("step_id", "?")
        s_status = s.get("status", "?")
        s_elapsed = s.get("elapsed_ms", 0)
        step_total_ms += s_elapsed
        marker = "OK" if s_status == "completed" else s_status.upper()
        elapsed_str = _fmt_elapsed(s_elapsed / 1000) if s_elapsed else "-"
        err = s.get("error", "")
        extra = f"  {err}" if err else ""
        print(f"  {sid:20s}  {marker:10s}  {elapsed_str:>10s}{extra}", flush=True)

    print(f"{'─'*60}", flush=True)
    print(f"  {'Steps total':<20s}  {'':10s}  {_fmt_elapsed(step_total_ms / 1000):>10s}", flush=True)
    print(f"  {'Wall clock':<20s}  {'':10s}  {_fmt_elapsed(total_elapsed):>10s}", flush=True)
    print(f"{'='*60}", flush=True)

    if job.get("error"):
        print(f"\nError: {job['error']}", file=sys.stderr)
    fo = job.get("final_output") or ""
    if fo.strip():
        print(f"\n--- final output ---\n{fo}", flush=True)
    if job.get("report_path"):
        print(f"Report: {job['report_path']}", flush=True)

    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
