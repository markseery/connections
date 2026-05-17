"""Run state server standalone: python -m servers.state.run"""

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("STATE_SERVER_PORT", "7028"))
    uvicorn.run(
        "servers.state.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
