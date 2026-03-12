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

PLANNING_INSTRUCTIONS = """You are a planner. Given a user request and a FIXED list of available skills, produce a JSON execution plan.

ABSOLUTELY CRITICAL — READ THIS FIRST:
You may ONLY use skills and routes that appear in the "Skills:" list below.
DO NOT hallucinate, guess, or invent skill names or route paths.
If a skill or route is NOT in the list, it DOES NOT EXIST. Do not use it.
Every skill_name and route_path_template you output MUST be copied from the list.

Return ONLY a valid JSON object with this schema:
{"objective":"<goal>","steps":[{"step_id":1,"skill_name":"<skill>","method":"GET or POST","route_path_template":"<route path with params filled in>","reason":"<why>","arguments":{},"depends_on":[]}]}

ROUTE SELECTION — how to fill route_path_template:
1. Find the skill in the list that best matches the user's request.
2. Copy that route's path EXACTLY as written.
3. Replace ONLY {param} placeholders with actual values from the user's request.
4. Do NOT modify, shorten, rearrange, or invent any part of the path.

Examples:
  Listed route: GET /skills/news_skill/stock/{symbol}
  User asks about CRWV → "route_path_template": "/skills/news_skill/stock/CRWV"

  Listed route: POST /skills/statistics/mean
  User asks for mean → "route_path_template": "/skills/statistics/mean"

WORKFLOW ROUTING (MANDATORY):
- If the user asks to "run workflow <name>" or "execute workflow <name>", you MUST select `workflow_skill`.
- Use `POST /skills/workflow_skill/run/{name}` with `{name}` filled in from the user's text.
- Put the workflow parameters in `arguments` (e.g. url/pages/depth/timeout).
- Do NOT invent new routes and do NOT suggest CLI commands/scripts.

RULES:
- skill_name MUST exactly match a "skill_name=" value from the skills list.
- route_path_template MUST exactly match a listed route path (with {param} replaced).
- If NO skill in the list can handle the request, return {"objective":"...","steps":[]}.
- depends_on MUST be a list of integer step_id values, e.g. [1] or [].
- Return ONLY JSON, no markdown, no explanation.
- Arguments MUST be valid JSON types (arrays not comma-separated strings).
- Do NOT call test routes (paths containing "/test") unless explicitly requested.
- Do NOT call listing/status routes (e.g. GET /notifications, GET /stats, GET /config) unless explicitly requested.
- ONLY include steps that DIRECTLY answer the user's request. Do NOT add extra steps (like listing skills, showing help, etc.) unless the user explicitly asked for them. One request = one step in most cases.
- Do NOT output instructions, shell commands, or "here's what you should run". Your output must be a plan that actually calls skills.

FINAL REMINDER: Only use skills and routes from the list. Nothing else exists.
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

    payload: dict[str, Any] = {"prompt": prompt, "profile": "agent"}
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
    except Exception as exc:
        print(f"[agent_llm] stage={stage} (json encode failed: {exc})", flush=True)


