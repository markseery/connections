"""
License: MIT
Description: FastAPI application for the configuration server.

The configuration server uses the storage server for configuration records, always
in the `system` namespace.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.transport_encryption import get_transport_encryption
from .routes import router
from .storage_client import StorageClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate transport encryption key at startup (used to talk to storage server and callers).
    get_transport_encryption()
    app.state.storage_client = StorageClient()
    yield


app = FastAPI(
    title="Configuration API",
    description="Configuration records stored via storage server (system namespace).",
    lifespan=lifespan,
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}

app.include_router(router)

