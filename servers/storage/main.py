"""
License: MIT
Description: FastAPI application for the storage server. Mounts CRUD and list
routes; uses an abstract storage backend so the storage mechanism is not exposed to callers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from decorations.monitor import monitor_fastapi_app
from .backend import FileEncryptedBackend, StorageBackend
from .routes import router

# Data dir relative to project root
STORAGE_ROOT = Path(__file__).resolve().parents[2] / "data" / "storage"
_backend: StorageBackend | None = None


def get_backend() -> StorageBackend:
    global _backend
    if _backend is None:
        _backend = FileEncryptedBackend(STORAGE_ROOT)
    return _backend


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .config import get_storage_encryption_key
    get_storage_encryption_key()  # validate .env key at startup
    from common.compound.transport_encryption import get_transport_encryption
    get_transport_encryption()  # validate transport key at startup
    app.state.storage_backend = get_backend()
    yield


app = FastAPI(
    title="Storage API",
    description="JSON storage by namespace and key; CRUD and list. Storage is encrypted.",
    lifespan=lifespan,
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}

app.include_router(router)
monitor_fastapi_app(app)
