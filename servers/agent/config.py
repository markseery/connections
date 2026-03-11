"""
License: MIT
Description: Agent server config. Resolves registry, aiserver, and configuration
server URLs from the registry (injected by start_app).
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path)

REGISTRY_SERVER_URL_ENV = "REGISTRY_SERVER_URL"


def get_registry_url() -> str:
    url = os.environ.get(REGISTRY_SERVER_URL_ENV, "http://127.0.0.1:7002").strip()
    return url.rstrip("/") if url else "http://127.0.0.1:7002"


def get_server_url(server_name: str) -> str:
    """Resolve a server's base URL from the registry."""
    registry = get_registry_url()
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{registry}/servers/{server_name}")
        r.raise_for_status()
        data = r.json()
        url = data.get("url")
        if not url:
            raise ValueError(f"Registry response missing 'url' for {server_name}")
        return url.rstrip("/")


def get_aiserver_url() -> str:
    return get_server_url("aiserver")


def get_config_server_url() -> str:
    return get_server_url("configuration")
