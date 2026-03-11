"""
License: MIT
Description: Terminates all running instances of `start_app.py`.

Finds matching processes and sends SIGTERM to their process groups (so child
uvicorn servers are terminated too). Escalates to SIGKILL if needed.
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


def find_start_app_procs() -> list[Proc]:
    """
    Return processes whose command line contains `start_app.py`.
    Prefers `pgrep -f` (more reliable across macOS ps variants), and falls back
    to parsing `ps` output.
    """
    # First try pgrep (available on macOS).
    cp = _run("pgrep -f \"python(3(\\.\\d+)?)? .*start_app\\.py\" || true")
    pids: list[int] = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.isdigit():
            pids.append(int(line))

    if pids:
        # Get command lines for display.
        ps = _run("ps -axww -o pid=,command=")
        cmd_by_pid: dict[int, str] = {}
        if ps.returncode == 0:
            for line in ps.stdout.splitlines():
                m = re.match(r"\\s*(\\d+)\\s+(.*)$", line)
                if m:
                    cmd_by_pid[int(m.group(1))] = m.group(2)
        return [Proc(pid=pid, command=cmd_by_pid.get(pid, "")) for pid in sorted(set(pids))]

    # Fallback: parse ps output.
    ps = _run("ps aux")
    if ps.returncode != 0:
        raise RuntimeError(f"Failed to list processes: {ps.stderr.strip()}")

    procs: list[Proc] = []
    for line in ps.stdout.splitlines():
        if "start_app.py" not in line:
            continue
        if "kill_start_app.py" in line:
            continue
        parts = line.split(None, 10)
        if len(parts) < 2:
            continue
        pid_str = parts[1]
        if not pid_str.isdigit():
            continue
        procs.append(Proc(pid=int(pid_str), command=parts[-1] if parts else line))

    # De-duplicate
    seen: set[int] = set()
    uniq: list[Proc] = []
    for p in procs:
        if p.pid in seen:
            continue
        seen.add(p.pid)
        uniq.append(p)
    return uniq


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but we can't signal it.
        return True


def terminate_all(procs: list[Proc], *, force: bool, dry_run: bool) -> int:
    if not procs:
        print("No start_app.py processes found.")
        return 0

    for p in procs:
        print(f"Found pid={p.pid} cmd={shlex.quote(p.command) if p.command else ''}")

    if dry_run:
        print("Dry-run: not sending any signals.")
        return 0

    sig_first = signal.SIGKILL if force else signal.SIGTERM
    for p in procs:
        try:
            # Prefer process-group kill (matches start_new_session=True in start_app.py)
            os.killpg(p.pid, sig_first)
        except ProcessLookupError:
            continue
        except Exception:
            try:
                os.kill(p.pid, sig_first)
            except ProcessLookupError:
                continue

    if force:
        return 0

    # Wait briefly, then escalate remaining.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not any(_pid_exists(p.pid) for p in procs):
            return 0
        time.sleep(0.2)

    still = [p for p in procs if _pid_exists(p.pid)]
    if still:
        print("Escalating to SIGKILL for remaining pids:", ", ".join(str(p.pid) for p in still))
        for p in still:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except Exception:
                try:
                    os.kill(p.pid, signal.SIGKILL)
                except Exception:
                    pass
        return 0

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Terminate all running start_app.py instances.")
    ap.add_argument("--force", action="store_true", help="Send SIGKILL immediately.")
    ap.add_argument("--dry-run", action="store_true", help="List processes without killing.")
    args = ap.parse_args()

    try:
        procs = find_start_app_procs()
        return terminate_all(procs, force=args.force, dry_run=args.dry_run)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

