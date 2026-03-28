"""
License: MIT
Description: Direct chat route — sends prompts straight to the AI server.
No skill planning or agent processing; that lives in /agent-chat.
Returns ServiceResponse for both UI and API consumers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from .config import get_aiserver_url
from common.simple.models import ServiceResponse, SkillOutput

router = APIRouter(prefix="/chat", tags=["chat"])


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

    base = get_aiserver_url()
    payload: dict[str, Any] = {"prompt": prompt.strip(), "profile": profile.strip()}
    try:
        with httpx.Client(timeout=120.0) as client:
            r = client.post(f"{base}/generate", json=payload)
            r.raise_for_status()
            ai = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    ai_text = ""
    ai_output = ai.get("output")
    if isinstance(ai_output, dict):
        ai_text = ai_output.get("text", "")
    elif isinstance(ai_output, str):
        ai_text = ai_output

    resp = ServiceResponse(
        success=True,
        prompt=prompt,
        namespace=namespace,
        output=SkillOutput(text=ai_text),
        source="ai:" + (ai.get("provider") or "unknown"),
        metadata={"model": ai.get("model", ""), "profile": profile},
    )
    result = resp.model_dump(mode="json", exclude_none=True)
    result["text"] = ai_text
    result["profile"] = profile
    return result
