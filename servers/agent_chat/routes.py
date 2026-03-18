"""
License: MIT
Description: Agent chat API. POST /chat with namespace and prompt; uses storage
for current_memory and the agent server (planner + executor) for skills or direct answer.
Memory lifecycle unchanged: load/ensure current_memory, pass as conversation_context,
append exchange and save.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException

from common.skill_response import skill_response_to_markdown

from .config import get_registry_url

router = APIRouter(prefix="/chat", tags=["chat"])

AGENT_EXECUTE_TIMEOUT = 180.0
STORAGE_TIMEOUT = 15.0
CURRENT_MEMORY_KEY = "current_memory"
RECORD_TYPE = "current_memory"
DEFAULT_PROFILE = "agent"


def _agent_url() -> str:
    reg = get_registry_url()
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{reg}/servers/agent")
        r.raise_for_status()
        u = (r.json() or {}).get("url", "").strip().rstrip("/")
        if not u:
            raise HTTPException(status_code=503, detail="Registry has no agent URL")
        return u


def _storage_url() -> str:
    reg = get_registry_url()
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{reg}/servers/storage")
        r.raise_for_status()
        u = (r.json() or {}).get("url", "").strip().rstrip("/")
        if not u:
            raise HTTPException(status_code=503, detail="Registry has no storage URL")
        return u


def _call_agent_server(
    agent_url: str, prompt: str, conversation_context: str | None = None
) -> dict[str, Any]:
    """POST /agent/execute with prompt and optional conversation_context. Returns agent result."""
    payload: dict[str, Any] = {"prompt": prompt}
    if conversation_context:
        payload["conversation_context"] = conversation_context
    with httpx.Client(timeout=AGENT_EXECUTE_TIMEOUT) as client:
        r = client.post(f"{agent_url}/agent/execute", json=payload)
        r.raise_for_status()
    return r.json()


def _memory_record(namespace: str, attributes: list[dict]) -> dict:
    return {
        "recordType": RECORD_TYPE,
        "namespace": namespace,
        "attributes": attributes,
    }


def _format_memory_context(attributes: list[dict]) -> str:
    if not attributes:
        return ""
    parts = []
    for a in attributes:
        dt = a.get("datetime", "")
        mem = a.get("memory", "")
        if mem:
            parts.append(f"[{dt}]\n{mem}")
    return "\n\n".join(parts)


def _get_current_memory(storage_url: str, namespace: str) -> dict | None:
    ns = quote(namespace.strip(), safe="")
    key = quote(CURRENT_MEMORY_KEY, safe="")
    url = f"{storage_url}/namespaces/{ns}/records/{key}"
    with httpx.Client(timeout=STORAGE_TIMEOUT) as client:
        r = client.get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
    data = r.json()
    val = data.get("value")
    return val if isinstance(val, dict) else None


def _put_current_memory(storage_url: str, namespace: str, record: dict) -> None:
    ns = quote(namespace.strip(), safe="")
    key = quote(CURRENT_MEMORY_KEY, safe="")
    url = f"{storage_url}/namespaces/{ns}/records/{key}"
    body = {
        "recordType": record.get("recordType", RECORD_TYPE),
        "namespace": record.get("namespace", namespace),
        "attributes": record.get("attributes", []),
    }
    with httpx.Client(timeout=STORAGE_TIMEOUT) as client:
        r = client.put(url, json=body)
        r.raise_for_status()


def _ensure_current_memory(storage_url: str, namespace: str) -> dict:
    existing = _get_current_memory(storage_url, namespace)
    if existing is not None:
        return existing
    record = _memory_record(namespace, [])
    _put_current_memory(storage_url, namespace, record)
    return record


def _build_display_from_result(result: dict[str, Any]) -> str | None:
    """
    Build chat-friendly markdown from agent result.step_results and result.objective.
    Returns None to mean use result.answer as-is (e.g. direct answer, no steps).
    """
    step_results = result.get("step_results") or []
    if not step_results:
        return None
    objective = (result.get("objective") or "").strip()
    parts = [f"## {objective}", ""] if objective else []
    for r in step_results:
        if r.get("error"):
            continue
        data = r.get("response_data")
        if data is not None:
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    pass
            parts.append(skill_response_to_markdown(data))
            parts.append("")
    failures = [r for r in step_results if r.get("error")]
    if failures:
        parts.append("---")
        parts.append("**Failed steps:**")
        for r in failures:
            parts.append(f"- {r.get('skill_name', '?')}: {r.get('error', '')}")
    return "\n".join(parts).strip() if parts else None


def _format_raw_answer_with_results(raw: str) -> str | None:
    """
    If raw answer is "Objective: ... Results: ... - skill path: {json}", parse and format for display.
    Fallback when result.step_results is missing from the agent response.
    """
    s = (raw or "").strip()
    if not s.startswith("Objective:") or "Results:" not in s:
        return None
    lines = s.split("\n")
    objective = ""
    for i, line in enumerate(lines):
        if line.strip().startswith("Objective:"):
            objective = line.strip().replace("Objective:", "").strip()
            break
    parts = [f"## {objective}", ""] if objective else []
    for line in lines:
        line = line.strip()
        if not line.startswith("- "):
            continue
        # JSON starts at ": {" so we don't split on ": " inside the payload
        idx = line.find(": {")
        if idx < 0:
            continue
        payload = line[idx + 2 :].strip()  # after ": "
        if not payload.startswith("{"):
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            parts.append(skill_response_to_markdown(data))
            parts.append("")
    if not parts:
        return None
    if "Failed:" in s:
        failed_start = s.find("Failed:")
        if failed_start >= 0:
            failed_section = s[failed_start:].strip()
            parts.append("---")
            parts.append("**Failed steps:**")
            for fl in failed_section.split("\n")[1:]:
                fl = fl.strip()
                if fl.startswith("- "):
                    parts.append(fl)
    return "\n".join(parts).strip()


def _format_answer_for_display(text: str) -> str:
    """If the answer looks like a single raw JSON object, render with common formatter. Otherwise return as-is."""
    s = (text or "").strip()
    if not s or not s.startswith("{"):
        return text
    if "\n" in s and s.count("\n") > 3:
        return text
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return skill_response_to_markdown(data)
    except json.JSONDecodeError:
        pass
    return text


def _append_to_memory(record: dict, prompt: str, response_text: str) -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    memory_entry = f"User: {prompt}\nAssistant: {response_text}"
    attrs = list(record.get("attributes") or [])
    attrs.append({"datetime": now, "memory": memory_entry})
    return {**record, "attributes": attrs}


@router.post("")
def chat(body: dict[str, Any]) -> dict[str, Any]:
    """
    Body: namespace (required), prompt (required), profile (optional, ignored; agent uses agent profile).
    Loads or creates current_memory for namespace, passes it as conversation_context to the agent server
    (planner + executor; direct answer when no skills needed). Appends exchange to memory, returns text.
    """
    namespace = (body.get("namespace") or "").strip()
    if not namespace:
        raise HTTPException(status_code=400, detail="namespace is required")
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    storage_url = _storage_url()
    memory_record = _ensure_current_memory(storage_url, namespace)
    context = _format_memory_context(memory_record.get("attributes") or [])

    agent_url = _agent_url()
    try:
        data = _call_agent_server(agent_url, prompt, conversation_context=context or None)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    result = data.get("result") or {}
    raw_answer = (result.get("answer") or "").strip()
    # UI-only: format for chat display; APIs get raw from agent
    display = _build_display_from_result(result)
    if not display:
        display = _format_raw_answer_with_results(raw_answer)
    text = display if display else _format_answer_for_display(raw_answer)

    updated = _append_to_memory(memory_record, prompt, text)
    _put_current_memory(storage_url, namespace, updated)

    return {
        "namespace": namespace,
        "prompt": prompt,
        "text": text,
        "profile": DEFAULT_PROFILE,
        "provider": None,
    }
