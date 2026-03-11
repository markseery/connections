"""
License: MIT
Description: Provider implementations for AI generation.

Each provider supports the profiles: fast, chat, reason, agent, code, image, video.
This module maps profiles to models and provides a single `generate(...)` function.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx

from .config import Profile, Provider, get_model, get_provider_base_url, get_provider_key


def _require_key(provider: Provider) -> str:
    key = get_provider_key(provider)
    if not key:
        raise RuntimeError(f"Missing API key for provider '{provider}' in .env")
    return key


def _ollama_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    base = get_provider_base_url("ollama") or "http://localhost:11434"
    # Ollama's /api/generate is a simple baseline.
    url = f"{base.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False}
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        text = data.get("response", "")
        return {"type": "text", "text": text}


def _openai_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    base = (get_provider_base_url("openai") or "https://api.openai.com").rstrip("/")
    key = _require_key("openai")
    # Use a conservative Chat Completions call for broad compatibility.
    url = f"{base}/v1/chat/completions"
    messages = [{"role": "user", "content": prompt}]
    payload = {"model": model, "messages": messages}
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        j = r.json()
        text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"type": "text", "text": text}


def _xai_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    base = (get_provider_base_url("xai") or "https://api.x.ai").rstrip("/")
    key = _require_key("xai")
    # xAI is largely OpenAI-compatible.
    url = f"{base}/v1/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        j = r.json()
        text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"type": "text", "text": text}


def _google_generate(prompt: str, model: str, profile: Profile) -> dict[str, Any]:
    base = (get_provider_base_url("google") or "https://generativelanguage.googleapis.com").rstrip("/")
    key = _require_key("google")
    # Minimal Generative Language API call (text).
    # POST /v1beta/models/{model}:generateContent?key=...
    url = f"{base}/v1beta/models/{model}:generateContent"
    params = {"key": key}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, params=params, json=payload)
        r.raise_for_status()
        j = r.json()
        # Extract first candidate text if present.
        candidates = j.get("candidates") or []
        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or []) if candidates else []
        text = (parts[0] or {}).get("text", "") if parts else ""
        return {"type": "text", "text": text}


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
    else:
        raise RuntimeError(f"Unsupported provider: {provider}")

    return {"provider": provider, "profile": profile, "model": model, "output": out}

