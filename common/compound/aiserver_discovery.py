"""
License: MIT
Description: Canonical resolution of the aiserver base URL for any code that
imports ``common`` (skills, CLIs, workers). Do not duplicate registry GET logic
elsewhere — use this module only.
"""

from __future__ import annotations

import os

# Dev fallback when the registry is unreachable or aiserver is not registered.
AISERVER_DEV_FALLBACK = "http://127.0.0.1:7012"


def get_aiserver_base_url(
    *,
    explicit: str | None = None,
    registry_override: str | None = None,
) -> str:
    """Return the aiserver base URL with no trailing slash.

    Resolution order:

    1. If ``explicit`` is set (e.g. CLI ``--url``), return it after stripping.
    2. If ``registry_override`` is set and differs from the current registry URL,
       set ``REGISTRY_SERVER_URL`` and clear the cached aiserver registration.
    3. ``get_server_url("aiserver")`` from the registry.
    4. On any failure, ``AISERVER_DEV_FALLBACK``.
    """
    if explicit:
        return explicit.rstrip("/")

    from common.compound.registry_client import (
        get_registry_url,
        get_server_url,
        invalidate_cache,
    )

    if registry_override is not None:
        want = registry_override.strip().rstrip("/")
        if want != get_registry_url():
            os.environ["REGISTRY_SERVER_URL"] = want
            invalidate_cache("aiserver")

    try:
        return get_server_url("aiserver")
    except Exception:
        return AISERVER_DEV_FALLBACK
