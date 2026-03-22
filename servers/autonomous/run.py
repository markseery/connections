"""Standalone uvicorn entrypoint for the autonomous agent server."""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "servers.autonomous.main:app",
        host="127.0.0.1",
        port=7027,
        reload=False,
    )
