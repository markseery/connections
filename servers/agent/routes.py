"""
License: MIT
Description: HTTP routes for the agent server: execute, plan-only, job status.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .models import AgentExecutionRequest
from .service import AgentService

router = APIRouter(prefix="/agent", tags=["agent"])
_service: AgentService | None = None


def get_service() -> AgentService:
    global _service
    if _service is None:
        _service = AgentService()
    return _service


@router.post("/execute")
def execute(body: AgentExecutionRequest) -> dict[str, Any]:
    """Run full execution: discover skills, plan (or cache), execute, return result."""
    try:
        return get_service().execute(body)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/plan")
def plan_only(body: AgentExecutionRequest) -> dict[str, Any]:
    """Return a plan only, without executing."""
    try:
        return get_service().plan_only(body)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/jobs/{request_id}")
def get_job(request_id: str) -> dict[str, Any]:
    """Return job state by request_id."""
    job = get_service().get_job(request_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    out = job.model_dump(mode="json")
    if job.result:
        out["result"] = job.result.model_dump(mode="json")
    return out
