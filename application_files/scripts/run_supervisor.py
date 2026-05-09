#!/usr/bin/env python3
"""
Submit a goal to the autonomous supervisor agent and poll until completion.

The supervisor decomposes the goal into subagents, executes them with
approval gates and layered memory, and returns an aggregated result.

Usage:
  python scripts/run_supervisor.py "Find the latest CoreWeave news and summarise it"
  python scripts/run_supervisor.py --goal "Get stock quotes for AAPL and MSFT"
  python scripts/run_supervisor.py "Scrape https://nebius.com and summarise it" --timeout 600
  python scripts/run_supervisor.py --list-goals
  python scripts/run_supervisor.py --list-approvals
  python scripts/run_supervisor.py --approve <approval_id>
  python scripts/run_supervisor.py --deny <approval_id>
  python scripts/run_supervisor.py --episodes
  python scripts/run_supervisor.py --cancel <goal_id>

Requires: autonomous server (port 7027), registry, agent, worker(s), aiserver, storage.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


class AdaptivePollDelay:
    __slots__ = (
        "min_delay", "max_delay", "backoff", "burst_polls",
        "cooldown_after", "_current", "_no_change_count", "_burst_remaining",
    )

    def __init__(
        self,
        min_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff: float = 1.5,
        burst_polls: int = 2,
        cooldown_after: int = 20,
    ) -> None:
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.backoff = backoff
        self.burst_polls = burst_polls
        self.cooldown_after = cooldown_after
        self._current = min_delay
        self._no_change_count = 0
        self._burst_remaining = 0

    def next(self, changed: bool) -> float:
        if changed:
            self._no_change_count = 0
            self._burst_remaining = self.burst_polls
            self._current = self.min_delay
            return self._current

        if self._burst_remaining > 0:
            self._burst_remaining -= 1
            self._current = self.min_delay
            return self._current

        self._no_change_count += 1

        if self._no_change_count >= self.cooldown_after:
            self._current = self.max_delay
            return self._current

        self._current = min(self._current * self.backoff, self.max_delay)
        return self._current


def _default_url() -> str:
    return "http://127.0.0.1:7027"


def _pp(data: dict | list) -> None:
    """Pretty-print JSON data."""
    print(json.dumps(data, indent=2, default=str), flush=True)


def submit_goal(base: str, goal: str, *, timeout: float) -> str:
    body: dict = {"goal": goal}
    if timeout != 3600.0:
        body["config"] = {"timeout": timeout}

    with httpx.Client(timeout=30.0) as client:
        r = client.post(f"{base}/goals/submit", json=body)
        r.raise_for_status()
    data = r.json()
    goal_id = data.get("goal_id", "")
    status = data.get("status", "")
    print(f"Goal submitted: {goal_id}", flush=True)
    print(f"  Status: {status}", flush=True)
    return goal_id


def poll_goal(base: str, goal_id: str, *, timeout: float) -> dict:
    poll_delay = AdaptivePollDelay(
        min_delay=1.0,
        max_delay=30.0,
        backoff=1.5,
        burst_polls=2,
        cooldown_after=20,
    )

    deadline = time.monotonic() + timeout
    prev_status = ""
    prev_subagent_count = 0
    prev_completed = 0
    start_time = time.monotonic()

    print(f"\nPolling {base}/goals/{goal_id} ...", flush=True)

    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(f"{base}/goals/{goal_id}")
                if r.status_code == 404:
                    print(
                        f"\n  Goal {goal_id} no longer exists (server may have restarted).",
                        flush=True,
                    )
                    return {"status": "lost", "error": "Goal not found — server likely restarted."}
                r.raise_for_status()
            data = r.json()
        except httpx.HTTPStatusError as exc:
            print(f"  Poll error: {exc}", file=sys.stderr, flush=True)
            time.sleep(poll_delay.next(False))
            continue
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            print(f"  Connection error: {exc}", file=sys.stderr, flush=True)
            time.sleep(poll_delay.next(False))
            continue

        status = data.get("status", "")
        subagents = data.get("subagents") or []
        completed = sum(
            1 for s in subagents
            if s.get("status") in ("completed", "failed")
        )
        elapsed = round(time.monotonic() - start_time)

        changed = (
            status != prev_status
            or len(subagents) != prev_subagent_count
            or completed != prev_completed
        )

        if changed or elapsed % 30 == 0:
            _print_status(data, elapsed)

        prev_status = status
        prev_subagent_count = len(subagents)
        prev_completed = completed

        if status in ("completed", "failed", "cancelled"):
            return data

        delay = poll_delay.next(changed)
        time.sleep(delay)

    print(f"\nTimeout after {timeout}s. Last status: {prev_status}", flush=True)
    with httpx.Client(timeout=15.0) as client:
        r = client.get(f"{base}/goals/{goal_id}")
        r.raise_for_status()
    return r.json()


def _print_status(data: dict, elapsed: int) -> None:
    status = data.get("status", "")
    goal = data.get("goal", "")[:80]
    subagents = data.get("subagents") or []
    plan = data.get("plan") or {}
    objective = plan.get("objective", "")

    total = len(plan.get("subgoals") or [])
    completed = sum(
        1 for s in subagents
        if s.get("status") in ("completed", "failed")
    )
    running = sum(1 for s in subagents if s.get("status") == "executing")
    awaiting = sum(
        1 for s in subagents if s.get("status") == "awaiting_approval"
    )

    parts = [f"  [{status}]"]
    if total:
        parts.append(f"{completed}/{total} subagents done")
    if running:
        parts.append(f"{running} running")
    if awaiting:
        parts.append(f"{awaiting} awaiting approval")
    parts.append(f"({elapsed}s)")

    print(" — ".join(parts), flush=True)

    for sa in subagents:
        sa_status = sa.get("status", "")
        sa_type = sa.get("agent_type", "")
        desc = (sa.get("subgoal") or {}).get("description", "")[:60]
        duration = sa.get("duration_seconds", 0)
        marker = "✓" if sa_status == "completed" else ("✗" if sa_status == "failed" else "…")
        line = f"    {marker} [{sa_type}] {desc}"
        if sa_status in ("completed", "failed") and duration:
            line += f" ({duration:.1f}s)"
        if sa_status == "failed" and sa.get("error"):
            line += f" — {sa['error'][:80]}"
        if sa_status == "awaiting_approval":
            ids = sa.get("approval_ids") or []
            if ids:
                line += f" [approval: {ids[0]}]"
        print(line, flush=True)


def list_goals(base: str) -> None:
    with httpx.Client(timeout=15.0) as client:
        r = client.get(f"{base}/goals")
        r.raise_for_status()
    data = r.json()
    goals = data.get("goals") or []
    if not goals:
        print("No goals found.", flush=True)
        return
    print(f"Goals ({data.get('count', len(goals))}):", flush=True)
    for g in goals:
        gid = g.get("goal_id", "")
        status = g.get("status", "")
        goal_text = g.get("goal", "")[:60]
        created = g.get("created_at", "")[:19]
        n_sub = len(g.get("subagents") or [])
        print(f"  {gid}  [{status}]  {goal_text}  ({n_sub} subagents, {created})", flush=True)


def list_approvals(base: str) -> None:
    with httpx.Client(timeout=15.0) as client:
        r = client.get(f"{base}/approvals/pending")
        r.raise_for_status()
    data = r.json()
    approvals = data.get("approvals") or []
    if not approvals:
        print("No pending approvals.", flush=True)
        return
    print(f"Pending approvals ({data.get('count', len(approvals))}):", flush=True)
    for a in approvals:
        aid = a.get("approval_id", "")
        action = a.get("action_description", "")
        risk = a.get("risk_level", "")
        goal_id = a.get("goal_id", "")
        requested = a.get("requested_at", "")[:19]
        print(f"  {aid}", flush=True)
        print(f"    Action: {action}", flush=True)
        print(f"    Risk: {risk}  Goal: {goal_id}  Requested: {requested}", flush=True)


def approve_action(base: str, approval_id: str) -> None:
    with httpx.Client(timeout=15.0) as client:
        r = client.post(f"{base}/approvals/{approval_id}/approve")
        r.raise_for_status()
    data = r.json()
    print(f"Approved: {approval_id} (status={data.get('status', '')})", flush=True)


def deny_action(base: str, approval_id: str) -> None:
    with httpx.Client(timeout=15.0) as client:
        r = client.post(f"{base}/approvals/{approval_id}/deny")
        r.raise_for_status()
    data = r.json()
    print(f"Denied: {approval_id} (status={data.get('status', '')})", flush=True)


def cancel_goal(base: str, goal_id: str) -> None:
    with httpx.Client(timeout=15.0) as client:
        r = client.post(f"{base}/goals/{goal_id}/cancel")
        r.raise_for_status()
    data = r.json()
    print(f"Cancelled: {goal_id} (status={data.get('status', '')})", flush=True)


def show_episodes(base: str, agent_id: str) -> None:
    with httpx.Client(timeout=15.0) as client:
        r = client.get(f"{base}/memory/{agent_id}/episodes")
        r.raise_for_status()
    data = r.json()
    episodes = data.get("episodes") or []
    if not episodes:
        print(f"No episodes for agent '{agent_id}'.", flush=True)
        return
    print(f"Episodes for '{agent_id}' ({data.get('count', len(episodes))}):", flush=True)
    for ep in episodes:
        gid = ep.get("goal_id", "")
        outcome = ep.get("outcome", "")
        summary = ep.get("goal_summary", "")[:60]
        duration = ep.get("duration_seconds", 0)
        ts = ep.get("timestamp", "")[:19]
        skills = ", ".join(ep.get("skills_used") or [])
        print(f"  [{outcome}] {summary} ({duration:.0f}s, {ts})", flush=True)
        if skills:
            print(f"    Skills: {skills}", flush=True)
        findings = ep.get("key_findings") or []
        for f in findings[:3]:
            print(f"    - {f[:100]}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Submit goals to the supervisor agent and monitor progress.",
    )
    ap.add_argument(
        "goal_arg", nargs="?", default="",
        help="Goal text (or use --goal)",
    )
    ap.add_argument(
        "--goal", metavar="TEXT", default="",
        help="Goal text (overrides positional)",
    )
    ap.add_argument(
        "--url", metavar="URL", default="",
        help=f"Autonomous server URL (default: {_default_url()})",
    )
    ap.add_argument(
        "--timeout", type=float,
        default=3600.0,
        help="Max seconds to wait for goal completion",
    )
    ap.add_argument(
        "--list-goals", action="store_true",
        help="List all submitted goals",
    )
    ap.add_argument(
        "--list-approvals", action="store_true",
        help="List pending approvals",
    )
    ap.add_argument(
        "--approve", metavar="ID", default="",
        help="Approve a pending approval by ID",
    )
    ap.add_argument(
        "--deny", metavar="ID", default="",
        help="Deny a pending approval by ID",
    )
    ap.add_argument(
        "--cancel", metavar="GOAL_ID", default="",
        help="Cancel a running goal",
    )
    ap.add_argument(
        "--episodes", action="store_true",
        help="Show episodic memory for the supervisor agent",
    )
    ap.add_argument(
        "--agent-id", metavar="ID", default="supervisor",
        help="Agent ID for memory commands (default: supervisor)",
    )
    ap.add_argument(
        "--json", action="store_true", dest="output_json",
        help="Print full JSON result instead of formatted output",
    )
    args = ap.parse_args()

    base = (args.url or _default_url()).rstrip("/")

    if args.list_goals:
        list_goals(base)
        return 0

    if args.list_approvals:
        list_approvals(base)
        return 0

    if args.approve:
        approve_action(base, args.approve)
        return 0

    if args.deny:
        deny_action(base, args.deny)
        return 0

    if args.cancel:
        cancel_goal(base, args.cancel)
        return 0

    if args.episodes:
        show_episodes(base, args.agent_id)
        return 0

    goal_text = (args.goal or args.goal_arg or "").strip()
    if not goal_text:
        ap.print_help()
        print("\nError: goal text is required (positional or --goal)", file=sys.stderr)
        return 1

    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{base}/health")
            r.raise_for_status()
    except Exception as exc:
        print(
            f"Error: cannot reach autonomous server at {base} — {exc}",
            file=sys.stderr,
        )
        print(
            "Make sure the server is running (check app_config.yaml, port 7027).",
            file=sys.stderr,
        )
        return 1

    try:
        goal_id = submit_goal(base, goal_text, timeout=args.timeout)
        result = poll_goal(base, goal_id, timeout=args.timeout)
    except httpx.HTTPStatusError as exc:
        print(f"HTTP error: {exc.response.status_code} — {exc.response.text[:300]}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    status = result.get("status", "")
    print(f"\n{'='*60}", flush=True)
    print(f"Goal: {goal_text}", flush=True)
    print(f"Status: {status}", flush=True)

    if result.get("answer"):
        print(f"\n--- Answer ---", flush=True)
        print(result["answer"], flush=True)

    if result.get("error"):
        print(f"\n--- Error ---", flush=True)
        print(result["error"], flush=True)

    if args.output_json:
        print(f"\n--- Full JSON ---", flush=True)
        _pp(result)

    return 0 if status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
