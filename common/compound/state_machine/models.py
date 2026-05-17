from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class StateChangeEvent:
    machine_id: str
    dimension: str
    entity: dict[str, str]
    old_state_id: str | None
    new_state_id: str | None
    old_raw: dict[str, Any] | None
    new_raw: dict[str, Any]
    changed_at: datetime
    correlation_id: str

    @property
    def topic(self) -> str:
        return f"state.{self.machine_id}.{self.dimension}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "machine_id": self.machine_id,
            "dimension": self.dimension,
            "entity": dict(self.entity),
            "old_state_id": self.old_state_id,
            "new_state_id": self.new_state_id,
            "old_raw": self.old_raw,
            "new_raw": self.new_raw,
            "changed_at": self.changed_at.isoformat().replace("+00:00", "Z"),
            "correlation_id": self.correlation_id,
            "topic": self.topic,
        }


@dataclass
class DimensionSnapshot:
    state_id: str
    raw: dict[str, Any] = field(default_factory=dict)
    previous_state_id: str | None = None
    last_changed_at: str | None = None
    last_pull_at: str | None = None
    pull_status: str = "pending"
    pull_error: str | None = None


@dataclass
class MachineSnapshot:
    machine_id: str
    entity: dict[str, str]
    updated_at: str
    dimensions: dict[str, DimensionSnapshot] = field(default_factory=dict)

    def to_storage(self) -> dict[str, Any]:
        return {
            "machine_id": self.machine_id,
            "entity": dict(self.entity),
            "updated_at": self.updated_at,
            "dimensions": {
                k: {
                    "state_id": v.state_id,
                    "raw": v.raw,
                    "previous_state_id": v.previous_state_id,
                    "last_changed_at": v.last_changed_at,
                    "last_pull_at": v.last_pull_at,
                    "pull_status": v.pull_status,
                    "pull_error": v.pull_error,
                }
                for k, v in self.dimensions.items()
            },
        }

    @classmethod
    def from_storage(cls, data: dict[str, Any]) -> MachineSnapshot:
        dims_in = data.get("dimensions") or {}
        dims: dict[str, DimensionSnapshot] = {}
        if isinstance(dims_in, dict):
            for name, d in dims_in.items():
                if not isinstance(d, dict):
                    continue
                dims[str(name)] = DimensionSnapshot(
                    state_id=str(d.get("state_id") or "unknown"),
                    raw=d.get("raw") if isinstance(d.get("raw"), dict) else {},
                    previous_state_id=d.get("previous_state_id"),
                    last_changed_at=d.get("last_changed_at"),
                    last_pull_at=d.get("last_pull_at"),
                    pull_status=str(d.get("pull_status") or "unknown"),
                    pull_error=d.get("pull_error"),
                )
        entity = data.get("entity") if isinstance(data.get("entity"), dict) else {}
        return cls(
            machine_id=str(data.get("machine_id") or ""),
            entity={str(k): str(v) for k, v in entity.items()},
            updated_at=str(data.get("updated_at") or ""),
            dimensions=dims,
        )
