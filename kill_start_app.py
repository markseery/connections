"""
License: MIT
Description: Terminates ALL processes related to this project.

Finds and kills:
  1. `start_app.py` supervisor processes
  2. Orphaned uvicorn child servers (`servers.*.main:app`)
  3. Any `run_agent_mean_demo.py` or similar project scripts

Sends SIGTERM first, waits briefly, then escalates to SIGKILL.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

_MY_PID = os.getpid()

_PATTERNS = [
    "start_app.py",
    "servers.registry.main:app",
    "servers.storage.main:app",
    "servers.configuration.main:app",
    "servers.aiserver.main:app",
    "servers.chatserver.main:app",
    "servers.chatagent.main:app",
    "servers.connections_ui.main:app",
    "servers.agent.main:app",
    "servers.worker.main:app",
    "run_agent_mean_demo.py",
]


@dataclass(frozen=True)
class Proc:
    pid: int
    command: str


def _run(cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        shell=True,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def find_project_procs() -> list[Proc]:
    """Return all processes related to this project."""
    ps = _run("ps -axww -o pid=,command=")
    if ps.returncode != 0:
        raise RuntimeError(f"Failed to list processes: {ps.stderr.strip()}")

    seen: set[int] = set()
    procs: list[Proc] = []

    for line in ps.stdout.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*)$", line)
        if not m:
            continue
        pid = int(m.group(1))
        cmd = m.group(2)

        if pid == _MY_PID or pid in seen:
            continue
        if "kill_start_app" in cmd:
            continue
        cmd_lower = cmd.lower()
        if cmd_lower.startswith(("/bin/zsh", "/bin/bash", "/bin/sh")):
            continue

        if any(pat in cmd for pat in _PATTERNS):
            seen.add(pid)
            procs.append(Proc(pid=pid, command=cmd))

    return sorted(procs, key=lambda p: p.pid)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _kill_proc(proc: Proc, sig: int) -> None:
    """Send signal to a process, trying process-group first."""
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError):
        pass
    except Exception:
        try:
            os.kill(proc.pid, sig)
        except ProcessLookupError:
            pass


def terminate_all(procs: list[Proc], *, force: bool, dry_run: bool) -> int:
    if not procs:
        print("No project processes found.")
        return 0

    print(f"Found {len(procs)} project process(es):")
    for p in procs:
        label = p.command[:120]
        print(f"  pid={p.pid}  {label}")

    if dry_run:
        print("Dry-run: not sending any signals.")
        return 0

    sig_first = signal.SIGKILL if force else signal.SIGTERM
    for p in procs:
        _kill_proc(p, sig_first)

    if force:
        print("SIGKILL sent.")
        return 0

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not any(_pid_exists(p.pid) for p in procs):
            print("All processes terminated.")
            return 0
        time.sleep(0.3)

    still = [p for p in procs if _pid_exists(p.pid)]
    if still:
        print(f"Escalating to SIGKILL for {len(still)} remaining process(es):")
        for p in still:
            print(f"  pid={p.pid}")
            _kill_proc(p, signal.SIGKILL)

    time.sleep(1)
    remaining = [p for p in still if _pid_exists(p.pid)]
    if remaining:
        print(f"WARNING: {len(remaining)} process(es) could not be killed:")
        for p in remaining:
            print(f"  pid={p.pid}  {p.command[:120]}")
        return 1

    print("All processes terminated.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Terminate ALL processes related to this project "
        "(supervisor, uvicorn servers, demo scripts).",
    )
    ap.add_argument("--force", action="store_true", help="Send SIGKILL immediately.")
    ap.add_argument("--dry-run", action="store_true", help="List processes without killing.")
    args = ap.parse_args()

    try:
        procs = find_project_procs()
        return terminate_all(procs, force=args.force, dry_run=args.dry_run)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

