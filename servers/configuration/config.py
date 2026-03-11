"""
License: MIT
Description: Loads configuration server settings from environment (.env).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (parent of servers/)
_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path)

STORAGE_SERVER_URL_ENV = "STORAGE_SERVER_URL"


def get_storage_server_url() -> str:
    url = os.environ.get(STORAGE_SERVER_URL_ENV, "http://localhost:8000").strip()
    if not url:
        raise RuntimeError(f"Missing {STORAGE_SERVER_URL_ENV} in environment")
    return url.rstrip("/")

