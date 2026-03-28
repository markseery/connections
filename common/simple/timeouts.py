"""
License: MIT
Description: Centralized timeout configuration read from app_config.yaml.
All skill and server code should use these instead of hardcoded values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "app_config.yaml"

_DEFAULTS: dict[str, float] = {
    "skill_call": 3600,
    # Workflow YAML subprocess steps (crawls, batch scripts); each step gets this full budget.
    "workflow_subprocess": 7200,
    "ai_generate": 600,
    "inter_service": 30,
    "storage": 15,
    "registry": 10,
    "content_fetch": 30,
    "feed_fetch": 30,
    "smtp": 30,
}

_cache: dict[str, float] | None = None


def _load() -> dict[str, float]:
    global _cache
    if _cache is not None:
        return _cache
    merged = dict(_DEFAULTS)
    try:
        text = _CONFIG_PATH.read_text(encoding="utf-8")
        cfg = yaml.safe_load(text)
        if isinstance(cfg, dict) and isinstance(cfg.get("timeouts"), dict):
            for k, v in cfg["timeouts"].items():
                if isinstance(v, (int, float)) and v > 0:
                    merged[str(k)] = float(v)
    except Exception:
        pass
    _cache = merged
    return _cache


def get(name: str) -> float:
    """Get a named timeout in seconds. Falls back to defaults if not in config."""
    return _load().get(name, _DEFAULTS.get(name, 30.0))


def reload() -> None:
    """Force re-read of config (e.g. after config change)."""
    global _cache
    _cache = None
