"""
State machine skill — read snapshots and trigger refresh via the state server or in-process engine.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.compound.http_client import http_client
from common.compound.registry_client import get_server_url
from common.compound.skill_config import SkillConfig
from common.compound.state_machine.config_loader import OrchestratorConfig
from common.compound.state_machine.engine import StateEngine
from common.simple.skill_response import skill_result

_conf = SkillConfig("state_machine_skill")
router = APIRouter()


def _state_base_url() -> str:
    explicit = os.environ.get("STATE_SERVER_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    try:
        return get_server_url("state").rstrip("/")
    except Exception:
        return ""


def _engine() -> StateEngine:
    return StateEngine(OrchestratorConfig.load())


class RefreshBody(BaseModel):
    symbol: str = Field(..., min_length=1)
    dimension: str | None = None
    local: bool = Field(
        default=False,
        description="Run refresh in-process instead of calling the state server.",
    )


@router.get("/machines")
def list_machines() -> dict[str, Any]:
    base = _state_base_url()
    if base:
        with http_client("inter_service") as client:
            r = client.get(f"{base}/state/machines")
            r.raise_for_status()
            return r.json()
    eng = _engine()
    ids = eng.list_machine_ids()
    return skill_result(
        summary=f"{len(ids)} symbol state machines configured.",
        machines=[{"machine_id": m} for m in ids],
    )


@router.get("/machines/{symbol}")
def get_machine(symbol: str) -> dict[str, Any]:
    mid = symbol.strip().upper()
    base = _state_base_url()
    if base:
        with http_client("inter_service") as client:
            r = client.get(f"{base}/state/machines/{mid}")
            if r.status_code == 404:
                raise HTTPException(status_code=404, detail="snapshot not found")
            r.raise_for_status()
            return r.json()
    snap = _engine().get_snapshot(mid)
    if snap is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return skill_result(summary=f"State for {mid}", snapshot=snap.to_storage())


@router.post("/refresh")
def refresh_symbol(body: RefreshBody) -> dict[str, Any]:
    mid = body.symbol.strip().upper()
    if body.local or not _state_base_url():
        try:
            snap = _engine().refresh_machine(mid, dimension=body.dimension)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return skill_result(summary=f"Refreshed {mid}", snapshot=snap.to_storage())
    base = _state_base_url()
    payload: dict[str, Any] = {}
    if body.dimension:
        payload["dimension"] = body.dimension
    with http_client("inter_service") as client:
        r = client.post(f"{base}/state/machines/{mid}/refresh", json=payload)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        data = r.json()
    return skill_result(summary=f"Refreshed {mid} via state server", snapshot=data)
