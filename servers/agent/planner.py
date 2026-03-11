"""
License: MIT
Description: Build a planning prompt and call the aiserver to produce a structured plan.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

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

    raw = _loads_json_object(text)
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


def _loads_json_object(text: str) -> dict[str, Any]:
    """
    Extract the first JSON object from model output (which may include prose or
    fenced code blocks) and parse it.  If the raw extraction fails json.loads,
    attempt common structural repairs before giving up.
    """
    t = text.strip()
    if not t:
        raise ValueError("Empty plan text")

    # Strip fenced code blocks.
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()

    candidate = _extract_brace_block(t)
    if candidate is None:
        raise ValueError("No JSON object found in plan text")

    # Happy path: valid JSON on first try.
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Repair pass: fix common LLM mistakes before giving up.
    repaired = _repair_json(candidate)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    raise ValueError(f"Could not parse JSON from plan text: {candidate[:200]}")


def _extract_brace_block(t: str) -> str | None:
    """Find the first top-level {...} block using brace-depth tracking."""
    start = t.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "\"":
                in_str = False
            continue
        if ch == "\"":
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return t[start : i + 1]
    return None


def _repair_json(text: str) -> str:
    """
    Attempt to fix common LLM JSON mistakes by rebuilding the plan from parts.

    The most frequent error: step fields (especially "depends_on") end up outside
    the step object but still inside the steps array, producing invalid JSON like:
        "steps":[{...step...},"depends_on":[]]

    Strategy: extract "objective" and each step-like {...} from the text, merge any
    orphaned key:value pairs back into the preceding step, and reassemble.
    """
    # Extract objective.
    m = re.search(r'"objective"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    objective = m.group(1) if m else ""

    # Find all {...} blocks inside the "steps" array.
    steps_match = re.search(r'"steps"\s*:\s*\[', text)
    if not steps_match:
        return text

    remainder = text[steps_match.end():]
    step_objects: list[dict[str, Any]] = []

    pos = 0
    while pos < len(remainder):
        # Find next { that starts a step object.
        brace_start = remainder.find("{", pos)
        if brace_start == -1:
            # Check for orphaned key:value pairs between last } and ]
            tail = remainder[pos:]
            _absorb_orphans(tail, step_objects)
            break

        # Check for orphaned "key":value between previous } and this {
        gap = remainder[pos:brace_start]
        _absorb_orphans(gap, step_objects)

        # Extract the {...} block.
        block = _extract_brace_block(remainder[brace_start:])
        if block is None:
            break
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                step_objects.append(obj)
        except json.JSONDecodeError:
            pass
        pos = brace_start + len(block)

    # Reassemble clean JSON.
    plan = {"objective": objective, "steps": step_objects}
    return json.dumps(plan, ensure_ascii=False)


def _absorb_orphans(gap: str, steps: list[dict[str, Any]]) -> None:
    """
    Scan a text gap between step objects for orphaned "key":value pairs
    and merge them into the last step.
    """
    if not steps:
        return
    for m in re.finditer(r'"(\w+)"\s*:\s*(\[[^\]]*\]|"(?:[^"\\]|\\.)*"|\d+|true|false|null)', gap):
        key = m.group(1)
        val_str = m.group(2)
        try:
            val = json.loads(val_str)
        except Exception:
            continue
        steps[-1][key] = val
