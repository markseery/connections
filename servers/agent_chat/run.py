"""
License: MIT
Description: Entrypoint to run the agent chat server. From project root:
  python -m servers.agent_chat.run
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "servers.agent_chat.main:app",
        host="0.0.0.0",
        port=7025,
        reload=True,
    )
