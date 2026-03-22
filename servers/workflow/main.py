"""
License: MIT
Description: Workflow server — submit and poll multi-step YAML workflows.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from decorations.monitor import monitor_fastapi_app
from .routes import router

app = FastAPI(
    title="Workflow Server",
    description="Submit and poll multi-step YAML workflows with per-step progress.",
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
