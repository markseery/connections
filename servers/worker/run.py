"""
License: MIT
Description: Uvicorn entrypoint for the worker server.
"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7030"))
    uvicorn.run(
        "servers.worker.main:app",
        host="127.0.0.1",
        port=port,
        reload=False,
    )

