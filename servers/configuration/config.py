"""
License: MIT
Description: Loads configuration server settings from environment (.env).
"""

from __future__ import annotations

import os

from common.simple.user_dir import load_connections_dotenv

load_connections_dotenv()

STORAGE_SERVER_URL_ENV = "STORAGE_SERVER_URL"


def get_storage_server_url() -> str:
    url = os.environ.get(STORAGE_SERVER_URL_ENV, "http://localhost:8000").strip()
    if not url:
        raise RuntimeError(f"Missing {STORAGE_SERVER_URL_ENV} in environment")
    return url.rstrip("/")

