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
from common.skill_response import skill_response_to_markdown
from servers.agent.context import AgentContext
from servers.agent.executor import execute_plan
from servers.agent.models import AgentExecutionRequest, AgentPlan, PlannedStep, StepResult
from servers.agent.planner import create_plan


router = APIRouter(prefix="/chat", tags=["chatagent"])


def _format_step_failures(step_results: list[StepResult]) -> str:
    """Format failed step results for display when no step succeeded."""
    lines = ["**Skill execution failed** — no step returned success.\n"]
    for sr in step_results:
        err = sr.error or str(sr.status_code)
        lines.append(f"- **Step {sr.step_id}** ({sr.skill_name}): {sr.status_code} — {err[:300]}")
    return "\n".join(lines)


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
    except Exception as exc:
        print(f"[chatagent] skill lifecycle FAILED: {exc}", flush=True)
        skills = []

    # Ask the planner whether any skill should be used.
    if skills:
        req = AgentExecutionRequest(prompt=prompt.strip())
        ctx = AgentContext(request_id=str(uuid4()))
        try:
            plan = create_plan(request=req, available_skills=skills, context=ctx)
        except Exception as exc:
            print(f"[chatagent] planner FAILED: {exc}", flush=True)
            plan = None

        if plan and plan.steps:
            print(
                f"[chatagent] plan: {[(s.skill_name, s.method, s.route_path_template) for s in plan.steps]}",
                flush=True,
            )
            try:
                plan = _sanitize_plan(plan, skills)
                if not plan.steps:
                    print("[chatagent] plan empty after sanitize — falling back to AI", flush=True)
                else:
                    for step in plan.steps:
                        if step.arguments is None:
                            step.arguments = {}
                        step.arguments["prompt"] = prompt.strip()
                    step_results = execute_plan(
                        plan=plan, context=ctx, skills=skills, timeout_seconds=60.0
                    )
                    for sr in step_results:
                        print(
                            f"[chatagent] step result: skill={sr.skill_name} "
                            f"path={sr.path} status={sr.status_code} error={sr.error}",
                            flush=True,
                        )
                    ok = [r for r in step_results if not r.error and r.status_code == 200]
                    if ok:
                        primary = ok[0]
                        out = primary.response_data
                        return {
                            "namespace": namespace,
                            "profile": profile,
                            "prompt": prompt,
                            "output": {"text": skill_response_to_markdown(out)},
                            "used": {
                                "type": "skill",
                                "skill_name": primary.skill_name,
                                "path": primary.path,
                                "method": primary.method,
                            },
                            "raw": out,
                        }
                    else:
                        primary = step_results[0]
                        return {
                            "namespace": namespace,
                            "profile": profile,
                            "prompt": prompt,
                            "output": {"text": _format_step_failures(step_results)},
                            "used": {
                                "type": "skill",
                                "skill_name": primary.skill_name,
                                "path": primary.path,
                                "method": primary.method,
                            },
                            "raw": {
                                "success": False,
                                "step_results": [sr.model_dump(mode="json") for sr in step_results],
                            },
                        }
            except Exception as exc:
                print(f"[chatagent] plan execution FAILED: {exc}", flush=True)
                return {
                    "namespace": namespace,
                    "profile": profile,
                    "prompt": prompt,
                    "output": {"text": f"**Skill execution failed**\n\n{exc!s}"},
                    "used": {"type": "skill"},
                    "raw": {"success": False, "error": str(exc)},
                }
        else:
            print("[chatagent] planner returned no steps — falling back to AI", flush=True)

    # AI fallback — only when no skill was selected or no steps were executed
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
    """Validate steps against discovered routes.  When the planner
    hallucinated an invalid route for a valid skill, try to repair it
    by matching the trailing path segment to an actual route template."""
    import re as _re

    skills_by_name = {getattr(s, "skill_name"): s for s in skills}

    kept: list[PlannedStep] = []
    for step in plan.steps:
        skill = skills_by_name.get(step.skill_name)
        if not skill:
            continue

        path = step.route_path_template.lower()
        if "/test" in path:
            continue
        if step.method.upper() == "GET" and any(
            x in path for x in ["/stats", "/config", "/notifications"]
        ):
            continue

        if route_exists(skill, step.method, step.route_path_template):
            kept.append(step)
            continue

        fixed = _try_fix_route(step, skill)
        if fixed:
            print(
                f"[chatagent] route fix: {step.route_path_template} → {fixed.route_path_template}",
                flush=True,
            )
            kept.append(fixed)

    kept_ids = {s.step_id for s in kept}
    cleaned: list[PlannedStep] = []
    for s in kept:
        deps = [d for d in (s.depends_on or []) if d in kept_ids]
        cleaned.append(s.model_copy(update={"depends_on": deps}))

    return plan.model_copy(update={"steps": cleaned})


def _try_fix_route(step: PlannedStep, skill: Any) -> PlannedStep | None:
    """Try to match a hallucinated route to a real one on the same skill.
    E.g. /skills/news_skill/news/CRWV → /skills/news_skill/stock/CRWV"""
    import re as _re

    hallucinated = step.route_path_template
    parts = hallucinated.rstrip("/").split("/")
    tail_value = parts[-1] if parts else ""

    for route in skill.routes:
        if route.method.upper() != step.method.upper():
            continue
        tpl = route.path
        param_names = _re.findall(r"\{(\w+)\}", tpl)
        if len(param_names) == 1 and tail_value:
            fixed_path = _re.sub(r"\{" + param_names[0] + r"\}", tail_value, tpl)
            return step.model_copy(update={"route_path_template": fixed_path})
        if not param_names:
            return step.model_copy(update={"route_path_template": tpl})

    return None
