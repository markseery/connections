from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from common.simple.user_dir import repo_root, user_dir

_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _substitute_env(value: Any, *, enabled: bool) -> Any:
    if not enabled:
        return value
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v, enabled=enabled) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v, enabled=enabled) for v in value]
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


@dataclass
class OrchestratorConfig:
    raw: dict[str, Any]
    orchestrator: dict[str, Any]
    subscribers: list[dict[str, Any]]
    repo_root: Path

    @classmethod
    def load(
        cls,
        *,
        orchestrator_path: Path | None = None,
        subscribers_path: Path | None = None,
    ) -> OrchestratorConfig:
        root = repo_root()
        orch_repo = root / "config" / "state" / "state_orchestrator.yaml"
        sub_repo = root / "config" / "state" / "subscribers.yaml"
        orch_user = user_dir() / "config" / "state" / "state_orchestrator.yaml"
        sub_user = user_dir() / "config" / "state" / "subscribers.yaml"

        orch_path = orchestrator_path or (orch_user if orch_user.is_file() else orch_repo)
        sub_path = subscribers_path or (sub_user if sub_user.is_file() else sub_repo)

        orch_raw = _load_yaml(orch_path)
        sub_raw = _load_yaml(sub_path)
        if orch_user.is_file() and orch_path == orch_user and orch_repo.is_file():
            orch_raw = _deep_merge(_load_yaml(orch_repo), orch_raw)
        if sub_user.is_file() and sub_path == sub_user and sub_repo.is_file():
            sub_raw = _deep_merge(_load_yaml(sub_repo), sub_raw)

        env_cfg = orch_raw.get("env_substitution") if isinstance(orch_raw.get("env_substitution"), dict) else {}
        env_on = bool(env_cfg.get("enabled", False))
        orch_raw = _substitute_env(orch_raw, enabled=env_on)
        sub_raw = _substitute_env(sub_raw, enabled=env_on)

        orchestrator = orch_raw.get("orchestrator")
        if not isinstance(orchestrator, dict):
            raise ValueError(f"orchestrator section missing in {orch_path}")

        subs = sub_raw.get("subscribers")
        if not isinstance(subs, list):
            subs = []

        return cls(
            raw=orch_raw,
            orchestrator=orchestrator,
            subscribers=[s for s in subs if isinstance(s, dict)],
            repo_root=root,
        )

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.orchestrator
        for k in keys:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(k)
        return cur if cur is not None else default

    def resolve_path(self, rel: str) -> Path:
        p = Path(rel).expanduser()
        if p.is_absolute():
            return p.resolve()
        return (self.repo_root / p).resolve()

    def load_symbols(self) -> list[str]:
        sym_cfg = self.get("symbols_from")
        if not isinstance(sym_cfg, dict):
            raise ValueError("orchestrator.symbols_from must be configured")
        stype = str(sym_cfg.get("type") or "")
        if stype != "positions_yaml":
            raise ValueError(f"unsupported symbols_from.type: {stype}")
        path = self.resolve_path(str(sym_cfg.get("path") or ""))
        field_name = str(sym_cfg.get("symbol_field") or "symbol")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = raw.get("positions") or raw.get("symbols") or []
        seen: set[str] = set()
        out: list[str] = []
        if isinstance(items, list):
            for row in items:
                if isinstance(row, dict):
                    sym = str(row.get(field_name) or "").strip().upper()
                else:
                    sym = str(row or "").strip().upper()
                if sym and sym not in seen:
                    seen.add(sym)
                    out.append(sym)
        return out

    def machine_config_for_symbol(self, symbol: str) -> dict[str, Any]:
        defaults = self.get("machine_defaults")
        if not isinstance(defaults, dict):
            raise ValueError("orchestrator.machine_defaults must be configured")
        cfg = deepcopy(defaults)
        machine_id = symbol.strip().upper()
        entity = cfg.get("entity") if isinstance(cfg.get("entity"), dict) else {}
        entity = dict(entity)
        entity["type"] = str(entity.get("type") or "symbol")
        entity["symbol"] = machine_id
        cfg["entity"] = entity
        cfg["machine_id"] = machine_id
        return cfg
