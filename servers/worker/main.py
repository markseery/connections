"""
License: MIT
Description: FastAPI application for the worker server.

Workers can dynamically load skills from the local `skills` package and expose
their routes under `/skills/<skill_name>/...`.

Skills are loaded on first request — callers never need to explicitly load a
skill before using it.
"""

from __future__ import annotations

import re
import threading

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from decorations.monitor import monitor_fastapi_app
from .routes import router, _mgr

from common.simple.user_dir import load_connections_dotenv

load_connections_dotenv()

_SKILL_PATH_RE = re.compile(r"^/skills/([^/]+)")

_active_requests = 0
_active_lock = threading.Lock()


class _ActiveRequestMiddleware(BaseHTTPMiddleware):
    """Track the number of in-flight skill requests."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        global _active_requests
        path = request.url.path
        is_skill = path.startswith("/skills/")

        if is_skill:
            with _active_lock:
                _active_requests += 1
        try:
            return await call_next(request)
        finally:
            if is_skill:
                with _active_lock:
                    _active_requests -= 1


class _AutoLoadSkillMiddleware(BaseHTTPMiddleware):
    """Intercept requests to /skills/{name}/... and auto-load the skill if needed."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        match = _SKILL_PATH_RE.match(request.url.path)
        if match:
            skill_name = match.group(1)
            mgr = _mgr(request)
            if not mgr.is_loaded(skill_name):
                try:
                    mgr.load(skill_name)
                except Exception as exc:
                    print(f"[worker] auto-load '{skill_name}' failed: {exc}", flush=True)
                    return Response(
                        content=f'{{"detail":"Skill \'{skill_name}\' could not be loaded: {exc}"}}',
                        status_code=500,
                        media_type="application/json",
                    )
        return await call_next(request)


app = FastAPI(
    title="Worker Server",
    description="Loads skills on demand and serves their routes.",
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str | int]:
    return {"status": "ok", "active_requests": _active_requests}


app.include_router(router)
app.add_middleware(_AutoLoadSkillMiddleware)
app.add_middleware(_ActiveRequestMiddleware)
monitor_fastapi_app(app)

