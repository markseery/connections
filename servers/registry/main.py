"""
License: MIT
Description: FastAPI application for the registry server.

Maintains a registry of named servers -> URL/host/port. Used by startup supervisor
to publish server locations, and by clients/processes to discover them.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from common.transport_encryption import get_transport_encryption
from .routes import router
from .state import RegistryState


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_transport_encryption()
    persist = Path(__file__).resolve().parents[2] / "data" / "registry" / "registry.json"
    app.state.registry_state = RegistryState(persist_path=persist)
    yield


app = FastAPI(
    title="Registry API",
    description="Registry of named servers to their URLs/ports.",
    lifespan=lifespan,
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router)

