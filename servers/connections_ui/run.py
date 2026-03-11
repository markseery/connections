"""
License: MIT
Description: Entrypoint to run the Connections UI server. From project root:
  python -m servers.connections_ui.run
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "servers.connections_ui.main:app",
        host="0.0.0.0",
        port=7020,
        reload=True,
    )
