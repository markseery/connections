"""
License: MIT
Description: Entrypoint to run the consolidated chat server. From project root:
  python -m servers.chat.run
"""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7023"))
    uvicorn.run(
        "servers.chat.main:app",
        host="127.0.0.1",
        port=port,
        reload=False,
    )
