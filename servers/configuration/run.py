"""
License: MIT
Description: Entrypoint to run the configuration server with uvicorn. From project root:
  python -m servers.configuration.run
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "servers.configuration.main:app",
        host="0.0.0.0",
        port=7011,
        reload=True,
    )

