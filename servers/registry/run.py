"""
License: MIT
Description: Entrypoint to run the registry server with uvicorn. From project root:
  python -m servers.registry.run
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "servers.registry.main:app",
        host="0.0.0.0",
        port=7002,
        reload=True,
    )

