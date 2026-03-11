"""
License: MIT
Description: ChatAgent server configuration helpers.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path)


def get_registry_url() -> str:
    return os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").strip().rstrip("/")


def get_server_url(server_name: str) -> str:
    reg = get_registry_url()
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{reg}/servers/{server_name}")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError(f"Registry missing url for {server_name}")
        return str(url).rstrip("/")


def get_aiserver_url() -> str:
    return get_server_url("aiserver")


def get_config_url() -> str:
    return get_server_url("configuration")

