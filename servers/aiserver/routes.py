"""
License: MIT
Description: AI server routes.

Provides `/generate` that accepts prompt/profile/provider, maps profiles to models
per provider, and supports transport encryption for request and response bodies.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from common.transport_encryption import get_transport_encryption
from .config import SUPPORTED_PROFILES, SUPPORTED_PROVIDERS, get_provider_for_profile
from .providers import generate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])

_ENC_HEADER = "x-transport-encrypted"
_ENC_FIELD = "_enc"


def _wants_encrypted_response(request: Request) -> bool:
    return request.headers.get(_ENC_HEADER, "").strip() in {"1", "true", "True", "yes", "YES"}


def _maybe_decrypt_body(body: Any) -> Any:
    if isinstance(body, dict) and isinstance(body.get(_ENC_FIELD), str) and body[_ENC_FIELD]:
        return get_transport_encryption().decrypt_json(body[_ENC_FIELD])
    return body


def _maybe_encrypt_response(request: Request, payload: Any) -> Any:
    if _wants_encrypted_response(request):
        return {_ENC_FIELD: get_transport_encryption().encrypt_json(payload)}
    return payload


@router.post("/generate")
def generate_route(request: Request, body: dict[str, Any]) -> Any:
    """
    Body:
      - prompt: str (required)
      - profile: one of fast/chat/reason/agent/code/image/video (required)
      - provider: one of ollama/openai/xai/google/perplexity/wandb (optional)
    """
    body_any = _maybe_decrypt_body(body)
    if not isinstance(body_any, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    prompt = body_any.get("prompt")
    profile = body_any.get("profile")

    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    if not isinstance(profile, str) or profile not in SUPPORTED_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"profile must be one of {sorted(SUPPORTED_PROFILES)}",
        )
    # When client omits provider, use the provider configured for this profile (can differ per profile).
    provider = body_any.get("provider") or get_provider_for_profile(profile)
    if not isinstance(provider, str) or provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}",
        )

    try:
        result = generate(prompt=prompt, profile=profile, provider=provider)  # type: ignore[arg-type]
    except httpx.HTTPError as e:  # type: ignore[name-defined]
        detail = str(e)
        response_snippet = ""
        if getattr(e, "response", None) is not None:
            try:
                body = e.response.text
                if body:
                    response_snippet = body[:800].strip()
                    if len(body) > 800:
                        response_snippet += "..."
            except Exception:
                pass
        if response_snippet:
            detail = f"{detail}; upstream response: {response_snippet}"
        logger.error(
            "generate 502 (upstream error): %s | profile=%s provider=%s",
            detail,
            profile,
            provider,
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail=detail) from e
    except Exception as e:
        logger.exception(
            "generate 500: %s | profile=%s provider=%s",
            e,
            profile,
            provider,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e

    return _maybe_encrypt_response(request, result)

