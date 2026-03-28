"""
License: MIT
Description: Batcher skill — iterate over stored records in a namespace+key
in controlled batches.

Callers first hit /info to learn the total record count, then call /batch
repeatedly with offset/limit to page through results.

Requires: registry, storage.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from common.simple.skill_response import skill_result
from common.compound.skill_config import SkillConfig

router = APIRouter()
_conf = SkillConfig("batcher_skill")

REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")

_cached_storage_url: str | None = None


def _storage_url() -> str:
    global _cached_storage_url
    if _cached_storage_url:
        return _cached_storage_url
    env_url = os.environ.get("STORAGE_SERVER_URL", "").strip().rstrip("/")
    if env_url:
        _cached_storage_url = env_url
        return env_url
    with httpx.Client(timeout=_conf.get("registry_timeout", 5.0)) as client:
        r = client.get(f"{REGISTRY_URL}/servers/storage")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise RuntimeError("Storage server not found in registry")
        _cached_storage_url = str(url).rstrip("/")
        return _cached_storage_url


def _record_url(base: str, namespace: str, key: str) -> str:
    return f"{base}/namespaces/{quote(namespace, safe='')}/records/{quote(key, safe='')}"


def _list_keys(namespace: str, prefix: str | None = None) -> list[str]:
    base = _storage_url()
    ns_encoded = quote(namespace, safe="")
    params: dict[str, str] = {}
    if prefix:
        params["prefix"] = prefix
    with httpx.Client(timeout=_conf.get("storage_timeout", 15.0)) as client:
        r = client.get(f"{base}/namespaces/{ns_encoded}/records", params=params)
        r.raise_for_status()
    data = r.json()
    return data.get("keys", [])


def _get_record(namespace: str, key: str) -> dict[str, Any] | None:
    base = _storage_url()
    with httpx.Client(timeout=_conf.get("storage_timeout", 15.0)) as client:
        r = client.get(_record_url(base, namespace, key))
        if r.status_code == 404:
            return None
        r.raise_for_status()
    data = r.json()
    return data.get("value")


def _get_records_bulk(namespace: str, keys: list[str]) -> list[dict[str, Any]]:
    base = _storage_url()
    records: list[dict[str, Any]] = []
    with httpx.Client(timeout=_conf.get("storage_timeout", 30.0)) as client:
        for key in keys:
            r = client.get(_record_url(base, namespace, key))
            if r.status_code == 404:
                continue
            r.raise_for_status()
            data = r.json()
            val = data.get("value")
            if isinstance(val, dict):
                val["_key"] = key
                records.append(val)
    return records


class InfoRequest(BaseModel):
    namespace: str = Field(..., description="Storage namespace to query")
    prefix: str | None = Field(None, description="Optional key prefix filter")


class BatchRequest(BaseModel):
    namespace: str = Field(..., description="Storage namespace to query")
    prefix: str | None = Field(None, description="Optional key prefix filter")
    offset: int = Field(0, ge=0, description="Number of records to skip")
    limit: int = Field(50, ge=1, le=1000, description="Max records to return (1-1000)")


@router.post("/info")
def batch_info(req: InfoRequest) -> dict[str, Any]:
    """Return total record count for a namespace (optionally prefix-filtered).

    Use this before calling /batch to know how many records exist and
    how many batch calls you need.
    """
    try:
        keys = _list_keys(req.namespace, req.prefix)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Storage error: {exc}") from exc

    return skill_result(
        summary=f"{len(keys)} records in namespace '{req.namespace}'"
                + (f" with prefix '{req.prefix}'" if req.prefix else ""),
        total=len(keys),
        namespace=req.namespace,
        prefix=req.prefix,
    )


@router.post("/batch")
def batch_records(req: BatchRequest) -> dict[str, Any]:
    """Return a slice of records from a namespace.

    Callers should iterate by incrementing offset by limit on each call
    until offset >= total (from /info).
    """
    try:
        all_keys = _list_keys(req.namespace, req.prefix)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Storage error: {exc}") from exc

    total = len(all_keys)
    page_keys = all_keys[req.offset : req.offset + req.limit]

    if not page_keys:
        return skill_result(
            summary=f"No records at offset {req.offset} (total {total})",
            items=[],
            total=total,
            offset=req.offset,
            limit=req.limit,
            returned=0,
            has_more=False,
            namespace=req.namespace,
            prefix=req.prefix,
        )

    try:
        records = _get_records_bulk(req.namespace, page_keys)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Storage error: {exc}") from exc

    has_more = (req.offset + req.limit) < total
    next_offset = req.offset + req.limit if has_more else None

    return skill_result(
        summary=f"Returned {len(records)} of {total} records "
                f"(offset {req.offset}, limit {req.limit})",
        items=records,
        total=total,
        offset=req.offset,
        limit=req.limit,
        returned=len(records),
        has_more=has_more,
        next_offset=next_offset,
        namespace=req.namespace,
        prefix=req.prefix,
    )


@router.get("/info")
def batch_info_get(
    namespace: str = Query(..., description="Storage namespace to query"),
    prefix: str | None = Query(None, description="Optional key prefix filter"),
) -> dict[str, Any]:
    """GET variant of /info for convenience."""
    return batch_info(InfoRequest(namespace=namespace, prefix=prefix))


@router.get("/batch")
def batch_records_get(
    namespace: str = Query(..., description="Storage namespace to query"),
    prefix: str | None = Query(None, description="Optional key prefix filter"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=1000, description="Max records to return"),
) -> dict[str, Any]:
    """GET variant of /batch for convenience."""
    return batch_records(BatchRequest(
        namespace=namespace, prefix=prefix, offset=offset, limit=limit,
    ))


def get_router() -> APIRouter:
    return router
