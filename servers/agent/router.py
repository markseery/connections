"""
License: MIT
Description: Router: decide with high confidence whether the user request matches
one or more skills. Only when true do we invoke the planner. Otherwise prompt
goes to AI with memory (direct answer path).
"""

from __future__ import annotations

from typing import Any

import httpx

from common.json_repair import parse_llm_json_or_none

from .config import get_aiserver_url
from .skill_discovery import SkillDefinition

ROUTER_INSTRUCTIONS = """You are a router. Given a user message and a list of available skills (name + route + short description), decide: is there a HIGH CONFIDENCE match to one or more of these skills?

Use a high bar. Only answer use_skills: true if the user is clearly and specifically asking for something a listed skill does (e.g. get news, stock quote, run workflow, list help, send email). 
Answer use_skills: false for: greetings, thanks, chitchat, vague or open-ended prompts, or when you are not confident. When in doubt, say false.

Return ONLY a valid JSON object: {"use_skills": true} or {"use_skills": false}
No markdown, no explanation."""


def use_skills_high_confidence(
    prompt: str,
    skills: list[SkillDefinition],
    aiserver_url: str | None = None,
) -> bool:
    """
    One AI call: should we invoke the planner (use skills) or send prompt to AI with memory (direct)?
    Returns True only when high-confidence skill match. On failure or parse error, returns True
    so we keep existing behavior (invoke planner).
    """
    prompt = (prompt or "").strip()
    if not prompt or not skills:
        return False

    skill_lines = []
    for s in skills:
        for r in s.routes:
            skill_lines.append(f"- {s.skill_name} {r.method} {r.path} — {r.description}")
    skills_text = "\n".join(skill_lines)

    router_prompt = (
        ROUTER_INSTRUCTIONS
        + "\n\nSkills:\n"
        + skills_text
        + "\n\nUser message:\n"
        + prompt
    )

    base = (aiserver_url or get_aiserver_url()).rstrip("/")
    payload: dict[str, Any] = {"prompt": router_prompt, "profile": "agent"}
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(f"{base}/generate", json=payload)
            r.raise_for_status()
            data = r.json() or {}
        output = data.get("output") or {}
        if isinstance(output, dict):
            text = (output.get("text") or "").strip()
        else:
            text = str(output).strip()
        if not text:
            return True
        raw = parse_llm_json_or_none(text)
        if not isinstance(raw, dict):
            return True
        return raw.get("use_skills") is True
    except Exception as exc:
        print(f"[agent] router failed, defaulting to use_skills=True: {exc}", flush=True)
        return True
