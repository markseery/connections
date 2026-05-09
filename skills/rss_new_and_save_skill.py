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
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.simple.skill_response import skill_result

from common.complex.skill_lifecycle import find_live_worker
from common.compound.skill_config import SkillConfig

router = APIRouter()

REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
STORAGE_NAMESPACE = "rss_notified"
_conf = SkillConfig("rss_new_and_save_skill")

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


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


def _poll_rss_new_skill_job(worker_url: str, job_id: str) -> dict[str, Any]:
    """Poll rss_new_skill until its job completes or fails. No timeout — runs until done."""
    poll_url = f"{worker_url}/skills/rss_new_skill/jobs/{job_id}"
    interval = float(_conf.get("poll_interval", 5.0))
    while True:
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.get(poll_url)
                r.raise_for_status()
            data = r.json()
        except Exception:
            time.sleep(interval)
            continue
        status = data.get("status", "unknown")
        if status == "completed":
            return data.get("result", {})
        if status == "failed":
            raise RuntimeError(f"rss_new_skill job failed: {data.get('error', 'unknown')}")
        time.sleep(interval)


def _execute_run(body: RunRequest) -> dict[str, Any]:
    """Core logic: submit to rss_new_skill, poll until done, persist to storage."""
    list_name = body.list_name.strip()
    dry_run = body.dry_run

    worker_url = (body.worker_url or "").strip().rstrip("/")
    if not worker_url:
        w = find_live_worker(REGISTRY_URL)
        if not w:
            raise RuntimeError("No live worker in registry")
        worker_url = w.rstrip("/")

    payload = {
        "list_name": list_name,
        "dry_run": dry_run,
        "worker_url": worker_url,
        "debug": body.debug,
        "skip_content": body.warmup,
    }
    t0 = time.perf_counter()

    with httpx.Client(timeout=30.0) as client:
        r = client.post(f"{worker_url}/skills/rss_new_skill/run", json=payload)
        r.raise_for_status()
    submit_data = r.json()
    inner_job_id = submit_data.get("job_id")
    if not inner_job_id:
        raise RuntimeError("rss_new_skill did not return a job_id")

    print(f"[rss_new_and_save_skill] rss_new_skill job submitted: {inner_job_id}", file=sys.stderr, flush=True)
    data = _poll_rss_new_skill_job(worker_url, inner_job_id)

    elapsed = time.perf_counter() - t0
    n_new = data.get("new_items_count") or 0
    print(f"[rss_new_and_save_skill] rss_new_skill completed in {elapsed:.2f}s: {n_new} new items",
          file=sys.stderr, flush=True)

    new_item_ids = data.get("new_item_ids") or []
    persisted_count = 0
    persist_errors: list[str] = []

    if not dry_run and new_item_ids:
        try:
            storage_base = _storage_url()
        except Exception as e:
            raise RuntimeError(f"Storage: {e}") from e
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
        print(f"[rss_new_and_save_skill] Persist done in {elapsed:.2f}s: {persisted_count} written",
              file=sys.stderr, flush=True)

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


@router.post("/run")
def run(body: RunRequest) -> dict[str, Any]:
    """Accept a run request, execute in background, return job_id for polling.
    Poll GET /jobs/{job_id} for status and results."""
    job_id = str(uuid.uuid4())

    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "result": None,
            "error": None,
        }

    def _bg() -> None:
        try:
            result = _execute_run(body)
            with _jobs_lock:
                _jobs[job_id]["status"] = "completed"
                _jobs[job_id]["result"] = result
                _jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            with _jobs_lock:
                _jobs[job_id]["status"] = "failed"
                _jobs[job_id]["error"] = str(exc)
                _jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()

    threading.Thread(target=_bg, daemon=True).start()

    return {"job_id": job_id, "status": "running", "poll": f"/skills/rss_new_and_save_skill/jobs/{job_id}"}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    """Poll job status. Returns full result when completed."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    out: dict[str, Any] = {
        "job_id": job_id,
        "status": job["status"],
        "started_at": job.get("started_at"),
    }
    if job["status"] == "completed":
        out["result"] = job["result"]
        out["completed_at"] = job.get("completed_at")
    elif job["status"] == "failed":
        out["error"] = job["error"]
        out["completed_at"] = job.get("completed_at")
    return out


def get_router() -> APIRouter:
    return router
