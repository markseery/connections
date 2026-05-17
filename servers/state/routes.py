"""
License: MIT
Description: HTTP routes for state machine snapshots, refresh, and events.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.compound.state_machine.config_loader import OrchestratorConfig
from common.compound.state_machine.engine import StateEngine
from common.compound.state_machine.scheduler import StateScheduler
from common.compound.state_machine.storage_client import StateStorageClient

router = APIRouter(prefix="/state", tags=["state"])

_lock = threading.Lock()
_config: OrchestratorConfig | None = None
_engine: StateEngine | None = None
_scheduler: StateScheduler | None = None


def _orch() -> OrchestratorConfig:
    global _config
    with _lock:
        if _config is None:
            _config = OrchestratorConfig.load()
        return _config


def _engine() -> StateEngine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = StateEngine(_orch())
        return _engine


def _scheduler() -> StateScheduler:
    global _scheduler
    with _lock:
        if _scheduler is None:
            _scheduler = StateScheduler(_engine(), _orch())
        return _scheduler


def startup_scheduler() -> None:
    _scheduler().start()


def shutdown_scheduler() -> None:
    global _config, _engine, _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.stop()
            _scheduler = None
        _engine = None
        _config = None


def reload_config() -> None:
    global _config, _engine, _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.stop()
        _config = OrchestratorConfig.load()
        _engine = StateEngine(_config)
        _scheduler = StateScheduler(_engine, _config)
        _scheduler.start()


class RefreshRequest(BaseModel):
    dimension: str | None = Field(
        default=None,
        description="Refresh only this dimension (default: all configured dimensions).",
    )


@router.get("/machines")
def list_machines() -> dict[str, Any]:
    eng = _engine()
    ids = eng.list_machine_ids()
    snaps = []
    for mid in ids:
        s = eng.get_snapshot(mid)
        snaps.append(
            {
                "machine_id": mid,
                "has_snapshot": s is not None,
                "updated_at": s.updated_at if s else None,
            }
        )
    return {"count": len(ids), "machines": snaps}


@router.get("/machines/{machine_id}")
def get_machine(machine_id: str) -> dict[str, Any]:
    snap = _engine().get_snapshot(machine_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="machine snapshot not found")
    return snap.to_storage()


@router.post("/machines/{machine_id}/refresh")
def refresh_machine(machine_id: str, body: RefreshRequest | None = None) -> dict[str, Any]:
    dim = body.dimension if body else None
    try:
        snap = _engine().refresh_machine(machine_id, dimension=dim)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return snap.to_storage()


@router.post("/refresh-due")
def refresh_due() -> dict[str, Any]:
    refreshed = _scheduler().tick_once()
    return {"refreshed": refreshed, "count": len(refreshed)}


@router.get("/events")
def list_events(limit: int = 50) -> dict[str, Any]:
    cfg = _orch()
    ns = str(cfg.get("storage", "events_namespace") or "state_events")
    raw = StateStorageClient().get(ns, "events") or {}
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    if limit > 0:
        items = items[-limit:]
    return {"count": len(items), "events": items}


@router.post("/reload")
def reload() -> dict[str, str]:
    reload_config()
    return {"status": "reloaded"}
