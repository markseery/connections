"""
License: MIT
Description: Build a planning prompt and call the aiserver to produce a structured plan.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from common.json_repair import parse_llm_json
from .config import get_aiserver_url
from .context import AgentContext
from .models import AgentExecutionRequest, AgentPlan, PlannedStep
from .skill_discovery import SkillDefinition

PLANNING_INSTRUCTIONS = """You are a planner. Given a user request and available skills, produce a JSON execution plan.

Return ONLY a valid JSON object with this exact schema:
{"objective":"string","steps":[{"step_id":1,"skill_name":"exact_skill_name","method":"GET or POST","route_path_template":"/exact/path/or/{param}","reason":"why","arguments":{},"depends_on":[]}]}

RULES:
- skill_name MUST be the exact skill name from the skills list (exactly as shown after "skill_name=").
- depends_on MUST be a list of integer step_id values, e.g. [1] or [].
- Use only the listed skills and their exact routes. Return ONLY JSON, no markdown.
- Arguments MUST be valid JSON types. If an argument is a list of numbers, it MUST be a JSON array (e.g. {"values":[10,13,45,23]}), not a comma-separated string.
- Do NOT call routes that are tests (paths containing "/test") unless the user explicitly requests a test.
- Do NOT call "verification" or "listing" routes (e.g. GET /notifications, GET /stats, GET /config) unless the user explicitly asks to check status/history/config.
"""


def create_plan(
    request: AgentExecutionRequest,
    available_skills: list[SkillDefinition],
    context: AgentContext | None = None,
    aiserver_url: str | None = None,
) -> AgentPlan:
    """Call aiserver to produce an AgentPlan. Never fabricates; raises on failure."""
    base = (aiserver_url or get_aiserver_url()).rstrip("/")
    prompt_parts = [PLANNING_INSTRUCTIONS]

    skill_lines = []
    for s in available_skills:
        for r in s.routes:
            skill_lines.append(f"- skill_name={s.skill_name} route={r.method} {r.path} — {r.description}")
    prompt_parts.append("\nSkills:\n" + "\n".join(skill_lines))

    if context and context.scratchpad:
        prompt_parts.append(f"\nPrevious results: {json.dumps(context.scratchpad, default=str)}")
    if context and context.partial_results:
        failures = [f"step {r.step_id}: {getattr(r, 'error', r)}" for r in context.partial_results]
        if failures:
            prompt_parts.append("\nFailed steps: " + "; ".join(failures))

    prompt_parts.append(f"\nRequest: {request.prompt}")
    if request.system_prompt:
        prompt_parts.insert(1, f"\nSystem: {request.system_prompt}")
    prompt = "\n".join(prompt_parts)

    payload: dict[str, Any] = {"prompt": prompt, "profile": "reason"}
    if _trace_llm_enabled():
        _trace_llm("llm_request", {"url": f"{base}/generate", "payload": payload})
    with httpx.Client(timeout=120.0) as client:
        r = client.post(f"{base}/generate", json=payload)
        r.raise_for_status()
        data = r.json()

    output = data.get("output") or {}
    if isinstance(output, dict):
        text = output.get("text", "") or ""
    else:
        text = str(output)
    if not text:
        raise RuntimeError("AIServer returned no plan text")
    if _trace_llm_enabled():
        _trace_llm("llm_response", {"raw_text": text})

    raw = parse_llm_json(text)
    raw_steps = [s for s in (raw.get("steps") or []) if isinstance(s, dict)]
    for s in raw_steps:
        deps = s.get("depends_on") or []
        s["depends_on"] = [int(x) for x in deps if isinstance(x, (int, float))]
    plan = AgentPlan(
        objective=raw.get("objective", ""),
        steps=[PlannedStep(**s) for s in raw_steps],
    )
    return plan


def _trace_llm_enabled() -> bool:
    v = os.environ.get("AGENT_TRACE_LLM", "1").strip()
    return v not in {"0", "false", "False", "no", "NO"}


def _trace_llm(stage: str, payload: dict[str, Any]) -> None:
    try:
        print(f"[agent_llm] {json.dumps({'stage': stage, **payload}, default=str)}", flush=True)
    except Exception:
        print(f"[agent_llm] stage={stage}", flush=True)


