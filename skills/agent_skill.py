"""
Agent skill — receives a prompt and returns an AI-generated response via the aiserver.

Input: POST body with "prompt" (required), optional "profile" (default "agent").
Requires: registry (REGISTRY_SERVER_URL) to resolve aiserver; aiserver must be running.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.simple.skill_response import skill_result
from common.compound.skill_config import SkillConfig

router = APIRouter()

DEFAULT_PROFILE = "agent"
_conf = SkillConfig("agent_skill")


def _registry_url() -> str:
    return os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").strip().rstrip("/")


def _aiserver_url() -> str:
    reg = _registry_url()
    with httpx.Client(timeout=_conf.get("registry_timeout", 5.0)) as client:
        r = client.get(f"{reg}/servers/aiserver")
        r.raise_for_status()
        url = (r.json() or {}).get("url", "").strip().rstrip("/")
        if not url:
            raise HTTPException(status_code=503, detail="Registry returned no aiserver url")
        return url


class AgentRequest(BaseModel):
    """Request body for the Agent skill."""

    prompt: str = Field(..., min_length=1, description="The prompt to send to the AI.")
    profile: str = Field(default=DEFAULT_PROFILE, description="Aiserver profile (e.g. fast, agent).")
    context: str | None = Field(default=None, description="Optional context (e.g. memory) prepended to the prompt.")


@router.post("/respond")
def respond(body: AgentRequest) -> dict[str, Any]:
    """Get an AI response to a prompt. Body: prompt (required), profile (optional), context (optional). Use for general Q&A when no other skill fits."""
    aiserver = _aiserver_url()
    prompt_text = body.prompt.strip()
    if body.context and body.context.strip():
        prompt_text = (
            "Relevant context:\n\n" + body.context.strip() + "\n\nUser prompt:\n\n" + prompt_text
        )
    payload = {"prompt": prompt_text, "profile": body.profile.strip() or DEFAULT_PROFILE}
    with httpx.Client(timeout=_conf.get("generate_timeout", 120.0)) as client:
        r = client.post(f"{aiserver}/generate", json=payload)
        r.raise_for_status()
    data = r.json() or {}
    output = data.get("output")
    if isinstance(output, dict):
        text = output.get("text", "")
    else:
        text = str(output) if output is not None else ""
    profile = data.get("profile", body.profile) or body.profile
    return skill_result(
        summary=f"Agent response from profile **{profile}**.",
        text=text,
        profile=profile,
        provider=data.get("provider"),
    )


def get_router() -> APIRouter:
    return router
