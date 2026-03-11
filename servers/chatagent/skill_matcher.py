"""
License: MIT
Description: Skill discovery + simple prompt matching + argument extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from common.json_repair import parse_llm_json_or_none
from .config import get_aiserver_url, get_config_url, get_registry_url


@dataclass
class SkillRoute:
    method: str
    path: str
    description: str = ""


@dataclass
class SkillDefinition:
    skill_name: str
    base_url: str
    routes: list[SkillRoute]


@dataclass
class SkillCall:
    skill_name: str
    base_url: str
    method: str
    path: str
    arguments: dict[str, Any]


def discover_skills() -> list[SkillDefinition]:
    config_url = get_config_url()
    registry_url = get_registry_url()
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{config_url}/configs")
        r.raise_for_status()
        keys = (r.json() or {}).get("keys") or []

    skills: list[SkillDefinition] = []
    for key in keys:
        if not isinstance(key, str) or not key.startswith("skill:"):
            continue
        name = key.split(":", 1)[1].strip()
        if not name:
            continue
        with httpx.Client(timeout=5.0) as client:
            rr = client.get(f"{config_url}/configs/skill/{name}")
            if rr.status_code == 404:
                continue
            rr.raise_for_status()
            rec = rr.json() or {}
        value = rec.get("value") if isinstance(rec.get("value"), dict) else rec
        if not isinstance(value, dict):
            continue
        base_url = str(value.get("base_url") or "").strip().rstrip("/")
        if not base_url and value.get("server_name"):
            server_name = str(value.get("server_name") or "").strip()
            if server_name:
                with httpx.Client(timeout=5.0) as client:
                    s = client.get(f"{registry_url}/servers/{server_name}")
                    if s.status_code == 200:
                        base_url = str((s.json() or {}).get("url") or "").strip().rstrip("/")
        if not base_url:
            continue
        routes: list[SkillRoute] = []
        for ro in value.get("routes") or []:
            if isinstance(ro, dict) and ro.get("method") and ro.get("path"):
                routes.append(
                    SkillRoute(
                        method=str(ro["method"]).upper(),
                        path=str(ro["path"]),
                        description=str(ro.get("description") or ""),
                    )
                )
        skills.append(SkillDefinition(skill_name=name, base_url=base_url, routes=routes))
    return skills


def match_skill(prompt: str, skills: list[SkillDefinition]) -> SkillCall | None:
    # Skill choice + arguments are decided by the AI model, not heuristics.
    # This keeps the "skill match" behavior robust and extensible as skills change.
    p = prompt.strip()
    if not p:
        return None
    return decide_skill_call(prompt=p, skills=skills)


def decide_skill_call(prompt: str, skills: list[SkillDefinition], *, profile: str = "reason") -> SkillCall | None:
    """
    Ask the AI server to decide whether to use a skill. Returns a SkillCall when the
    model chooses a valid known skill route; otherwise returns None.
    """
    if not skills:
        return None

    options: list[dict[str, Any]] = []
    for s in skills:
        for r in s.routes:
            options.append(
                {
                    "skill_name": s.skill_name,
                    "method": r.method,
                    "path": r.path,
                    "description": r.description,
                }
            )

    instruction = (
        "You are a router for a tool-using chat system.\n"
        "Given a user prompt and a list of available skill routes, decide if any route should be called.\n\n"
        "Return ONLY a valid JSON object with this schema:\n"
        "{\n"
        '  "use_skill": true|false,\n'
        '  "skill_name": "exact skill_name from options" | null,\n'
        '  "method": "GET|POST" | null,\n'
        '  "path": "/exact/route/path" | null,\n'
        '  "arguments": { } \n'
        "}\n\n"
        "Rules:\n"
        "- If no skill is clearly needed, set use_skill=false.\n"
        "- If use_skill=true, skill_name/method/path MUST exactly match one of the options.\n"
        "- arguments MUST be valid JSON types (use arrays for lists of numbers).\n"
        "- Do NOT choose routes that are tests or status/config listings unless the user explicitly asks.\n\n"
        f"Options:\n{json.dumps(options, ensure_ascii=False)}\n\n"
        f"Prompt: {prompt}\n"
    )

    base = get_aiserver_url()
    with httpx.Client(timeout=30.0) as client:
        r = client.post(f"{base}/generate", json={"prompt": instruction, "profile": profile})
        if r.status_code != 200:
            return None
        data = r.json() or {}
        text = ((data.get("output") or {}).get("text")) if isinstance(data.get("output"), dict) else None
        if not isinstance(text, str) or not text.strip():
            return None

    raw = parse_llm_json_or_none(text)
    if not isinstance(raw, dict):
        return None
    if raw.get("use_skill") is not True:
        return None
    skill_name = raw.get("skill_name")
    method = raw.get("method")
    path = raw.get("path")
    arguments = raw.get("arguments")
    if not isinstance(skill_name, str) or not isinstance(method, str) or not isinstance(path, str):
        return None
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return None

    # Validate selection against discovered skills (exact match).
    selected = next((s for s in skills if s.skill_name == skill_name), None)
    if not selected:
        return None
    route = _find_route(selected, method, path)
    if not route:
        return None

    return SkillCall(
        skill_name=selected.skill_name,
        base_url=selected.base_url,
        method=route.method,
        path=route.path,
        arguments=arguments,
    )


def _find_route(skill: SkillDefinition, method: str, path: str) -> SkillRoute | None:
    for r in skill.routes:
        if r.method.upper() == method.upper() and r.path == path:
            return r
    return None


def _extract_numbers(text: str) -> list[float]:
    # Accept integers/floats with optional commas.
    raw = re.findall(r"-?\\d+(?:\\.\\d+)?", text)
    out: list[float] = []
    for t in raw:
        try:
            out.append(float(t))
        except Exception:
            continue
    return out


def _extract_email(text: str) -> list[str] | None:
    m = re.search(r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,})", text, flags=re.IGNORECASE)
    if not m:
        return None
    return [m.group(1)]


def _extract_field(prompt: str, field: str) -> str | None:
    # Parse patterns like "field: value" until next "word:" marker.
    m = re.search(rf"{re.escape(field)}\\s*:\\s*(.+)", prompt, flags=re.IGNORECASE)
    if not m:
        return None
    tail = m.group(1).strip()
    # stop at next marker like "subject:" / "body:" / "to:"
    tail = re.split(r"\\b\\w+\\s*:\\s*", tail, maxsplit=1)[0].strip()
    if (tail.startswith("'") and tail.endswith("'")) or (tail.startswith('"') and tail.endswith('"')):
        tail = tail[1:-1].strip()
    return tail or None

