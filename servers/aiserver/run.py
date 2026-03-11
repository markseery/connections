"""
License: MIT
Description: Entrypoint to run the AI server with uvicorn. From project root:
  python -m servers.aiserver.run
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "servers.aiserver.main:app",
        host="0.0.0.0",
        port=7012,
        reload=True,
    )

