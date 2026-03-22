"""
License: MIT
Description: Discover agent skills from the configuration server. Skill configs are
stored as resource_type=skill, resource_name=<name>; value can include server_name
(resolve URL via registry) or base_url, plus routes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import get_config_server_url, get_registry_url


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


def discover_skills(config_url: str | None = None, registry_url: str | None = None) -> list[SkillDefinition]:
    """
    List config keys, filter skill:*, fetch each config, resolve base_url via registry.
    Results are cached for _CACHE_TTL seconds to avoid redundant HTTP calls when
    multiple subagents discover skills within a short window.
    """
    global _cached_skills, _cache_time

    with _cache_lock:
        if _cached_skills is not None and (time.monotonic() - _cache_time) < _CACHE_TTL:
            return list(_cached_skills)

    config_url = config_url or get_config_server_url()
    registry_url = registry_url or get_registry_url()

    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{config_url}/configs")
        r.raise_for_status()
        data = r.json()
        keys = data.get("keys") or []

    skills: list[SkillDefinition] = []
    for key in keys:
        if not isinstance(key, str) or not key.startswith("skill:"):
            continue
        parts = key.split(":", 1)
        resource_type = parts[0]
        resource_name = parts[1] if len(parts) > 1 else ""
        if not resource_name:
            continue

        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{config_url}/configs/{resource_type}/{resource_name}")
            if r.status_code == 404:
                continue
            r.raise_for_status()
            rec = r.json()

        value = rec.get("value") if isinstance(rec.get("value"), dict) else rec
        if not value:
            continue

        base_url = value.get("base_url", "").strip()
        if not base_url and value.get("server_name"):
            server_name = value.get("server_name", "").strip()
            if server_name:
                with httpx.Client(timeout=5.0) as client:
                    rr = client.get(f"{registry_url}/servers/{server_name}")
                    if rr.status_code != 200:
                        continue
                    base_url = (rr.json().get("url") or "").rstrip("/")
        if not base_url:
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
                skill_name=resource_name,
                base_url=base_url,
                routes=routes,
            )
        )

    with _cache_lock:
        _cached_skills = skills
        _cache_time = time.monotonic()

    return skills
