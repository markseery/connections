"""
License: MIT
Description: Workflow server — submit and poll multi-step YAML workflows.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from decorations.monitor import monitor_fastapi_app
from .routes import router, shutdown_workflow_thread_pool


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    shutdown_workflow_thread_pool()


app = FastAPI(
    title="Workflow Server",
    description="Submit and poll multi-step YAML workflows with per-step progress.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router)
monitor_fastapi_app(app)
