"""
License: MIT
Description: FastAPI application for the Connections UI server.

Serves the home page (icon tiles) and applet pages such as chat. Static assets
and HTML are served from the package directory. Exposes /api/chatserver-url so
the chat page can discover the chat server from the registry.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from starlette.staticfiles import StaticFiles

from decorations.monitor import monitor_fastapi_app

_here = Path(__file__).resolve().parent
_templates = _here / "templates"
_static = _here / "static"


app = FastAPI(
    title="Connections UI",
    description="HTML/CSS/JS UI for Connections.",
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    path = _templates / "index.html"
    return path.read_text(encoding="utf-8")


@app.get("/chat", response_class=HTMLResponse)
def chat() -> str:
    path = _templates / "chat.html"
    return path.read_text(encoding="utf-8")


@app.get("/agent-chat", response_class=HTMLResponse)
def agent_chat() -> str:
    path = _templates / "agent_chat.html"
    return path.read_text(encoding="utf-8")


@app.get("/api/agent-chat-url")
def agent_chat_url() -> dict[str, str]:
    """Return the agent_chat server base URL from the registry (for the agent-chat page)."""
    registry = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").strip().rstrip("/")
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{registry}/servers/agent_chat")
            r.raise_for_status()
            data = r.json()
            url = data.get("url")
            if not url:
                raise HTTPException(status_code=502, detail="Registry missing agent_chat url")
            return {"url": url.rstrip("/")}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/api/chatagent-url")
def chatagent_url() -> dict[str, str]:
    """Return the chatagent base URL from the registry (for the chat page to call)."""
    registry = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").strip().rstrip("/")
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{registry}/servers/chatagent")
            r.raise_for_status()
            data = r.json()
            url = data.get("url")
            if not url:
                raise HTTPException(status_code=502, detail="Registry missing chatagent url")
            return {"url": url.rstrip("/")}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


if _static.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

monitor_fastapi_app(app)
