"""
License: MIT
Description: Batch recommender skill — given a storage namespace, key prefix,
and AI profile, returns an optimally-sized batch plan for adaptive content
processing.

Combines storage inventory (record count and content sizes) with AI model
context window information to produce batch boundaries that maximise content
per AI call without exceeding the model's capacity.

Requires: registry, storage, aiserver.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.simple.skill_response import skill_result
from common.compound.skill_config import SkillConfig

router = APIRouter()
_conf = SkillConfig("batch_recommender_skill")

REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")

_cached_storage_url: str | None = None
_cached_aiserver_url: str | None = None


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


def _aiserver_url() -> str:
    global _cached_aiserver_url
    if _cached_aiserver_url:
        return _cached_aiserver_url
    env_url = os.environ.get("AISERVER_URL", "").strip().rstrip("/")
    if env_url:
        _cached_aiserver_url = env_url
        return env_url
    with httpx.Client(timeout=_conf.get("registry_timeout", 5.0)) as client:
        r = client.get(f"{REGISTRY_URL}/servers/aiserver")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise RuntimeError("AI server not found in registry")
        _cached_aiserver_url = str(url).rstrip("/")
        return _cached_aiserver_url


# ── storage helpers ──────────────────────────────────────────────────────

def _list_keys(namespace: str, prefix: str | None = None) -> list[str]:
    base = _storage_url()
    ns_encoded = quote(namespace, safe="")
    params: dict[str, str] = {}
    if prefix:
        params["prefix"] = prefix
    with httpx.Client(timeout=_conf.get("storage_timeout", 15.0)) as client:
        r = client.get(f"{base}/namespaces/{ns_encoded}/records", params=params)
        r.raise_for_status()
    return r.json().get("keys", [])


def _get_record_value(client: httpx.Client, base: str, namespace: str, key: str) -> Any:
    ns_enc = quote(namespace, safe="")
    key_enc = quote(key, safe="")
    r = client.get(f"{base}/namespaces/{ns_enc}/records/{key_enc}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json().get("value")


def _measure_content_sizes(namespace: str, keys: list[str]) -> list[int]:
    """Fetch records in chunks and return content length for each key."""
    base = _storage_url()
    chunk_size = int(_conf.get("fetch_chunk", 200))
    sizes: list[int] = []
    with httpx.Client(timeout=_conf.get("storage_timeout", 30.0)) as client:
        for i in range(0, len(keys), chunk_size):
            chunk_keys = keys[i : i + chunk_size]
            for key in chunk_keys:
                val = _get_record_value(client, base, namespace, key)
                if isinstance(val, dict):
                    sizes.append(len(str(val.get("content", ""))))
                elif isinstance(val, str):
                    sizes.append(len(val))
                else:
                    sizes.append(0)
    return sizes


# ── AI model info ────────────────────────────────────────────────────────

def _get_model_info(ai_profile: str) -> dict[str, Any]:
    url = _aiserver_url()
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{url}/model-info", params={"profile": ai_profile})
        r.raise_for_status()
    return r.json()


# ── batch planning (extracted from batch_analysis.py) ────────────────────

def _plan_batches(page_sizes: list[int], content_budget: int) -> list[dict[str, Any]]:
    batches: list[dict[str, Any]] = []
    start = 0
    n = len(page_sizes)
    batch_num = 1
    while start < n:
        total = 0
        end = start
        while end < n:
            if total + page_sizes[end] > content_budget and end > start:
                break
            total += page_sizes[end]
            end += 1
        batches.append({
            "batch": batch_num,
            "start": start,
            "end": end,
            "records": end - start,
            "chars": total,
        })
        batch_num += 1
        start = end
    return batches


# ── request / response models ────────────────────────────────────────────

class RecommendRequest(BaseModel):
    namespace: str = Field(..., description="Storage namespace to query")
    prefix: str = Field(..., description="Key prefix filter (e.g. 'https://coreweave.com\\x00')")
    ai_profile: str = Field("agent", description="AI profile name for model lookup")
    context_pct: float = Field(0.80, ge=0.1, le=0.95, description="Fraction of context window to use")
    chars_per_token: float = Field(3.5, gt=0, description="Estimated characters per token")
    prompt_overhead: int = Field(2000, ge=0, description="Chars reserved for prompt template/instructions")


# ── endpoint ─────────────────────────────────────────────────────────────

@router.post("/recommend")
def recommend(req: RecommendRequest) -> dict[str, Any]:
    """Produce an adaptive batch plan for processing stored records with an AI model.

    1. Lists all keys matching namespace + prefix from storage.
    2. Measures content size for each record.
    3. Queries the AI server for model context window.
    4. Computes a content budget and plans optimally-sized batches.

    Returns the batch plan with start/end indices, record counts, and char totals.
    """
    try:
        keys = _list_keys(req.namespace, req.prefix)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Storage error listing keys: {exc}") from exc

    total_records = len(keys)
    if total_records == 0:
        return skill_result(
            summary=f"No records found in namespace '{req.namespace}' with prefix '{req.prefix}'",
            total_records=0,
            batches=[],
        )

    try:
        page_sizes = _measure_content_sizes(req.namespace, keys)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Storage error measuring sizes: {exc}") from exc

    total_chars = sum(page_sizes)
    non_empty = sum(1 for s in page_sizes if s > 0)

    try:
        model_info = _get_model_info(req.ai_profile)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI server error: {exc}") from exc

    model_name = model_info.get("model", "unknown")
    context_window = model_info.get("context_window", 128_000)
    context_source = model_info.get("context_window_source", "unknown")

    content_budget = int(context_window * req.chars_per_token * req.context_pct) - req.prompt_overhead
    content_budget = max(content_budget, 10_000)

    batches = _plan_batches(page_sizes, content_budget)

    return skill_result(
        summary=(
            f"{len(batches)} batch(es) planned for {non_empty} records "
            f"({total_chars:,} chars) using {model_name} "
            f"(budget: {content_budget:,} chars/batch)"
        ),
        total_records=total_records,
        non_empty_records=non_empty,
        total_chars=total_chars,
        model=model_name,
        context_window=context_window,
        context_window_source=context_source,
        content_budget=content_budget,
        context_pct=req.context_pct,
        chars_per_token=req.chars_per_token,
        prompt_overhead=req.prompt_overhead,
        num_batches=len(batches),
        batches=batches,
    )


@router.get("/recommend")
def recommend_get(
    namespace: str,
    prefix: str,
    ai_profile: str = "agent",
    context_pct: float = 0.80,
    chars_per_token: float = 3.5,
    prompt_overhead: int = 2000,
) -> dict[str, Any]:
    """GET variant of /recommend for convenience."""
    return recommend(RecommendRequest(
        namespace=namespace, prefix=prefix, ai_profile=ai_profile,
        context_pct=context_pct, chars_per_token=chars_per_token,
        prompt_overhead=prompt_overhead,
    ))


def get_router() -> APIRouter:
    return router
