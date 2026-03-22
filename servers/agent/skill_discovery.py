"""
License: MIT
Description: Discover agent skills from the configuration server. Skill configs are
stored as resource_type=skill, resource_name=<name>; value includes routes.

The base_url for every skill is resolved from the registry (live worker) rather
than trusting the value stored in config, which can go stale when workers restart
on different ports.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import get_config_server_url, get_registry_url

WORKER_NAMES = ["worker-1", "worker-2", "worker"]


@dataclass
class SkillRoute:
    method: str
    path: str
    description: str = ""


@dataclass
class SkillDefinition:
    skill_name: str
    base_url: str
    routes: list[SkillRoute]


_cache_lock = threading.Lock()
_cached_skills: list[SkillDefinition] | None = None
_cache_time: float = 0.0
_CACHE_TTL: float = 60.0


def _find_live_worker(registry_url: str) -> str | None:
    """Return the URL of the first healthy worker found in the registry."""
    with httpx.Client(timeout=3.0) as client:
        for name in WORKER_NAMES:
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


def discover_skills(config_url: str | None = None, registry_url: str | None = None) -> list[SkillDefinition]:
    """
    List config keys, filter skill:*, fetch each config, resolve base_url from
    the live worker in the registry.

    Results are cached for _CACHE_TTL seconds to avoid redundant HTTP calls when
    multiple subagents discover skills within a short window.
    """
    global _cached_skills, _cache_time

    with _cache_lock:
        if _cached_skills is not None and (time.monotonic() - _cache_time) < _CACHE_TTL:
            return list(_cached_skills)

    config_url = config_url or get_config_server_url()
    registry_url = registry_url or get_registry_url()

    worker_url = _find_live_worker(registry_url)
    if not worker_url:
        print("[skill_discovery] no live worker found in registry", flush=True)
        return []

    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{config_url}/configs/skill")
        r.raise_for_status()
        data = r.json()
        all_records: dict[str, Any] = data.get("records") or {}

    skills: list[SkillDefinition] = []
    for skill_name, rec in all_records.items():
        if not skill_name:
            continue

        value = rec.get("value") if isinstance(rec.get("value"), dict) else rec
        if not value:
            continue

        routes: list[SkillRoute] = []
        for ro in value.get("routes") or []:
            if isinstance(ro, dict) and ro.get("method") and ro.get("path"):
                routes.append(
                    SkillRoute(
                        method=str(ro["method"]).upper(),
                        path=str(ro["path"]),
                        description=str(ro.get("description", "")),
                    )
                )
        skills.append(
            SkillDefinition(
                skill_name=skill_name,
                base_url=worker_url,
                routes=routes,
            )
        )

    with _cache_lock:
        _cached_skills = skills
        _cache_time = time.monotonic()

    return skills
