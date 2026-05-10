"""Shared HTTP client for aiserver /generate calls."""

from __future__ import annotations

from typing import Any

import httpx


class AiserverGenerateClient:
    """Small reusable client for the aiserver /generate endpoint."""

    def __init__(self, base_url: str, timeout_sec: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = float(timeout_sec)

    def generate(
        self,
        *,
        prompt: str,
        profile: str,
        provider: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"prompt": prompt, "profile": profile}
        if provider:
            payload["provider"] = provider

        with httpx.Client(timeout=self._timeout_sec) as client:
            response = client.post(f"{self._base_url}/generate", json=payload)
            if response.status_code >= 400:
                detail = (response.text or "").strip()
                if len(detail) > 1200:
                    detail = detail[:1200] + "... [truncated]"
                raise RuntimeError(
                    f"/generate failed ({response.status_code}) at {self._base_url}: {detail}"
                )
            data = response.json() or {}
            if not isinstance(data, dict):
                raise RuntimeError("Unexpected /generate response payload; expected JSON object")
            return data

    @staticmethod
    def output_text(response_payload: dict[str, Any]) -> str:
        output = response_payload.get("output")
        if isinstance(output, dict):
            return str(output.get("text") or "")
        return str(output or "")

