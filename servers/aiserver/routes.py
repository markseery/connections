"""
License: MIT
Description: AI server routes.

Provides `/generate` that accepts prompt/profile/provider, maps profiles to models
per provider, and supports transport encryption for request and response bodies.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from common.compound.transport_encryption import get_transport_encryption
from .config import (
    SUPPORTED_PROFILES,
    SUPPORTED_PROVIDERS,
    get_model_info,
    get_provider_for_profile,
)
from .providers import MissingProviderApiKeyError, generate

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


def _upstream_json_message(body: str) -> str | None:
    """If *body* is JSON with a provider-style error payload, return a short line."""
    if not body or not body.strip():
        return None
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    err = obj.get("error")
    if isinstance(err, dict):
        nested = err.get("message")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    if isinstance(err, str) and err.strip():
        cod = obj.get("code")
        if isinstance(cod, str) and cod.strip():
            return f"{cod.strip()}: {err.strip()}"
        return err.strip()
    msg = obj.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    return None


def _extract_upstream_error(exc: Exception) -> tuple[str, int]:
    """Return (detail_string, http_status) from an upstream exception.

    SDK clients (e.g. perplexity) attach the original ``httpx.Response``
    so we can forward the real status code instead of a blanket 500.
    For 429 responses, includes rate-limit headers to distinguish
    rate limiting from quota exhaustion.
    """
    response = getattr(exc, "response", None)
    upstream_status: int | None = None
    snippet = ""
    rate_info = ""
    human: str | None = None

    if response is not None:
        try:
            upstream_status = int(response.status_code)
        except (ValueError, TypeError):
            pass
        body = ""
        try:
            body = response.text or ""
        except Exception:
            body = ""
        if body.strip():
            human = _upstream_json_message(body)
            if not human:
                snippet = body[:800].strip()
                if len(body) > 800:
                    snippet += "..."

        if upstream_status == 429:
            rl_parts: list[str] = []
            headers = getattr(response, "headers", {})
            for key in ("retry-after", "anthropic-ratelimit-requests-remaining",
                        "anthropic-ratelimit-requests-limit",
                        "anthropic-ratelimit-requests-reset",
                        "anthropic-ratelimit-tokens-remaining",
                        "anthropic-ratelimit-tokens-limit",
                        "anthropic-ratelimit-tokens-reset",
                        "x-ratelimit-limit-requests",
                        "x-ratelimit-remaining-requests",
                        "x-ratelimit-reset-requests"):
                val = headers.get(key)
                if val is not None:
                    rl_parts.append(f"{key}={val}")
            if rl_parts:
                rate_info = " | rate-limit: " + ", ".join(rl_parts)

    if human:
        detail = human
    else:
        detail = str(exc)
        if snippet:
            detail = f"{detail}; upstream response: {snippet}"
    if rate_info:
        detail += rate_info

    if upstream_status and 400 <= upstream_status < 600:
        return detail, upstream_status
    if response is not None:
        return detail, 502
    return detail, 500


@router.post("/generate")
def generate_route(request: Request, body: dict[str, Any]) -> Any:
    """
    Body:
      - prompt: str (required)
      - profile: aiserver profile (e.g. fast, chat, reason, market_analyser, …) (required)
      - provider: one of ollama/openai/xai/google/perplexity/wandb/anthropic/mlx (optional)
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

    from .config import get_model
    model = get_model(provider, profile)  # type: ignore[arg-type]

    try:
        result = generate(prompt=prompt, profile=profile, provider=provider)  # type: ignore[arg-type]
    except MissingProviderApiKeyError as e:
        logger.warning("generate refused: %s | profile=%s provider=%s", e, profile, provider)
        raise HTTPException(status_code=503, detail=str(e)) from e
    except httpx.HTTPError as e:  # type: ignore[name-defined]
        detail, status = _extract_upstream_error(e)
        logger.error(
            "generate %d (upstream HTTP error): %s | profile=%s provider=%s model=%s",
            status, detail, profile, provider, model, exc_info=True,
        )
        raise HTTPException(status_code=status, detail=detail) from e
    except Exception as e:
        detail, status = _extract_upstream_error(e)
        logger.exception(
            "generate %d: %s | profile=%s provider=%s model=%s",
            status, e, profile, provider, model,
        )
        raise HTTPException(status_code=status, detail=detail) from e

    return _maybe_encrypt_response(request, result)


@router.get("/model-info")
def model_info_route(
    profile: str = "agent",
    provider: str | None = None,
) -> dict[str, Any]:
    """Return model name and context window for a profile/provider pair.

    Query params:
      profile  – one of the supported profiles (default: agent)
      provider – explicit provider; omit to use the configured default for the profile
    """
    if profile not in SUPPORTED_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"profile must be one of {sorted(SUPPORTED_PROFILES)}",
        )
    resolved_provider = provider or get_provider_for_profile(profile)  # type: ignore[arg-type]
    if resolved_provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}",
        )
    return get_model_info(resolved_provider, profile)  # type: ignore[arg-type]

