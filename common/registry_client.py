"""
License: MIT
Description: Cached registry client. Server URLs are resolved once and cached.
Cache is invalidated on connection errors so recovery is automatic after restarts.

Shared httpx.Client with connection pooling for all outbound calls.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import httpx
from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_env_path)

_REGISTRY_ENV = "REGISTRY_SERVER_URL"
_DEFAULT_REGISTRY = "http://127.0.0.1:7002"

_lock = threading.Lock()
_url_cache: dict[str, str] = {}

# Module-level pooled client (thread-safe by httpx design)
_http_client: httpx.Client | None = None


def get_registry_url() -> str:
    return os.environ.get(_REGISTRY_ENV, _DEFAULT_REGISTRY).strip().rstrip("/")


def _client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=180.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


def get_http_client() -> httpx.Client:
    """Return the shared pooled httpx.Client."""
    return _client()


def get_server_url(server_name: str, *, force_refresh: bool = False) -> str:
    """Resolve a server URL from the registry. Cached after first lookup."""
    if not force_refresh:
        cached = _url_cache.get(server_name)
        if cached:
            return cached

    with _lock:
        if not force_refresh:
            cached = _url_cache.get(server_name)
            if cached:
                return cached
        registry = get_registry_url()
        try:
            r = _client().get(f"{registry}/servers/{server_name}")
            r.raise_for_status()
            url = (r.json() or {}).get("url", "").strip().rstrip("/")
            if not url:
                raise ValueError(f"Registry response missing 'url' for {server_name}")
            _url_cache[server_name] = url
            return url
        except (httpx.ConnectError, httpx.TimeoutException):
            _url_cache.pop(server_name, None)
            raise


def invalidate_cache(server_name: str | None = None) -> None:
    """Clear cached URL(s). Call when a server is known to have restarted."""
    with _lock:
        if server_name:
            _url_cache.pop(server_name, None)
        else:
            _url_cache.clear()
