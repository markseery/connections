"""
License: MIT
Description: Entrypoint to run the workflow server standalone.
  python -m servers.workflow.run
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "servers.workflow.main:app",
        host="0.0.0.0",
        port=7026,
        reload=True,
    )
