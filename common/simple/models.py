"""
License: MIT
Description: Common request/response models shared across all servers and skills.

ServiceResponse is the canonical envelope returned by chat endpoints.
SkillOutput is the canonical shape for skill results.
ErrorDetail is the standard error structure.

UI consumers render SkillOutput via skill_response_to_markdown (client-side).
API consumers use output.data for structured access.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillOutput(BaseModel):
    """Canonical skill result shape. Skills should populate these fields."""

    summary: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)
    text: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class ErrorDetail(BaseModel):
    """Structured error information."""

    error: str = ""
    code: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class ServiceResponse(BaseModel):
    """Common response envelope for all chat/skill endpoints.

    Returned by agent_chat, chatagent, and any future API gateway.
    - UI consumers: render output via skill_response_to_markdown
    - API consumers: use output.data for structured access
    """

    success: bool = True
    prompt: str | None = None
    namespace: str | None = None
    output: SkillOutput = Field(default_factory=SkillOutput)
    source: str = ""
    error: ErrorDetail | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def skill_result(
    summary: str = "",
    items: list[dict[str, Any]] | None = None,
    text: str = "",
    **data: Any,
) -> dict[str, Any]:
    """Build a canonical skill response dict.

    Skills should use this instead of ad-hoc dicts so the response
    is guaranteed to match SkillOutput.

    >>> return skill_result(summary="5 articles found.", items=[...])
    """
    out = SkillOutput(
        summary=summary,
        items=items or [],
        text=text,
        data=data,
    )
    return out.model_dump()
