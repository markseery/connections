"""
License: MIT
Description: FastAPI application for the chat server.

Relays chat requests to the AI server; will evolve to manage conversations and memories.
Chats have a namespace context; when none is specified, use guest_<timestamp>.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router


app = FastAPI(
    title="Chat Server",
    description="Relays chat to AI server; manages namespace context.",
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
