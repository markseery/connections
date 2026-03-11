"""
License: MIT
Description: FastAPI application for the worker server.

Workers can dynamically load skills from the local `skills` package and expose
their routes under `/skills/<skill_name>/...`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from dotenv import load_dotenv

from .routes import router

_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path)


app = FastAPI(
    title="Worker Server",
    description="Loads skills on demand and serves their routes.",
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router)

