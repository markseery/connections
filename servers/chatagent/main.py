"""
License: MIT
Description: FastAPI application for the ChatAgent server.

ChatAgent uses skills when the prompt matches, otherwise falls back to aiserver.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from decorations.monitor import monitor_fastapi_app
from .routes import router


app = FastAPI(
    title="ChatAgent Server",
    description="Skill-first chat routing with AI fallback.",
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

