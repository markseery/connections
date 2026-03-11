"""
License: MIT
Description: Uvicorn entrypoint for the chatagent server.
"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7023"))
    uvicorn.run(
        "servers.chatagent.main:app",
        host="127.0.0.1",
        port=port,
        reload=False,
    )

