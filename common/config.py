"""
Centralized reader for app_config.yaml.

Provides typed accessors for config sections (workers, timeouts, etc.).
Config is loaded once and cached; call reload() after changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "app_config.yaml"

_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        text = _CONFIG_PATH.read_text(encoding="utf-8")
        _cache = yaml.safe_load(text) or {}
    except Exception:
        _cache = {}
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
