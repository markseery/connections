"""
License: MIT
Description: FastAPI application for the Agent Chat server.

Implements the same behavior as run_agent.py: namespace (client) + prompt,
current_memory in storage, agent_skill with context, append and save.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from decorations.monitor import monitor_fastapi_app
from .routes import router


app = FastAPI(
    title="Agent Chat Server",
    description="Chat with memory (namespace); uses worker agent_skill and storage current_memory.",
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
