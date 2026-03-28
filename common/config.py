"""
Centralized reader for app_config.yaml.

Provides typed accessors for config sections (workers, timeouts, etc.).
Config is loaded once and cached; call reload() after changes.

Resolution order: user dir ``app_config.yaml`` (if present) is deep-merged
on top of the repo-root ``app_config.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from common.user_dir import repo_root, user_dir

_cache: dict[str, Any] | None = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base* (override wins for scalars)."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    repo_cfg_path = repo_root() / "app_config.yaml"
    user_cfg_path = user_dir() / "app_config.yaml"
    base: dict[str, Any] = {}
    try:
        text = repo_cfg_path.read_text(encoding="utf-8")
        base = yaml.safe_load(text) or {}
    except Exception:
        pass
    if user_cfg_path.is_file():
        try:
            text = user_cfg_path.read_text(encoding="utf-8")
            override = yaml.safe_load(text) or {}
            if isinstance(override, dict):
                base = _deep_merge(base, override)
        except Exception:
            pass
    _cache = base
    return _cache


def get_section(name: str) -> dict[str, Any]:
    cfg = _load()
    val = cfg.get(name)
    return dict(val) if isinstance(val, dict) else {}


def worker_instances() -> int:
    return int(get_section("workers").get("instances", 2))


def threads_per_worker() -> int:
    return int(get_section("workers").get("threads_per_worker", 6))


def reload() -> None:
    global _cache
    _cache = None
