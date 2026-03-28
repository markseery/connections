"""
License: MIT
Description: FastAPI application for the AI server.

Exposes `/generate` for provider/profile-based generation, `/model-info`
for model metadata/context windows, and `/health`.
Supports transport encryption for requests/responses (shared across servers).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from decorations.monitor import monitor_fastapi_app
from common.compound.transport_encryption import get_transport_encryption
from .routes import router

logger = logging.getLogger(__name__)


def _probe_all_active_models() -> None:
    """Probe context windows for every (provider, profile) combination that
    has a configured API key.  Runs once at startup; failures are logged
    and silently skipped (the YAML fallback will be used instead)."""
    from .config import (
        SUPPORTED_PROFILES,
        Profile,
        get_model,
        get_provider_for_profile,
        get_provider_key,
        prime_context_cache,
    )

    seen: set[str] = set()
    for profile in sorted(SUPPORTED_PROFILES):
        try:
            provider = get_provider_for_profile(profile)  # type: ignore[arg-type]
        except Exception:
            continue
        if provider in ("mlx", "perplexity"):
            continue
        model = get_model(provider, profile)  # type: ignore[arg-type]
        pair = f"{provider}:{model}"
        if pair in seen:
            continue
        seen.add(pair)
        if provider not in ("ollama",) and not get_provider_key(provider):  # type: ignore[arg-type]
            continue
        logger.info("Probing context window: %s (%s / %s)", model, provider, profile)
        try:
            prime_context_cache(provider, model)  # type: ignore[arg-type]
        except Exception as exc:
            logger.debug("Probe failed for %s: %s", model, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_transport_encryption()
    _probe_all_active_models()
    yield


app = FastAPI(
    title="AI Server",
    description="Provider/profile-based generation API.",
    lifespan=lifespan,
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router)
monitor_fastapi_app(app)

