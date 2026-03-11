"""
License: MIT
Description: Shared skill lifecycle: discover skills from configuration, find a live
worker via registry, load skill modules into the worker, and register/update
config entries so skill routes are callable.

Any server or script that needs to call skills should use this class instead of
reimplementing the discovery → worker-load → config-update dance.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from servers.agent.skill_discovery import SkillDefinition, SkillRoute, discover_skills


WORKER_NAMES = ["worker-1", "worker-2", "worker"]


@dataclass
class SkillLifecycle:
    """
    Encapsulates the full lifecycle needed to make configured skills callable:

        1. Resolve registry + configuration URLs.
        2. Find a healthy worker from the registry.
        3. Load each configured skill into the worker.
        4. Update configuration entries so base_url points at the live worker.
        5. Return SkillDefinitions ready for the planner/executor.
    """

    registry_url: str = ""
    config_url: str = ""
    worker_url: str = ""
    _skills: list[SkillDefinition] = field(default_factory=list, repr=False)

    # ── public API ──────────────────────────────────────────────────────

    def prepare(self) -> list[SkillDefinition]:
        """
        Run the full lifecycle and return live SkillDefinitions.
        Raises RuntimeError if no worker can be found.
        """
        self._resolve_urls()
        self._find_worker()
        self._load_and_register()
        return list(self._skills)

    @property
    def skills(self) -> list[SkillDefinition]:
        return list(self._skills)

    # ── URL resolution ──────────────────────────────────────────────────

    def _resolve_urls(self) -> None:
        if not self.registry_url:
            self.registry_url = os.environ.get(
                "REGISTRY_SERVER_URL", "http://127.0.0.1:7002"
            ).strip().rstrip("/")
        if not self.config_url:
            self.config_url = _server_url(self.registry_url, "configuration")

    # ── Worker discovery ────────────────────────────────────────────────

    def _find_worker(self) -> None:
        if self.worker_url:
            return
        url = find_live_worker(self.registry_url)
        if not url:
            raise RuntimeError("No live worker found in registry")
        self.worker_url = url

    # ── Load + register ─────────────────────────────────────────────────

    def _load_and_register(self) -> None:
        raw = discover_skills(
            config_url=self.config_url, registry_url=self.registry_url
        )
        live: list[SkillDefinition] = []
        with httpx.Client(timeout=5.0) as client:
            for sk in raw:
                if not _load_skill(client, self.worker_url, sk.skill_name):
                    continue
                _register_skill(
                    client, self.config_url, self.worker_url, sk.skill_name, sk.routes
                )
                live.append(
                    SkillDefinition(
                        skill_name=sk.skill_name,
                        base_url=self.worker_url,
                        routes=sk.routes,
                    )
                )
        self._skills = live


# ── Standalone helpers (usable without the class) ──────────────────────


def find_live_worker(
    registry_url: str,
    names: list[str] | None = None,
) -> str | None:
    """Return the URL of the first healthy worker found in the registry."""
    names = names or WORKER_NAMES
    with httpx.Client(timeout=3.0) as client:
        for name in names:
            try:
                r = client.get(f"{registry_url}/servers/{name}")
                if r.status_code != 200:
                    continue
                url = ((r.json() or {}).get("url") or "").rstrip("/")
                if not url:
                    continue
                h = client.get(f"{url}/health")
                if h.status_code == 200:
                    return url
            except Exception:
                continue
    return None


def load_skill(worker_url: str, skill_name: str) -> bool:
    """POST /worker/skills/{skill_name}/load on the given worker. Returns success."""
    with httpx.Client(timeout=5.0) as client:
        return _load_skill(client, worker_url, skill_name)


def register_skill(
    config_url: str,
    worker_url: str,
    skill_name: str,
    routes: list[SkillRoute],
) -> None:
    """PUT the skill config entry with base_url pointing at the given worker."""
    with httpx.Client(timeout=5.0) as client:
        _register_skill(client, config_url, worker_url, skill_name, routes)


def route_exists(skill: SkillDefinition, method: str, path_template: str) -> bool:
    """Check whether a SkillDefinition contains the given route."""
    for r in skill.routes:
        if r.method.upper() != method.upper():
            continue
        if r.path == path_template:
            return True
        pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", r.path)
        if re.fullmatch(pattern, path_template):
            return True
    return False


# ── Internal helpers ────────────────────────────────────────────────────


def _server_url(registry_url: str, server_name: str) -> str:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{registry_url}/servers/{server_name}")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError(f"Registry missing url for {server_name}")
        return str(url).rstrip("/")


def _load_skill(client: httpx.Client, worker_url: str, skill_name: str) -> bool:
    try:
        r = client.post(f"{worker_url}/worker/skills/{skill_name}/load")
        return r.status_code < 400
    except Exception:
        return False


def _register_skill(
    client: httpx.Client,
    config_url: str,
    worker_url: str,
    skill_name: str,
    routes: list[SkillRoute],
) -> None:
    defn: dict[str, Any] = {
        "base_url": worker_url,
        "routes": [
            {"method": r.method, "path": r.path, "description": r.description}
            for r in routes
        ],
    }
    try:
        client.put(f"{config_url}/configs/skill/{skill_name}", json=defn)
    except Exception:
        pass
