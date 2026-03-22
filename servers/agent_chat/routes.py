"""
License: MIT
Description: Agent chat API. POST /chat with namespace and prompt; uses storage
for current_memory and the agent server (planner + executor) for skills or direct answer.

Returns ServiceResponse: structured SkillOutput for API consumers, plus
`text` (markdown) for backward-compatible UI rendering. Memory stores the
concise answer text (not markdown formatting).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException

from common.models import ServiceResponse, SkillOutput, ErrorDetail
from common.skill_response import skill_response_to_markdown

from .config import get_registry_url

router = APIRouter(prefix="/agent-chat", tags=["agent-chat"])

AGENT_EXECUTE_TIMEOUT = 180.0
STORAGE_TIMEOUT = 15.0
CURRENT_MEMORY_KEY = "current_memory"
RECORD_TYPE = "current_memory"
DEFAULT_PROFILE = "agent"


def _agent_url() -> str:
    from common.registry_client import get_server_url
    return get_server_url("agent")


def _storage_url() -> str:
    from common.registry_client import get_server_url
    return get_server_url("storage")


def _call_agent_server(
    agent_url: str, prompt: str, conversation_context: str | None = None
) -> dict[str, Any]:
    from common.registry_client import get_http_client
    payload: dict[str, Any] = {"prompt": prompt}
    if conversation_context:
        payload["conversation_context"] = conversation_context
    r = get_http_client().post(f"{agent_url}/agent/execute", json=payload, timeout=AGENT_EXECUTE_TIMEOUT)
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
    from common.registry_client import get_http_client
    ns = quote(namespace.strip(), safe="")
    key = quote(CURRENT_MEMORY_KEY, safe="")
    url = f"{storage_url}/namespaces/{ns}/records/{key}"
    r = get_http_client().get(url, timeout=STORAGE_TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    val = data.get("value")
    return val if isinstance(val, dict) else None


def _put_current_memory(storage_url: str, namespace: str, record: dict) -> None:
    from common.registry_client import get_http_client
    ns = quote(namespace.strip(), safe="")
    key = quote(CURRENT_MEMORY_KEY, safe="")
    url = f"{storage_url}/namespaces/{ns}/records/{key}"
    body = {
        "recordType": record.get("recordType", RECORD_TYPE),
        "namespace": record.get("namespace", namespace),
        "attributes": record.get("attributes", []),
    }
    r = get_http_client().put(url, json=body, timeout=STORAGE_TIMEOUT)
    r.raise_for_status()


def _ensure_current_memory(storage_url: str, namespace: str) -> dict:
    existing = _get_current_memory(storage_url, namespace)
    if existing is not None:
        return existing
    record = _memory_record(namespace, [])
    _put_current_memory(storage_url, namespace, record)
    return record


def _build_output_from_result(result: dict[str, Any]) -> SkillOutput:
    """Build SkillOutput from agent result. Uses step_results for structured data."""
    step_results = result.get("step_results") or []
    answer = (result.get("answer") or "").strip()
    objective = (result.get("objective") or "").strip()

    if not step_results:
        return SkillOutput(text=answer, summary=objective)

    all_items: list[dict[str, Any]] = []
    summaries: list[str] = []
    texts: list[str] = []
    combined_data: dict[str, Any] = {}
    for sr in step_results:
        if sr.get("error"):
            continue
        rd = sr.get("response_data")
        if rd is None:
            continue
        if isinstance(rd, str):
            try:
                rd = json.loads(rd)
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(rd, dict):
            if rd.get("summary"):
                summaries.append(rd["summary"])
            if isinstance(rd.get("text"), str) and rd["text"].strip():
                texts.append(rd["text"].strip())
            if isinstance(rd.get("items"), list):
                all_items.extend(rd["items"])
            combined_data[sr.get("skill_name", "step")] = rd

    if len(summaries) == 1 and len(summaries[0]) >= 300 and summaries[0].count("\n") >= 3:
        return SkillOutput(summary=summaries[0], data=combined_data)

    summary = objective
    if summaries:
        summary = (objective + " — " if objective else "") + " | ".join(summaries)

    text = answer or "\n\n".join(texts)

    return SkillOutput(
        summary=summary,
        items=all_items,
        text=text,
        data=combined_data,
    )


def _append_to_memory(record: dict, prompt: str, response_text: str) -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    memory_entry = f"User: {prompt}\nAssistant: {response_text}"
    attrs = list(record.get("attributes") or [])
    attrs.append({"datetime": now, "memory": memory_entry})
    return {**record, "attributes": attrs}


@router.post("")
def chat(body: dict[str, Any]) -> dict[str, Any]:
    """
    Body: namespace (required), prompt (required).

    Returns ServiceResponse (JSON):
    - output: structured SkillOutput (summary, items, text, data)
    - text: markdown-formatted display (backward compat for UI)
    - source, metadata, error: standard envelope fields

    API consumers use `output`; UI uses `text`.
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
    output = _build_output_from_result(result)

    # Determine source
    step_results = result.get("step_results") or []
    ok_steps = [sr for sr in step_results if not sr.get("error")]
    if ok_steps:
        source = f"skill:{ok_steps[0].get('skill_name', 'unknown')}"
    else:
        source = "ai:agent"

    # Markdown for UI backward compat
    text = skill_response_to_markdown(output.model_dump())

    # Store concise answer in memory (not markdown formatting)
    memory_text = output.text or output.summary or text
    updated = _append_to_memory(memory_record, prompt, memory_text)
    _put_current_memory(storage_url, namespace, updated)

    # Build metadata
    metadata: dict[str, Any] = {}
    if data.get("request_id"):
        metadata["request_id"] = data["request_id"]
    if data.get("plan_cache_hit") is not None:
        metadata["plan_cache_hit"] = data["plan_cache_hit"]
    if result.get("replan_count"):
        metadata["replan_count"] = result["replan_count"]
    if result.get("partial"):
        metadata["partial"] = True

    # Check for failures
    failed = [sr for sr in step_results if sr.get("error")]
    error = None
    if failed and not ok_steps:
        error = ErrorDetail(
            error="All skill steps failed",
            code="skill_execution_failed",
            detail={"failed_steps": [{"skill": sr.get("skill_name"), "error": sr.get("error")} for sr in failed]},
        )

    response = ServiceResponse(
        success=not error,
        prompt=prompt,
        namespace=namespace,
        output=output,
        source=source,
        error=error,
        metadata=metadata,
    )
    resp = response.model_dump(mode="json", exclude_none=True)
    resp["text"] = text
    resp["profile"] = DEFAULT_PROFILE
    return resp
