"""
HTTP routes for the autonomous agent server.

Goals, approvals, and memory management endpoints per the design doc.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from common.agent_config import AgentConfigLoader
from common.agent_memory import MemoryManager
from common.approval_gate import ApprovalGate

from .models import AgentConfig, GoalSubmitRequest, GoalSubmitResponse
from .supervisor import SupervisorAgent, _get_storage_url

_conf = AgentConfigLoader("supervisor")

router = APIRouter(tags=["autonomous"])
_supervisor: SupervisorAgent | None = None


def _get_supervisor() -> SupervisorAgent:
    global _supervisor
    if _supervisor is None:
        _supervisor = SupervisorAgent()
    return _supervisor


def _get_gate() -> ApprovalGate:
    policy = _conf.get("approval_policy", "approve_irreversible")
    return ApprovalGate(policy=policy, storage_url=_get_storage_url())


# -- Goal endpoints --

@router.post("/goals/submit")
def submit_goal(body: GoalSubmitRequest) -> GoalSubmitResponse:
    sup = _get_supervisor()
    state = sup.submit_goal(goal=body.goal, config=body.config)
    return GoalSubmitResponse(
        goal_id=state.goal_id,
        status=state.status.value,
        message="Goal submitted and running in background.",
    )


@router.get("/goals/{goal_id}")
def get_goal(goal_id: str) -> dict[str, Any]:
    state = _get_supervisor().get_goal(goal_id)
    if not state:
        raise HTTPException(status_code=404, detail="Goal not found")
    return state.model_dump(mode="json")


@router.get("/goals")
def list_goals() -> dict[str, Any]:
    goals = _get_supervisor().list_goals()
    return {
        "goals": [g.model_dump(mode="json") for g in goals],
        "count": len(goals),
    }


@router.post("/goals/{goal_id}/cancel")
def cancel_goal(goal_id: str) -> dict[str, Any]:
    state = _get_supervisor().cancel_goal(goal_id)
    if not state:
        raise HTTPException(status_code=404, detail="Goal not found")
    return state.model_dump(mode="json")


# -- Approval endpoints --

@router.get("/approvals/pending")
def list_pending_approvals() -> dict[str, Any]:
    gate = _get_gate()
    pending = gate.list_pending()
    return {
        "approvals": [a.model_dump(mode="json") for a in pending],
        "count": len(pending),
    }


@router.post("/approvals/{approval_id}/approve")
def approve_action(approval_id: str) -> dict[str, Any]:
    gate = _get_gate()
    req = gate.approve(approval_id)
    if not req:
        raise HTTPException(status_code=404, detail="Approval not found")
    return req.model_dump(mode="json")


@router.post("/approvals/{approval_id}/deny")
def deny_action(approval_id: str) -> dict[str, Any]:
    gate = _get_gate()
    req = gate.deny(approval_id)
    if not req:
        raise HTTPException(status_code=404, detail="Approval not found")
    return req.model_dump(mode="json")


# -- Memory endpoints --

@router.get("/memory/{agent_id}/episodes")
def list_episodes(agent_id: str) -> dict[str, Any]:
    mm = MemoryManager(agent_id=agent_id, storage_url=_get_storage_url())
    episodes = mm.get_recent_episodes(
        limit=_conf.get("memory.episodic_max_entries", 100),
    )
    return {
        "episodes": [e.model_dump(mode="json") for e in episodes],
        "count": len(episodes),
    }


@router.get("/memory/{agent_id}/semantic")
def list_semantic(agent_id: str) -> dict[str, Any]:
    mm = MemoryManager(agent_id=agent_id, storage_url=_get_storage_url())
    entries = mm.get_all_semantic()
    return {
        "entries": [e.model_dump(mode="json") for e in entries],
        "count": len(entries),
    }


@router.delete("/memory/{agent_id}")
def clear_memory(agent_id: str) -> dict[str, Any]:
    mm = MemoryManager(agent_id=agent_id, storage_url=_get_storage_url())
    deleted = mm.clear_all()
    return {"deleted": deleted, "agent_id": agent_id}
