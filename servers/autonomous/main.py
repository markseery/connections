"""
FastAPI application for the autonomous agent server.

Supervisor-managed subagents with layered memory, approval gates,
structured logging, and context compaction.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from decorations.monitor import monitor_fastapi_app
from .routes import router

app = FastAPI(
    title="Autonomous Agent Server",
    description=(
        "Supervisor agent that decomposes goals into subagents, "
        "with approval gates, layered memory, and structured logging."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router)
monitor_fastapi_app(app)
