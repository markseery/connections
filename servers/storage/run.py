"""
License: MIT
Description: Entrypoint to run the storage server with uvicorn. Use from project root:
  python -m servers.storage.run
  or: uvicorn servers.storage:app --reload
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "servers.storage.main:app",
        host="0.0.0.0",
        port=7010,
        reload=True,
    )
