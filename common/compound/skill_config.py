"""
Per-skill YAML configuration loader.

Each skill can have its own config file at config/skills/<skill_name>.yaml.
Skills use SkillConfig to load their config once (cached) and read typed values.

Resolution order: user dir ``config/skills/<name>.yaml`` wins over the repo
default at ``config/skills/<name>.yaml``.  When both exist, the user file is
deep-merged on top of the repo file so users only need to specify overrides.

Usage in a skill:

    from common.skill_config import SkillConfig

    _conf = SkillConfig("webscraper_skill")

    max_depth = _conf.get("max_depth_limit", 100)
    delay     = _conf.get("crawl_delay", 0.1)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, overload

from common.simple.user_dir import repo_root, user_dir

_cache: dict[str, dict[str, Any]] = {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def _load(skill_name: str) -> dict[str, Any]:
    if skill_name in _cache:
        return _cache[skill_name]
    repo_path = repo_root() / "config" / "skills" / f"{skill_name}.yaml"
    user_path = user_dir() / "config" / "skills" / f"{skill_name}.yaml"
    cfg: dict[str, Any] = {}
    import yaml
    if repo_path.is_file():
        try:
            with open(repo_path) as f:
                raw = yaml.safe_load(f)
            if isinstance(raw, dict):
                cfg = raw
        except Exception:
            pass
    if user_path.is_file():
        try:
            with open(user_path) as f:
                raw = yaml.safe_load(f)
            if isinstance(raw, dict):
                cfg = _deep_merge(cfg, raw) if cfg else raw
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
