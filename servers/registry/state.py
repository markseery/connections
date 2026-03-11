"""
License: MIT
Description: Registry in-memory state with optional persistence.

Stores the current URL/host/port for named servers so other processes can query
where to send requests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Registration:
    name: str
    host: str
    port: int
    url: str
    pid: int | None = None
    updatedAt: str = ""
    createdAt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "url": self.url,
            "pid": self.pid,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Registration":
        return cls(
            name=str(d["name"]),
            host=str(d["host"]),
            port=int(d["port"]),
            url=str(d["url"]),
            pid=int(d["pid"]) if d.get("pid") is not None else None,
            createdAt=str(d.get("createdAt") or ""),
            updatedAt=str(d.get("updatedAt") or ""),
        )


class RegistryState:
    def __init__(self, persist_path: Path | None = None) -> None:
        self._persist_path = persist_path
        self._by_name: dict[str, Registration] = {}
        if self._persist_path and self._persist_path.is_file():
            try:
                raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if isinstance(v, dict):
                            self._by_name[k] = Registration.from_dict(v)
            except Exception:
                # If persistence is corrupted, just start fresh.
                self._by_name = {}

    def list(self) -> list[dict[str, Any]]:
        return [self._by_name[k].to_dict() for k in sorted(self._by_name)]

    def get(self, name: str) -> dict[str, Any] | None:
        reg = self._by_name.get(name)
        return reg.to_dict() if reg else None

    def upsert(self, name: str, host: str, port: int, pid: int | None = None) -> dict[str, Any]:
        url = f"http://{host}:{port}"
        now = _now()
        existing = self._by_name.get(name)
        if existing:
            created = existing.createdAt or now
        else:
            created = now

        reg = Registration(
            name=name,
            host=host,
            port=port,
            url=url,
            pid=pid,
            createdAt=created,
            updatedAt=now,
        )
        self._by_name[name] = reg
        self._persist()
        return reg.to_dict()

    def delete(self, name: str) -> bool:
        if name not in self._by_name:
            return False
        self._by_name.pop(name, None)
        self._persist()
        return True

    def _persist(self) -> None:
        if not self._persist_path:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v.to_dict() for k, v in self._by_name.items()}
        self._persist_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

