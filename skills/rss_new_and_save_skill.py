"""
License: MIT
Description: RSS new-item fetcher + storage update. Calls rss_new_skill (single source of truth)
then persists new item IDs to storage (rss_notified namespace). No notification; use with
notification_skill or rss_notify_new.py for email.

Input: list_name (required), dry_run (optional), worker_url (optional).
Requires: registry, storage, rss_new_skill (loaded on worker).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.skill_response import skill_result

from common.skill_lifecycle import find_live_worker
from common.skill_config import SkillConfig

router = APIRouter()

REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
STORAGE_NAMESPACE = "rss_notified"
# rss_new_skill fetches many feeds then fetches article content per item (Google News = 2–3 requests per item)
_conf = SkillConfig("rss_new_and_save_skill")


class RunRequest(BaseModel):
    list_name: str = Field(..., min_length=1, description="Feed list name in data/lists (e.g. ai-news)")
    dry_run: bool = Field(default=False, description="If true, no storage writes (still calls rss_new_skill)")
    worker_url: str | None = Field(default=None, description="Worker base URL; if omitted, discovered from registry")
    debug: bool = Field(default=False, description="If true, pass to rss_new_skill for fetch_debug in response")
    warmup: bool = Field(default=False, description="If true, skip content fetch; only save link IDs to storage")


def _storage_url() -> str:
    env_url = os.environ.get("STORAGE_SERVER_URL", "").strip().rstrip("/")
    if env_url:
        return env_url
    with httpx.Client(timeout=_conf.get("registry_timeout", 5.0)) as client:
        r = client.get(f"{REGISTRY_URL}/servers/storage")
        r.raise_for_status()
        u = (r.json() or {}).get("url")
        if not u:
            raise ValueError("Registry has no storage url")
        return str(u).rstrip("/")


def _persist_item(storage_base: str, item_id: str) -> None:
    key = quote(item_id, safe="")
    url = f"{storage_base}/namespaces/{STORAGE_NAMESPACE}/records/{key}"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    body = {"link": item_id, "notified_at": now}
    with httpx.Client(timeout=_conf.get("storage_put_timeout", 10.0)) as client:
        r = client.put(url, json=body)
        r.raise_for_status()


@router.post("/run")
def run(body: RunRequest) -> dict[str, Any]:
    """Fetch new items from a feed list and persist to storage. Body: list_name (required), dry_run (optional), debug (optional). Use when user asks to fetch and save new RSS items for a list."""
    list_name = body.list_name.strip()
    dry_run = body.dry_run

    worker_url = (body.worker_url or "").strip().rstrip("/")
    if not worker_url:
        w = find_live_worker(REGISTRY_URL)
        if not w:
            raise HTTPException(status_code=503, detail="No live worker in registry")
        worker_url = w.rstrip("/")

    payload = {
        "list_name": list_name,
        "dry_run": dry_run,
        "worker_url": worker_url,
        "debug": body.debug,
        "skip_content": body.warmup,
    }
    t0 = time.perf_counter()
    with httpx.Client(timeout=_conf.get("rss_new_skill_timeout", 600.0)) as client:
        r = client.post(f"{worker_url}/skills/rss_new_skill/run", json=payload)
        r.raise_for_status()
    data = r.json()
    elapsed = time.perf_counter() - t0
    n_new = data.get("new_items_count") or 0
    print(f"[rss_new_and_save_skill] rss_new_skill run in {elapsed:.2f}s: {n_new} new items", file=sys.stderr, flush=True)

    # Update storage with new item IDs unless dry_run
    new_item_ids = data.get("new_item_ids") or []
    persisted_count = 0
    persist_errors: list[str] = []

    if not dry_run and new_item_ids:
        try:
            storage_base = _storage_url()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Storage: {e}") from e
        n_total = len(new_item_ids)
        print(f"[rss_new_and_save_skill] Persisting {n_total} IDs to storage...", file=sys.stderr, flush=True)
        t0 = time.perf_counter()
        for idx, iid in enumerate(new_item_ids):
            try:
                _persist_item(storage_base, iid)
                persisted_count += 1
            except Exception as e:
                persist_errors.append(f"{iid!r}: {e}")
            if (idx + 1) % int(_conf.get("persist_log_interval", 50)) == 0 or (idx + 1) == n_total:
                print(f"[rss_new_and_save_skill]   persisted {idx + 1}/{n_total}", file=sys.stderr, flush=True)
        elapsed = time.perf_counter() - t0
        print(f"[rss_new_and_save_skill] Persist done in {elapsed:.2f}s: {persisted_count} written", file=sys.stderr, flush=True)

    upstream_summary = data.get("summary") or ""
    upstream_items = data.get("items") or []
    upstream_data = data.get("data") or {}
    if not isinstance(upstream_data, dict):
        upstream_data = {}

    summary = upstream_summary
    if persisted_count:
        summary = summary.rstrip(". ") + f" Persisted **{persisted_count}** to storage."

    extra = dict(upstream_data)
    extra["persisted_count"] = persisted_count
    if persist_errors:
        extra["persist_errors"] = persist_errors
    for k in ("ok", "list_name", "dry_run", "feeds_count", "already_notified_count",
              "new_items_count", "new_items", "new_item_ids", "errors", "fetch_debug"):
        if k in data and k not in extra:
            extra[k] = data[k]

    return skill_result(summary=summary, items=upstream_items, **extra)


def get_router() -> APIRouter:
    return router
