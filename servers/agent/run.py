"""
License: MIT
Description: Uvicorn entrypoint for the agent server.
"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7024"))
    uvicorn.run(
        "servers.agent.main:app",
        host="127.0.0.1",
        port=port,
        reload=False,
    )
