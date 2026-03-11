"""
License: MIT
Description: Relay to the AI server. Resolves aiserver URL from registry and forwards generate requests.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import get_registry_url


def get_aiserver_url() -> str:
    registry = get_registry_url()
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{registry}/servers/aiserver")
        r.raise_for_status()
        data = r.json()
        url = data.get("url")
        if not url:
            raise ValueError("Registry response missing 'url' for aiserver")
        return url.rstrip("/")


def send_to_ai(prompt: str, profile: str, provider: str | None = None) -> dict[str, Any]:
    base = get_aiserver_url()
    payload: dict[str, Any] = {"prompt": prompt, "profile": profile}
    if provider:
        payload["provider"] = provider
    with httpx.Client(timeout=120.0) as client:
        r = client.post(f"{base}/generate", json=payload)
        r.raise_for_status()
        return r.json()
