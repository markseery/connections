"""
License: MIT
Description: Storage server package. Exposes the FastAPI app for running with uvicorn.
"""

from .main import app

__all__ = ["app"]
