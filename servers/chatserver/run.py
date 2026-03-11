"""
License: MIT
Description: Entrypoint to run the chat server. From project root:
  python -m servers.chatserver.run
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "servers.chatserver.main:app",
        host="0.0.0.0",
        port=7022,
        reload=True,
    )
