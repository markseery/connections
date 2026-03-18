"""
License: MIT
Description: Consolidated chat server — hosts both code paths:

  POST /chat        — skill-first routing with AI fallback (was chatagent)
  POST /agent-chat  — memory-backed agentic chat via agent server (was agent_chat)

Both paths return ServiceResponse. UI pages discover this server via
a single registry entry ("chat").
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from decorations.monitor import monitor_fastapi_app
from servers.chatagent.routes import router as chat_router
from servers.agent_chat.routes import router as agent_chat_router


app = FastAPI(
    title="Chat Server",
    description="Consolidated chat: /chat (skill+AI) and /agent-chat (memory+agent).",
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


app.include_router(chat_router)
app.include_router(agent_chat_router)
monitor_fastapi_app(app)
