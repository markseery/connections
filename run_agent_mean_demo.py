"""
License: MIT
Description: Demo script: use already-running servers (registry/agent/config/worker),
load skills via SkillLifecycle, ask the agent a question, and print the response.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import signal
import time
from typing import Any

import httpx

from common.skill_lifecycle import SkillLifecycle


ROOT = os.path.dirname(os.path.abspath(__file__))


def _wait_ok(url: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=1.5) as client:
                r = client.get(url)
                if r.status_code == 200 and isinstance(r.json(), dict) and r.json().get("status") == "ok":
                    return
        except Exception as e:
            last_err = str(e)
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {url}. Last error: {last_err}")


def _find_urls_via_registry(timeout_s: float = 60.0) -> dict[str, str]:
    """
    Find registry by scanning ports and requiring it to have agent/configuration entries.
    """
    deadline = time.monotonic() + timeout_s
    candidates = [7002] + [p for p in range(7000, 8000) if p != 7002]

    while time.monotonic() < deadline:
        for port in candidates:
            base = f"http://127.0.0.1:{port}"
            try:
                with httpx.Client(timeout=0.5) as client:
                    h = client.get(f"{base}/health")
                    if h.status_code != 200:
                        continue
                    j = h.json()
                    if not (isinstance(j, dict) and j.get("status") == "ok"):
                        continue
                    s = client.get(f"{base}/servers")
                    if s.status_code != 200:
                        continue
                    payload = s.json()
                    if not (isinstance(payload, dict) and isinstance(payload.get("servers"), list)):
                        continue

                servers: list[dict[str, Any]] = [x for x in payload["servers"] if isinstance(x, dict)]
                by_name = {str(x.get("name")): x for x in servers if x.get("name")}
                if not {"agent", "configuration"}.issubset(by_name.keys()):
                    continue

                out: dict[str, str] = {}
                for k in ["registry", "configuration", "agent", "worker-1", "worker"]:
                    if k in by_name and by_name[k].get("url"):
                        out[k] = str(by_name[k]["url"]).rstrip("/")
                return out
            except Exception:
                continue
        time.sleep(0.25)
    raise RuntimeError("Could not find registry/agent URLs for this run")


def main() -> int:
    prompt = "what is the mean of 10,13,45,23"
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:]).strip() or prompt

    urls = _find_urls_via_registry(timeout_s=30.0)
    registry_url = urls.get("registry")
    if not registry_url:
        registry_url = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")

    config_url = urls["configuration"]
    _wait_ok(f"{config_url}/health", timeout_s=30.0)

    # Start a dedicated worker instance for this demo run.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        worker_port = int(s.getsockname()[1])
    worker_url = f"http://127.0.0.1:{worker_port}"

    worker_env = os.environ.copy()
    worker_env["REGISTRY_SERVER_URL"] = registry_url
    worker_proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "servers.worker.main:app",
            "--host", "127.0.0.1", "--port", str(worker_port),
        ],
        cwd=ROOT,
        env=worker_env,
        start_new_session=True,
        stdout=None,
        stderr=None,
    )

    # Start an agent instance.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        agent_port = int(s.getsockname()[1])
    agent_url = f"http://127.0.0.1:{agent_port}"

    env = os.environ.copy()
    env["REGISTRY_SERVER_URL"] = registry_url
    agent_proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "servers.agent.main:app",
            "--host", "127.0.0.1", "--port", str(agent_port),
        ],
        cwd=ROOT,
        env=env,
        start_new_session=True,
        stdout=None,
        stderr=None,
    )
    try:
        _wait_ok(f"{agent_url}/health", timeout_s=30.0)
        _wait_ok(f"{worker_url}/health", timeout_s=30.0)

        # Use the shared SkillLifecycle to load + register skills.
        lifecycle = SkillLifecycle(
            registry_url=registry_url,
            config_url=config_url,
            worker_url=worker_url,
        )
        lifecycle.prepare()

        # Ask the agent.
        with httpx.Client(timeout=60.0) as client:
            r = client.post(f"{agent_url}/agent/execute", json={"prompt": prompt})
            if r.status_code >= 400:
                raise RuntimeError(f"Agent error {r.status_code}: {r.text}")
            data = r.json()

        result = data.get("result") or {}
        answer = result.get("answer") or result
        print(answer)
        return 0
    finally:
        try:
            os.killpg(worker_proc.pid, signal.SIGTERM)
        except Exception:
            try:
                worker_proc.terminate()
            except Exception:
                pass
        try:
            os.killpg(agent_proc.pid, signal.SIGTERM)
        except Exception:
            try:
                agent_proc.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
