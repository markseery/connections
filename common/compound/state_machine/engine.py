from __future__ import annotations

import hashlib
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

from .config_loader import OrchestratorConfig
from .event_bus import EventBus
from .models import DimensionSnapshot, MachineSnapshot, StateChangeEvent
from .sources import pull_source
from .storage_client import StateStorageClient
from .transitions import compute_state_id, state_changed

_logger = logging.getLogger(__name__)


class StateEngine:
    def __init__(
        self,
        config: OrchestratorConfig,
        *,
        storage: StateStorageClient | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.config = config
        self.storage = storage or StateStorageClient()
        self.bus = bus or EventBus(config)
        self._namespace = str(config.get("storage", "namespace") or "state_machines")
        self._events_ns = str(config.get("storage", "events_namespace") or "state_events")
        self._events_max = int(config.get("storage", "events_retention_max") or 500)
        self._timeout = float(config.get("defaults", "source_timeout_sec") or 25)
        self._last_pull: dict[str, float] = {}

    def list_machine_ids(self) -> list[str]:
        return self.config.load_symbols()

    def get_snapshot(self, machine_id: str) -> MachineSnapshot | None:
        raw = self.storage.get(self._namespace, machine_id.strip().upper())
        if not raw:
            return None
        return MachineSnapshot.from_storage(raw)

    def refresh_machine(self, machine_id: str, *, dimension: str | None = None) -> MachineSnapshot:
        mid = machine_id.strip().upper()
        mcfg = self.config.machine_config_for_symbol(mid)
        entity = mcfg.get("entity") if isinstance(mcfg.get("entity"), dict) else {"symbol": mid}
        dims_cfg = mcfg.get("dimensions")
        if not isinstance(dims_cfg, dict):
            raise ValueError(f"machine {mid} has no dimensions config")

        existing = self.get_snapshot(mid)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        snap = existing or MachineSnapshot(machine_id=mid, entity=entity, updated_at=now)

        targets = [dimension] if dimension else list(dims_cfg.keys())
        for dim_name in targets:
            if dim_name not in dims_cfg:
                continue
            dim_cfg = dims_cfg[dim_name]
            if not isinstance(dim_cfg, dict):
                continue
            self._refresh_dimension(snap, mid, entity, dim_name, dim_cfg, now)

        snap.updated_at = now
        self.storage.put(self._namespace, mid, snap.to_storage())
        return snap

    def _refresh_dimension(
        self,
        snap: MachineSnapshot,
        machine_id: str,
        entity: dict[str, str],
        dim_name: str,
        dim_cfg: dict[str, Any],
        now: str,
    ) -> None:
        source = dim_cfg.get("source")
        if not isinstance(source, dict):
            return
        adapter = str(source.get("adapter") or "")
        params = source.get("params") if isinstance(source.get("params"), dict) else {}
        compare = dim_cfg.get("compare") if isinstance(dim_cfg.get("compare"), dict) else {}
        initial = str(dim_cfg.get("initial_state_id") or "unknown")

        prev = snap.dimensions.get(dim_name)
        old_state_id = prev.state_id if prev else initial
        old_raw = dict(prev.raw) if prev and prev.raw else None

        try:
            new_raw = pull_source(adapter, entity, params, timeout_sec=self._timeout)
            pull_status = "ok"
            pull_error = None
        except Exception as exc:
            _logger.warning("pull failed %s.%s: %s", machine_id, dim_name, exc)
            refresh_cfg = dim_cfg.get("refresh")
            if not isinstance(refresh_cfg, dict):
                refresh_cfg = self.config.get("machine_defaults", "refresh")
            if not isinstance(refresh_cfg, dict):
                refresh_cfg = {}
            on_fail = str(refresh_cfg.get("on_failure") or "hold")
            if prev is None:
                snap.dimensions[dim_name] = DimensionSnapshot(
                    state_id=initial,
                    raw={},
                    pull_status="error",
                    pull_error=str(exc),
                    last_pull_at=now,
                )
                return
            if on_fail == "unknown":
                prev.pull_status = "error"
                prev.pull_error = str(exc)
                prev.last_pull_at = now
                snap.dimensions[dim_name] = prev
            else:
                prev.pull_status = "error"
                prev.pull_error = str(exc)
                prev.last_pull_at = now
                snap.dimensions[dim_name] = prev
            return

        new_state_id = compute_state_id(new_raw, compare=compare, initial_state_id=initial)
        changed = state_changed(old_raw, new_raw, old_state_id, new_state_id, compare)

        dim_snap = DimensionSnapshot(
            state_id=new_state_id,
            raw=new_raw,
            previous_state_id=old_state_id if changed else (prev.previous_state_id if prev else old_state_id),
            last_changed_at=now if changed else (prev.last_changed_at if prev else None),
            last_pull_at=now,
            pull_status=pull_status,
            pull_error=pull_error,
        )
        snap.dimensions[dim_name] = dim_snap

        if changed:
            corr = hashlib.sha256(
                f"{machine_id}:{dim_name}:{new_state_id}:{now}".encode()
            ).hexdigest()[:16]
            event = StateChangeEvent(
                machine_id=machine_id,
                dimension=dim_name,
                entity=entity,
                old_state_id=old_state_id,
                new_state_id=new_state_id,
                old_raw=old_raw,
                new_raw=new_raw,
                changed_at=datetime.now(timezone.utc),
                correlation_id=corr,
            )
            self.bus.publish(event)
            self.storage.append_event(
                self._events_ns,
                event.to_dict(),
                max_records=self._events_max,
            )

        self._last_pull[f"{machine_id}:{dim_name}"] = time.time()

    def due_machines(self) -> list[str]:
        symbols = self.list_machine_ids()
        md = self.config.get("machine_defaults", "refresh")
        if not isinstance(md, dict):
            return symbols
        interval = float(md.get("interval_sec") or 3600)
        jitter = float(md.get("jitter_sec") or 0)
        now = time.time()
        due: list[str] = []
        for sym in symbols:
            key = f"{sym}:__machine__"
            last = self._last_pull.get(key, 0.0)
            wait = interval + (random.uniform(0, jitter) if jitter > 0 else 0)
            if now - last >= wait:
                due.append(sym)
        return due

    def mark_machine_polled(self, machine_id: str) -> None:
        self._last_pull[f"{machine_id}:__machine__"] = time.time()

    def refresh_due(self, *, max_machines: int | None = None) -> list[str]:
        due = self.due_machines()
        if max_machines is not None and max_machines > 0:
            due = due[:max_machines]
        refreshed: list[str] = []
        for mid in due:
            try:
                self.refresh_machine(mid)
                self.mark_machine_polled(mid)
                refreshed.append(mid)
            except Exception as exc:
                _logger.exception("refresh_machine %s failed: %s", mid, exc)
        return refreshed
