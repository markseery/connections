"""
License: MIT
Description: Chat server config. Reads registry URL from env so the server can discover the AI server.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path)

REGISTRY_SERVER_URL_ENV = "REGISTRY_SERVER_URL"


def get_registry_url() -> str:
    url = os.environ.get(REGISTRY_SERVER_URL_ENV, "http://127.0.0.1:7002").strip()
    return url.rstrip("/") if url else "http://127.0.0.1:7002"
