"""
License: MIT
Description: Probe provider APIs for model context window sizes at runtime.

Supported providers:
  - Anthropic: GET /v1/models/{model} → max_input_tokens
  - Google:    GET /v1beta/models/{model} → inputTokenLimit
  - Ollama:    POST /api/show            → model_info.*.context_length or parameters.num_ctx

Providers that don't expose model metadata (OpenAI, xAI, W&B, Perplexity, MLX)
return None and the caller falls back to the YAML config table.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from .config import (
    Provider,
    get_provider_base_url,
    get_provider_key,
)

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 10.0


def _probe_anthropic(model: str) -> int | None:
    base = (get_provider_base_url("anthropic") or "https://api.anthropic.com").rstrip("/")
    key = get_provider_key("anthropic")
    if not key:
        return None
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            r = client.get(
                f"{base}/v1/models/{model}",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
            )
            r.raise_for_status()
        data = r.json()
        val = data.get("max_input_tokens")
        if isinstance(val, int) and val > 0:
            return val
    except Exception as exc:
        logger.debug("Anthropic probe failed for %s: %s", model, exc)
    return None


def _probe_google(model: str) -> int | None:
    base = (get_provider_base_url("google") or "https://generativelanguage.googleapis.com").rstrip("/")
    key = get_provider_key("google")
    if not key:
        return None
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            r = client.get(
                f"{base}/v1beta/models/{model}",
                params={"key": key},
            )
            r.raise_for_status()
        data = r.json()
        val = data.get("inputTokenLimit")
        if isinstance(val, int) and val > 0:
            return val
    except Exception as exc:
        logger.debug("Google probe failed for %s: %s", model, exc)
    return None


def _probe_ollama(model: str) -> int | None:
    base = (get_provider_base_url("ollama") or "http://localhost:11434").rstrip("/")
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            r = client.post(f"{base}/api/show", json={"model": model})
            r.raise_for_status()
        data = r.json()

        model_info = data.get("model_info") or {}
        for key, val in model_info.items():
            if key.endswith(".context_length") and isinstance(val, int) and val > 0:
                return val

        params_str = data.get("parameters") or ""
        if isinstance(params_str, str):
            match = re.search(r"num_ctx\s+(\d+)", params_str)
            if match:
                return int(match.group(1))
    except Exception as exc:
        logger.debug("Ollama probe failed for %s: %s", model, exc)
    return None


_PROBE_FNS: dict[str, Any] = {
    "anthropic": _probe_anthropic,
    "google": _probe_google,
    "ollama": _probe_ollama,
}


def probe_context_window(provider: Provider, model: str) -> int | None:
    """Attempt to discover the context window from the provider API.

    Returns the context window in tokens, or None if the provider does not
    expose this information or the probe fails.
    """
    fn = _PROBE_FNS.get(provider)
    if fn is None:
        return None
    return fn(model)
