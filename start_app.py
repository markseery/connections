"""
License: MIT
Description: Application startup supervisor.

Reads `app_config.yaml`, starts all configured FastAPI servers using uvicorn,
performs periodic health checks, and restarts servers that fail repeated health
checks. If this supervisor process is terminated, it terminates all child servers.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml


@dataclass
class HealthConfig:
    interval_seconds: float = 2.0
    timeout_seconds: float = 1.0
    retries: int = 3
    fail_retry_delay_seconds: float = 2.0


@dataclass
class ServerConfig:
    name: str
    app: str  # uvicorn app string, e.g. servers.storage.main:app
    host: str = "127.0.0.1"
    port: int = 8000
    port_min: int = 7000
    port_max: int = 7999
    base_name: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}/health"


class Supervisor:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.health = HealthConfig()
        self.servers: list[ServerConfig] = []
        self.procs: dict[str, subprocess.Popen[bytes]] = {}
        self.fail_counts: dict[str, int] = {}
        self._reserved_ports: dict[str, int] = {}
        self._registered: set[str] = set()
        self._stop = False

    def load_config(self) -> None:
        cfg = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        health = cfg.get("health") or {}
        self.health = HealthConfig(
            interval_seconds=float(health.get("interval_seconds", self.health.interval_seconds)),
            timeout_seconds=float(health.get("timeout_seconds", self.health.timeout_seconds)),
            retries=int(health.get("retries", self.health.retries)),
            fail_retry_delay_seconds=float(
                health.get("fail_retry_delay_seconds", self.health.fail_retry_delay_seconds)
            ),
        )

        servers_cfg = cfg.get("servers") or []
        self.servers = []
        for s in servers_cfg:
            base_name = str(s["name"])
            instances = int(s.get("instances", 1))
            if instances < 1:
                instances = 1
            base_port = int(s.get("port", 8000))
            for i in range(instances):
                name = base_name if instances == 1 else f"{base_name}-{i+1}"
                port = base_port + i if instances > 1 else base_port
                self.servers.append(
                    ServerConfig(
                        name=name,
                        base_name=base_name,
                        app=str(s["app"]),
                        host=str(s.get("host", "127.0.0.1")),
                        port=port,
                        port_min=int(s.get("port_min", 7000)),
                        port_max=int(s.get("port_max", 7999)),
                    )
                )

    def start_all(self) -> None:
        # Start registry first, then storage, then dependents.
        def _prio(s: ServerConfig) -> int:
            if s.base_name == "registry" or s.name == "registry":
                return 0
            if s.base_name == "storage" or s.name == "storage":
                return 1
            return 2

        ordered = sorted(self.servers, key=_prio)
        self._allocate_ports(ordered)
        for s in ordered:
            self.start_one(s)

    def _allocate_ports(self, ordered: list[ServerConfig]) -> None:
        """
        Allocate unique ports for all servers before launching any process.
        This avoids two servers choosing the same "free" port during startup.
        """
        self._reserved_ports.clear()
        for s in ordered:
            chosen_port = self._choose_port(s)
            if chosen_port != s.port:
                print(
                    f"[supervisor] {s.name} port {s.port} in use; using {chosen_port} instead",
                    flush=True,
                )
                s.port = chosen_port
            self._reserved_ports[s.name] = s.port

    def _is_port_free(self, host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return True
            except OSError as exc:
                print(f"[supervisor] port {port} on {host} not free: {exc}", flush=True)
                return False

    def _choose_port(self, s: ServerConfig) -> int:
        """
        Choose a free port in [port_min, port_max], starting at the configured port.
        Keeps servers in the 7000 range by default.
        """
        start = s.port
        if start < s.port_min or start > s.port_max:
            start = s.port_min

        # Scan forward from start to port_max, then wrap to port_min.
        for p in list(range(start, s.port_max + 1)) + list(range(s.port_min, start)):
            if p in self._reserved_ports.values():
                continue
            if self._is_port_free(s.host, p):
                return p
        raise RuntimeError(f"No free ports for {s.name} in range {s.port_min}-{s.port_max}")

    def start_one(self, s: ServerConfig) -> None:
        self.stop_one(s.name)

        env = os.environ.copy()
        # Port is pre-allocated in start_all() via _allocate_ports().

        registry = next((x for x in self.servers if x.name == "registry"), None)
        if registry is not None:
            env["REGISTRY_SERVER_URL"] = registry.base_url

        # Inject derived configuration between servers.
        # Configuration and worker (skills) talk to the storage server.
        storage = next((x for x in self.servers if x.base_name == "storage" or x.name == "storage"), None)
        if storage is not None:
            if s.base_name == "configuration" or s.name == "configuration":
                env["STORAGE_SERVER_URL"] = storage.base_url
            if s.base_name == "worker" or s.name.startswith("worker"):
                env["STORAGE_SERVER_URL"] = storage.base_url

        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            s.app,
            "--host",
            s.host,
            "--port",
            str(s.port),
        ]
        # Start a new session so we can kill the whole process group on Unix.
        proc = subprocess.Popen(cmd, env=env, start_new_session=True, stdout=None, stderr=None)
        self.procs[s.name] = proc
        self.fail_counts[s.name] = 0
        self._registered.discard(s.name)
        print(f"[supervisor] started {s.name} pid={proc.pid} {s.host}:{s.port}", flush=True)

    def stop_one(self, name: str) -> None:
        proc = self.procs.get(name)
        if not proc:
            return
        try:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except Exception as exc:
                    print(f"[supervisor] killpg SIGTERM failed for {name}: {exc}", flush=True)
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception as exc:
                        print(f"[supervisor] killpg SIGKILL failed for {name}: {exc}", flush=True)
                        proc.kill()
        finally:
            self.procs.pop(name, None)
            self._reserved_ports.pop(name, None)
            self._registered.discard(name)

    def stop_all(self) -> None:
        for name in list(self.procs.keys()):
            self.stop_one(name)

    def _check_health_once(self, s: ServerConfig) -> bool:
        try:
            with httpx.Client(timeout=self.health.timeout_seconds) as client:
                r = client.get(s.health_url)
                if r.status_code != 200:
                    return False
                data = r.json()
                return isinstance(data, dict) and data.get("status") == "ok"
        except Exception as exc:
            print(f"[supervisor] health check failed for {s.name}: {exc}", flush=True)
            return False

    def _check_health_with_retries(self, s: ServerConfig) -> bool:
        """
        Do a normal health check; if it fails, retry quickly `retries` times
        before declaring the server unhealthy.
        """
        if self._check_health_once(s):
            return True
        for _ in range(max(1, self.health.retries)):
            time.sleep(self.health.fail_retry_delay_seconds)
            if self._check_health_once(s):
                return True
        return False

    def _register_with_registry(self, s: ServerConfig) -> None:
        """
        Register a healthy server in the registry: PUT /servers/{name}.
        Uses transport-encrypted responses if desired by callers, but here we
        send plain JSON for simplicity.
        """
        registry = next((x for x in self.servers if x.name == "registry"), None)
        if registry is None:
            return

        # Registry must be reachable.
        try:
            with httpx.Client(timeout=2.0) as client:
                r = client.get(f"{registry.base_url}/health")
                if r.status_code != 200:
                    return
        except Exception as exc:
            print(f"[supervisor] registry health check failed: {exc}", flush=True)
            return

        pid = self.procs.get(s.name).pid if self.procs.get(s.name) else None
        payload = {"host": s.host, "port": s.port, "pid": pid}
        try:
            with httpx.Client(timeout=3.0) as client:
                client.put(f"{registry.base_url}/servers/{s.name}", json=payload).raise_for_status()
            self._registered.add(s.name)
        except Exception as exc:
            print(f"[supervisor] registry registration failed for {s.name}: {exc}", flush=True)
            return

    def monitor_loop(self) -> None:
        while not self._stop:
            for s in self.servers:
                proc = self.procs.get(s.name)
                if proc and proc.poll() is not None:
                    print(f"[supervisor] {s.name} exited (code={proc.returncode}), restarting", flush=True)
                    self.start_one(s)
                    continue

                ok = self._check_health_with_retries(s)
                if not ok:
                    print(f"[supervisor] {s.name} unhealthy after retries; restarting", flush=True)
                    self.start_one(s)
                    continue

                # Healthy: ensure registered.
                if s.name not in self._registered:
                    self._register_with_registry(s)

            time.sleep(self.health.interval_seconds)

    def request_stop(self) -> None:
        self._stop = True


def main() -> int:
    root = Path(__file__).resolve().parent
    cfg_path = root / "app_config.yaml"
    if not cfg_path.is_file():
        print(f"[supervisor] missing config: {cfg_path}", file=sys.stderr)
        return 2

    sup = Supervisor(cfg_path)
    sup.load_config()

    def _handle_signal(_signum: int, _frame: Any) -> None:
        print("[supervisor] termination requested; stopping servers", flush=True)
        sup.request_stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        sup.start_all()
        sup.monitor_loop()
        return 0
    finally:
        sup.stop_all()
        print("[supervisor] all servers stopped", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())

