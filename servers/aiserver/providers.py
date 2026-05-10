"""
License: MIT
Description: Provider implementations for AI generation.

Each provider supports the profiles: fast, chat, reason, agent, code, image, video.
This module maps profiles to models and provides a single `generate(...)` function.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

from .config import (
    Profile,
    Provider,
    get_model,
    get_provider_base_url,
    get_provider_key,
    get_provider_timeout,
)


class MissingProviderApiKeyError(RuntimeError):
    """Selected provider has no API key in the environment the aiserver loaded."""


def _require_key(provider: Provider) -> str:
    key = get_provider_key(provider)
    if not key:
        raise MissingProviderApiKeyError(
            f"Missing API key for provider '{provider}' after loading .env files "
            f"(set the key in repo or application_files/.env and restart aiserver)."
        )
    return key


def _ollama_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    base = get_provider_base_url("ollama") or "http://localhost:11434"
    url = f"{base.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False}
    with httpx.Client(timeout=get_provider_timeout("ollama")) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        text = data.get("response", "")
        return {"type": "text", "text": text}


def _openai_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    base = (get_provider_base_url("openai") or "https://api.openai.com").rstrip("/")
    key = _require_key("openai")
    url = f"{base}/v1/chat/completions"
    messages = [{"role": "user", "content": prompt}]
    payload = {"model": model, "messages": messages}
    with httpx.Client(timeout=get_provider_timeout("openai")) as client:
        r = client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        j = r.json()
        text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"type": "text", "text": text}


def _xai_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    base = (get_provider_base_url("xai") or "https://api.x.ai").rstrip("/")
    key = _require_key("xai")
    url = f"{base}/v1/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    with httpx.Client(timeout=get_provider_timeout("xai")) as client:
        r = client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        j = r.json()
        text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"type": "text", "text": text}


def _google_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    base = (get_provider_base_url("google") or "https://generativelanguage.googleapis.com").rstrip("/")
    key = _require_key("google")
    url = f"{base}/v1beta/models/{model}:generateContent"
    params = {"key": key}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    with httpx.Client(timeout=get_provider_timeout("google")) as client:
        r = client.post(url, params=params, json=payload)
        r.raise_for_status()
        j = r.json()
        # Extract first candidate text if present.
        candidates = j.get("candidates") or []
        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or []) if candidates else []
        text = (parts[0] or {}).get("text", "") if parts else ""
        return {"type": "text", "text": text}


def _wandb_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    """W&B Inference (OpenAI-compatible); uses WANDB_API_KEY only."""
    base = (get_provider_base_url("wandb") or "https://api.inference.wandb.ai/v1").rstrip("/")
    key = _require_key("wandb")
    url = f"{base}/chat/completions"
    messages = [{"role": "user", "content": prompt}]
    payload = {"model": model, "messages": messages}
    with httpx.Client(timeout=get_provider_timeout("wandb")) as client:
        r = client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        j = r.json()
        text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"type": "text", "text": text}


def _perplexity_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    from perplexity import Perplexity

    key = _require_key("perplexity")
    client = Perplexity(api_key=key)

    print(f"[perplexity] responses.create input={prompt!r}", flush=True)
    try:
        response = client.responses.create(
            preset="pro-search",
            input=prompt,
            instructions="You are an expert on current events.",
            tools=[
                {
                    "type": "web_search",
                    "filters": {
                        "search_recency_filter": "day",
                    },
                }
            ],
        )
    finally:
        client.close()

    text = response.output_text or ""
    print(f"[perplexity] response OK: {len(text)} chars", flush=True)

    results: list[dict[str, Any]] = []
    for output_item in getattr(response, "output", None) or []:
        if getattr(output_item, "type", None) == "search_results":
            for r in getattr(output_item, "results", None) or []:
                entry: dict[str, Any] = {}
                for attr in ("url", "title", "snippet", "date"):
                    val = getattr(r, attr, None)
                    if val is not None:
                        entry[attr] = str(val)
                if entry.get("title") or entry.get("url"):
                    results.append(entry)

    if not results:
        for item in getattr(response, "citations", None) or []:
            if isinstance(item, str):
                results.append({"url": item})
            elif isinstance(item, dict):
                results.append(item)
            else:
                entry = {}
                for attr in ("url", "title", "snippet", "date"):
                    val = getattr(item, attr, None)
                    if val:
                        entry[attr] = str(val)
                if entry:
                    results.append(entry)

    return {"type": "search", "text": text, "results": results}


_mlx_cache: dict[str, tuple[Any, Any]] = {}
_mlx_lock: Any = None


def _get_mlx_lock() -> Any:
    global _mlx_lock
    if _mlx_lock is None:
        import threading
        _mlx_lock = threading.Lock()
    return _mlx_lock


def _mlx_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    """Run inference locally via mlx_lm (Apple Silicon). Model is loaded once and cached.

    All calls are serialized with a lock because MLX's Metal backend
    is not thread-safe under uvicorn's thread pool.
    """
    from mlx_lm import load, generate as mlx_generate

    with _get_mlx_lock():
        if model not in _mlx_cache:
            _mlx_cache.clear()
            _mlx_cache[model] = load(model)
        mdl, tokenizer = _mlx_cache[model]

        messages = [{"role": "user", "content": prompt}]
        formatted = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        text = mlx_generate(mdl, tokenizer, prompt=formatted, verbose=False)
    return {"type": "text", "text": text.strip()}


_MAX_429_RETRIES = 5
_DEFAULT_RETRY_AFTER = 15.0


def _anthropic_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    base = (get_provider_base_url("anthropic") or "https://api.anthropic.com").rstrip("/")
    key = _require_key("anthropic")
    url = f"{base}/v1/messages"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    timeout = get_provider_timeout("anthropic")

    for attempt in range(_MAX_429_RETRIES + 1):
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=payload, headers=headers)

        if r.status_code != 429:
            r.raise_for_status()
            j = r.json()
            parts = j.get("content") or []
            text = "".join(
                block.get("text", "") for block in parts if block.get("type") == "text"
            )
            return {"type": "text", "text": text}

        if attempt >= _MAX_429_RETRIES:
            r.raise_for_status()

        retry_after = _DEFAULT_RETRY_AFTER
        ra_header = r.headers.get("retry-after")
        if ra_header:
            try:
                retry_after = max(1.0, float(ra_header))
            except ValueError:
                pass

        logger.warning(
            "Anthropic 429 rate-limited (attempt %d/%d), retrying in %.0fs (model=%s)",
            attempt + 1, _MAX_429_RETRIES, retry_after, model,
        )
        time.sleep(retry_after)

    r.raise_for_status()
    return {}


def generate(prompt: str, profile: Profile, provider: Provider) -> dict[str, Any]:
    model = get_model(provider, profile)

    if provider == "ollama":
        out = _ollama_generate(prompt, model, profile)
    elif provider == "openai":
        out = _openai_generate(prompt, model, profile)
    elif provider == "xai":
        out = _xai_generate(prompt, model, profile)
    elif provider == "google":
        out = _google_generate(prompt, model, profile)
    elif provider == "perplexity":
        out = _perplexity_generate(prompt, model, profile)
    elif provider == "wandb":
        out = _wandb_generate(prompt, model, profile)
    elif provider == "anthropic":
        out = _anthropic_generate(prompt, model, profile)
    elif provider == "mlx":
        out = _mlx_generate(prompt, model, profile)
    else:
        raise RuntimeError(f"Unsupported provider: {provider}")

    return {"provider": provider, "profile": profile, "model": model, "output": out}

