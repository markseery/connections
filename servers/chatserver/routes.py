"""
License: MIT
Description: Chat API. Accepts prompt and profile, assigns namespace when missing (guest_<timestamp>), relays to AI server.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from .relay import send_to_ai


router = APIRouter(prefix="/chat", tags=["chat"])


def _default_namespace() -> str:
    """Namespace when none specified: guest + timestamp when chat began."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"guest_{ts}"


@router.post("")
def chat(body: dict[str, Any]) -> dict[str, Any]:
    """
    Body: prompt (required), profile (optional, default fast), namespace (optional).
    When namespace is omitted, use guest_<timestamp>. Relays to AI server and returns
    response plus namespace for conversation context.
    """
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    profile = body.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        profile = "fast"
    namespace = body.get("namespace")
    if not isinstance(namespace, str) or not namespace.strip():
        namespace = _default_namespace()

    try:
        ai_response = send_to_ai(prompt=prompt.strip(), profile=profile.strip())
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # Return AI response plus namespace so client can send it back for same conversation.
    return {
        "namespace": namespace,
        "profile": profile,
        "prompt": prompt,
        **ai_response,
    }
