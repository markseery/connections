"""
License: MIT
Description: FastAPI application for the AI server.

Exposes `/generate` for provider/profile-based generation, and `/health`.
Supports transport encryption for requests/responses (shared across servers).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from decorations.monitor import monitor_fastapi_app
from common.compound.transport_encryption import get_transport_encryption
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate transport encryption key at startup.
    get_transport_encryption()
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

