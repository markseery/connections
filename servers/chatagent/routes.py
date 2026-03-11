"""
License: MIT
Description: ChatAgent routes: skill-first, else AI fallback.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException

from .config import get_aiserver_url
from common.skill_lifecycle import SkillLifecycle, route_exists
from servers.agent.context import AgentContext
from servers.agent.executor import execute_plan
from servers.agent.models import AgentExecutionRequest, AgentPlan, PlannedStep
from servers.agent.planner import create_plan


router = APIRouter(prefix="/chat", tags=["chatagent"])


def _default_namespace() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"guest_{ts}"


@router.post("")
def chat(body: dict[str, Any]) -> dict[str, Any]:
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    profile = body.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        profile = "fast"
    namespace = body.get("namespace")
    if not isinstance(namespace, str) or not namespace.strip():
        namespace = _default_namespace()

    # Full skill lifecycle: find worker → load skills → register config.
    lifecycle = SkillLifecycle()
    try:
        skills = lifecycle.prepare()
    except Exception:
        skills = []

    # Ask the planner whether any skill should be used.
    if skills:
        req = AgentExecutionRequest(prompt=prompt.strip())
        ctx = AgentContext(request_id=str(uuid4()))
        try:
            plan = create_plan(request=req, available_skills=skills, context=ctx)
        except Exception:
            plan = None

        if plan and plan.steps:
            try:
                plan = _sanitize_plan(plan, skills)
                if plan.steps:
                    step_results = execute_plan(
                        plan=plan, context=ctx, skills=skills, timeout_seconds=60.0
                    )
                    ok = [r for r in step_results if not r.error and r.status_code == 200]
                    if ok:
                        last = ok[-1]
                        out = last.response_data
                        return {
                            "namespace": namespace,
                            "profile": profile,
                            "prompt": prompt,
                            "output": {"text": _skill_to_text(last.skill_name, out)},
                            "used": {
                                "type": "skill",
                                "skill_name": last.skill_name,
                                "path": last.path,
                                "method": last.method,
                            },
                            "raw": out,
                        }
            except Exception:
                pass

    # AI fallback
    base = get_aiserver_url()
    payload: dict[str, Any] = {"prompt": prompt.strip(), "profile": profile.strip()}
    try:
        with httpx.Client(timeout=120.0) as client:
            r = client.post(f"{base}/generate", json=payload)
            r.raise_for_status()
            ai = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return {
        "namespace": namespace,
        "profile": profile,
        "prompt": prompt,
        **ai,
        "used": {"type": "ai"},
    }


def _sanitize_plan(plan: AgentPlan, skills: list[Any]) -> AgentPlan:
    """Drop steps that don't match a discovered route or are test/listing routes."""
    skills_by_name = {getattr(s, "skill_name"): s for s in skills}

    kept: list[PlannedStep] = []
    for step in plan.steps:
        skill = skills_by_name.get(step.skill_name)
        if not skill:
            continue
        if not route_exists(skill, step.method, step.route_path_template):
            continue
        path = step.route_path_template.lower()
        if "/test" in path:
            continue
        if step.method.upper() == "GET" and any(
            x in path for x in ["/stats", "/config", "/notifications"]
        ):
            continue
        kept.append(step)

    kept_ids = {s.step_id for s in kept}
    cleaned: list[PlannedStep] = []
    for s in kept:
        deps = [d for d in (s.depends_on or []) if d in kept_ids]
        cleaned.append(s.model_copy(update={"depends_on": deps}))

    return plan.model_copy(update={"steps": cleaned})


def _skill_to_text(skill_name: str, payload: Any) -> str:
    if isinstance(payload, dict):
        if skill_name == "statistics":
            for k in ["mean", "median", "stddev", "average"]:
                if k in payload:
                    return f"{k}: {payload[k]}"
        if "status" in payload and "notification_id" in payload:
            return f"notification sent: {payload.get('notification_id')}"
        return str(payload)
    return str(payload)
