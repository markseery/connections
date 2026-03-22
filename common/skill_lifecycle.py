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
from pathlib import Path
from typing import Any

import httpx

from servers.agent.skill_discovery import SkillDefinition, SkillRoute, discover_skills


WORKER_NAMES = ["worker-1", "worker-2", "worker"]
_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# Removed from the codebase; delete stale configuration keys so agent/planner
# do not keep resolving a dead skill module.
_OBSOLETE_SKILL_CONFIG_NAMES = ("stored_webscrape_skill",)


@dataclass
class SkillLifecycle:
    """
    Encapsulates the full lifecycle needed to make configured skills callable:

        1. Resolve registry + configuration URLs.
        2. Find a healthy worker from the registry.
        3. Auto-discover skill modules from the skills/ directory that aren't
           yet in configuration, register them so they become visible.
        4. Load each configured skill into the worker.
        5. Update configuration entries so base_url points at the live worker.
        6. Return SkillDefinitions ready for the planner/executor.
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
        self._retire_obsolete_skill_configs()
        self._seed_new_skills()
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

    # ── Retire removed skills (config cleanup) ───────────────────────────

    def _retire_obsolete_skill_configs(self) -> None:
        """DELETE obsolete skill:* records from configuration (best-effort)."""
        with httpx.Client(timeout=5.0) as client:
            for name in _OBSOLETE_SKILL_CONFIG_NAMES:
                try:
                    r = client.delete(f"{self.config_url}/configs/skill/{name}")
                    if r.status_code == 200:
                        print(
                            f"[skill_lifecycle] removed obsolete config skill:{name}",
                            flush=True,
                        )
                except Exception as exc:
                    print(
                        f"[skill_lifecycle] retire skill:{name} failed: {exc}",
                        flush=True,
                    )

    # ── Auto-seed new skills ────────────────────────────────────────────

    def _seed_new_skills(self) -> None:
        """Load skill modules found in skills/ into the worker and register
        any that aren't yet in config.  Route discovery is delegated to the
        worker (GET /worker/skills/{name}/routes) so we never import heavy
        skill dependencies inside the calling process."""
        try:
            existing = discover_skills(
                config_url=self.config_url, registry_url=self.registry_url
            )
        except Exception as exc:
            print(f"[skill_lifecycle] discover_skills failed: {exc}", flush=True)
            existing = []
        known_names = {sk.skill_name for sk in existing}

        fs_names = _scan_skill_modules()
        new_names = [n for n in fs_names if n not in known_names]
        if not new_names:
            return

        with httpx.Client(timeout=10.0) as client:
            for name in new_names:
                routes = _worker_skill_routes(client, self.worker_url, name)
                if not routes:
                    continue
                _register_skill(
                    client, self.config_url, self.worker_url, name, routes
                )

    # ── Load + register ─────────────────────────────────────────────────

    def _load_and_register(self) -> None:
        raw = discover_skills(
            config_url=self.config_url, registry_url=self.registry_url
        )
        live: list[SkillDefinition] = []
        with httpx.Client(timeout=5.0) as client:
            for sk in raw:
                fresh_routes = _worker_skill_routes(
                    client, self.worker_url, sk.skill_name
                )
                routes = fresh_routes if fresh_routes else sk.routes
                _register_skill(
                    client, self.config_url, self.worker_url, sk.skill_name, routes
                )
                live.append(
                    SkillDefinition(
                        skill_name=sk.skill_name,
                        base_url=self.worker_url,
                        routes=routes,
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
            except Exception as exc:
                print(f"[skill_lifecycle] worker {name} probe failed: {exc}", flush=True)
                continue
    return None


def load_skill(worker_url: str, skill_name: str) -> bool:
    """Kept for backward compatibility; with auto-load middleware this is a no-op."""
    return True


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


def _scan_skill_modules() -> list[str]:
    """Return skill module names found in the skills/ directory."""
    if not _SKILLS_DIR.is_dir():
        return []
    names: list[str] = []
    for p in sorted(_SKILLS_DIR.iterdir()):
        if p.suffix != ".py" or p.name.startswith("_"):
            continue
        names.append(p.stem)
    return names


def _worker_skill_routes(
    client: httpx.Client, worker_url: str, skill_name: str
) -> list[SkillRoute]:
    """Ask the worker for route metadata of an already-loaded skill."""
    try:
        r = client.get(f"{worker_url}/worker/skills/{skill_name}/routes")
        if r.status_code != 200:
            return []
        data = r.json()
        return [
            SkillRoute(
                method=str(ro.get("method", "")).upper(),
                path=str(ro.get("path", "")),
                description=str(ro.get("description", "")),
            )
            for ro in (data.get("routes") or [])
            if ro.get("method") and ro.get("path")
        ]
    except Exception as exc:
        print(f"[skill_lifecycle] route query for {skill_name} failed: {exc}", flush=True)
        return []


def _load_skill(client: httpx.Client, worker_url: str, skill_name: str) -> bool:
    try:
        r = client.post(f"{worker_url}/worker/skills/{skill_name}/load")
        return r.status_code < 400
    except Exception as exc:
        print(f"[skill_lifecycle] load {skill_name} failed: {exc}", flush=True)
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
    except Exception as exc:
        print(f"[skill_lifecycle] register {skill_name} failed: {exc}", flush=True)
