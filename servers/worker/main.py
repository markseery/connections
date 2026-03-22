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
from pathlib import Path

from fastapi import FastAPI, Request, Response
from dotenv import load_dotenv
from starlette.middleware.base import BaseHTTPMiddleware

from decorations.monitor import monitor_fastapi_app
from .routes import router, _mgr

_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path)

_SKILL_PATH_RE = re.compile(r"^/skills/([^/]+)")


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
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router)
app.add_middleware(_AutoLoadSkillMiddleware)
monitor_fastapi_app(app)

