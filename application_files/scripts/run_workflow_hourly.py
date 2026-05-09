#!/usr/bin/env python3
"""
Run a workflow template once or on a fixed schedule.

Usage:
    python3 scripts/run_workflow_hourly.py --name tco_research --once
    python3 scripts/run_workflow_hourly.py --name tco_research --runs 10 --interval 3600
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import httpx


def _worker_url(registry: str) -> str:
    for name in ["worker-1", "worker"]:
        try:
            r = httpx.get(f"{registry}/servers/{name}", timeout=5)
            if r.status_code == 200:
                url = (r.json() or {}).get("url", "").rstrip("/")
                if url:
                    return url
        except Exception:
            continue
    raise RuntimeError("No live worker found in registry")


def run_workflow(worker: str, name: str, params: dict, timeout: float) -> dict:
    url = f"{worker}/skills/workflow_skill/run/{name}"
    payload = {**params, "timeout": timeout}
    r = httpx.post(url, json=payload, timeout=timeout + 30)
    return r.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a workflow on a schedule")
    parser.add_argument("--name", required=True, help="Workflow template name")
    parser.add_argument("--once", action="store_true", help="Run once and print full output")
    parser.add_argument("--runs", type=int, default=10, help="Number of runs")
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between runs")
    parser.add_argument("--timeout", type=float, default=600, help="Per-run timeout")
    parser.add_argument("--registry-url", default="http://127.0.0.1:7002")
    parser.add_argument("--params", default="{}", help="JSON string of workflow params")
    args = parser.parse_args()

    params = json.loads(args.params)
    worker = _worker_url(args.registry_url)

    template = httpx.get(f"{worker}/skills/workflow_skill/templates/{args.name}", timeout=10)
    if template.status_code == 404:
        print(f"Workflow template '{args.name}' not found", file=sys.stderr)
        sys.exit(1)

    if args.once:
        print(f"Running '{args.name}' once ...", flush=True)
        result = run_workflow(worker, args.name, params, args.timeout)
        data = result.get("data", {})
        status = data.get("status", "unknown")
        completed = data.get("steps_completed", 0)
        total = data.get("steps_total", 0)
        failed = data.get("steps_failed", 0)

        print(f"\nStatus: {status} ({completed}/{total} steps, {failed} failed)")
        if data.get("error"):
            print(f"Error: {data['error']}")
        print()

        for r in data.get("step_results", []):
            ok = "OK" if r["success"] else "FAIL"
            dur = r.get("duration_ms", 0)
            print(f"--- Step {r['step_id']} ({r.get('skill','?')}): {ok}  [{dur:.0f}ms] ---")
            if r.get("error"):
                print(f"  Error: {r['error'][:500]}")
            elif r.get("data") and isinstance(r["data"], dict):
                rd = r["data"]
                if rd.get("summary"):
                    print(f"  Summary: {rd['summary'][:300]}")
                if rd.get("text"):
                    print(f"  Text ({len(rd['text'])} chars):")
                    print(rd["text"][:2000])
            print()
        return

    print(f"Scheduling {args.runs} runs of '{args.name}', every {args.interval}s", flush=True)
    print(f"Worker: {worker}", flush=True)
    print(f"Started: {datetime.now(timezone.utc).isoformat()}", flush=True)
    print("=" * 60, flush=True)

    for i in range(1, args.runs + 1):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\n[Run {i}/{args.runs}] {ts}", flush=True)

        try:
            result = run_workflow(worker, args.name, params, args.timeout)
            data = result.get("data", {})
            status = data.get("status", "unknown")
            completed = data.get("steps_completed", 0)
            total = data.get("steps_total", 0)
            failed = data.get("steps_failed", 0)
            print(f"  Status: {status} ({completed}/{total} steps, {failed} failed)", flush=True)
            if data.get("error"):
                print(f"  Error: {data['error'][:300]}", flush=True)
        except Exception as exc:
            print(f"  FAILED: {exc}", flush=True)

        if i < args.runs:
            next_ts = datetime.now(timezone.utc).timestamp() + args.interval
            next_str = datetime.fromtimestamp(next_ts, tz=timezone.utc).strftime("%H:%M:%S UTC")
            print(f"  Next run at {next_str} (sleeping {args.interval}s)", flush=True)
            time.sleep(args.interval)

    print(f"\nAll {args.runs} runs complete.", flush=True)


if __name__ == "__main__":
    main()
