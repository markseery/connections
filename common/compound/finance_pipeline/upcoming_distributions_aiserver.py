"""Aiserver connectivity helpers for the upcoming-distributions agent loop."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from common.compound.aiserver_generate_client import AiserverGenerateClient

ProgressFn = Callable[[str], None]


def check_aiserver_health(base_url: str, timeout_sec: float = 5.0) -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.get(f"{base_url.rstrip('/')}/health")
            if response.status_code == 200:
                return True, "ok"
            return False, f"status={response.status_code}"
    except Exception as exc:
        return False, str(exc)


def make_generate_fn(
    progress: ProgressFn,
) -> Callable[..., str]:
    def _call_generate(
        *,
        prompt: str,
        profile: str,
        provider: str | None,
        base_url: str,
        timeout_sec: float,
    ) -> str:
        progress(
            f"aiserver /generate profile={profile} provider={provider or 'default'} "
            f"timeout={timeout_sec:.0f}s prompt_chars={len(prompt)}"
        )
        client = AiserverGenerateClient(base_url=base_url, timeout_sec=timeout_sec)
        payload = client.generate(prompt=prompt, profile=profile, provider=provider)
        return AiserverGenerateClient.output_text(payload)

    return _call_generate
