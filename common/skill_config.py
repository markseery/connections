"""
Per-skill YAML configuration loader.

Each skill can have its own config file at config/skills/<skill_name>.yaml.
Skills use SkillConfig to load their config once (cached) and read typed values.

Usage in a skill:

    from common.skill_config import SkillConfig

    _conf = SkillConfig("webscraper_skill")

    max_depth = _conf.get("max_depth_limit", 100)
    delay     = _conf.get("crawl_delay", 0.1)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, overload

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _ROOT / "config" / "skills"

_cache: dict[str, dict[str, Any]] = {}


def _load(skill_name: str) -> dict[str, Any]:
    if skill_name in _cache:
        return _cache[skill_name]
    path = _CONFIG_DIR / f"{skill_name}.yaml"
    cfg: dict[str, Any] = {}
    if path.is_file():
        import yaml
        try:
            with open(path) as f:
                raw = yaml.safe_load(f)
            if isinstance(raw, dict):
                cfg = raw
        except Exception:
            pass
    _cache[skill_name] = cfg
    return cfg


class SkillConfig:
    """Typed, cached accessor for a skill's YAML config."""

    def __init__(self, skill_name: str) -> None:
        self._name = skill_name

    @overload
    def get(self, key: str, default: int) -> int: ...
    @overload
    def get(self, key: str, default: float) -> float: ...
    @overload
    def get(self, key: str, default: str) -> str: ...
    @overload
    def get(self, key: str, default: bool) -> bool: ...

    def get(self, key: str, default: Any) -> Any:
        """Return config value cast to the type of *default*, or *default* if missing."""
        v = _load(self._name).get(key)
        if v is None:
            return default
        try:
            return type(default)(v)
        except (TypeError, ValueError):
            return default

    def raw(self) -> dict[str, Any]:
        """Return the full config dict (read-only reference)."""
        return _load(self._name)

    def reload(self) -> None:
        """Force re-read from disk on next access."""
        _cache.pop(self._name, None)
