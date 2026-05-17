"""
License: MIT
Description: State machine server — config-driven symbol state with pub/sub notifications.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from decorations.monitor import monitor_fastapi_app
from common.simple.user_dir import load_connections_dotenv

from .routes import router, shutdown_scheduler, startup_scheduler

load_connections_dotenv()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title="State Machine Server",
    description="Periodic remote state pulls with pub/sub on changes.",
    lifespan=lifespan,
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
