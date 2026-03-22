"""
Agent configuration loader.

Loads YAML configs from config/agents/<agent_name>.yaml.
Supports nested key access via dot notation (e.g. "memory.working_ttl").

Usage:
    from common.agent_config import AgentConfigLoader

    _conf = AgentConfigLoader("supervisor")
    max_subagents = _conf.get("max_subagents", 5)
    working_ttl = _conf.get("memory.working_ttl", 3600)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, overload

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _ROOT / "config" / "agents"

_cache: dict[str, dict[str, Any]] = {}


def _load(agent_name: str) -> dict[str, Any]:
    if agent_name in _cache:
        return _cache[agent_name]
    path = _CONFIG_DIR / f"{agent_name}.yaml"
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
    _cache[agent_name] = cfg
    return cfg


def _resolve_dotted(data: dict[str, Any], key: str) -> Any:
    """Walk a dotted key path like 'memory.working_ttl'."""
    parts = key.split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


class AgentConfigLoader:
    """Typed, cached accessor for an agent's YAML config."""

    def __init__(self, agent_name: str) -> None:
        self._name = agent_name

    @overload
    def get(self, key: str, default: int) -> int: ...
    @overload
    def get(self, key: str, default: float) -> float: ...
    @overload
    def get(self, key: str, default: str) -> str: ...
    @overload
    def get(self, key: str, default: bool) -> bool: ...
    @overload
    def get(self, key: str, default: list) -> list: ...

    def get(self, key: str, default: Any) -> Any:
        """Return config value cast to type of *default*, or *default* if missing.

        Supports dotted keys: ``get("memory.working_ttl", 3600)``
        """
        v = _resolve_dotted(_load(self._name), key)
        if v is None:
            return default
        if isinstance(default, list):
            return v if isinstance(v, list) else default
        try:
            return type(default)(v)
        except (TypeError, ValueError):
            return default

    def raw(self) -> dict[str, Any]:
        return _load(self._name)

    def reload(self) -> None:
        _cache.pop(self._name, None)
