"""
License: MIT
Description: SEC EDGAR companyfacts and fundamental growth (QoQ/YoY).

Set ``SEC_EDGAR_USER_AGENT`` in the environment (SEC policy).

Routes:
- POST /analyze — one symbol
- POST /analyze/batch — multiple symbols (sequential SEC pacing)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.compound.sec_fundamentals_compute import compute_sec_growth
from common.compound.skill_config import SkillConfig
from common.simple.skill_response import skill_result

router = APIRouter()
_conf = SkillConfig("sec_fundamentals_skill")


class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    sleep_sec: float | None = Field(
        default=None,
        ge=0,
        description="Pause before SEC request (default from skill config)",
    )


class BatchAnalyzeRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1)
    sleep_sec: float | None = None


def _sleep(body_sleep: float | None) -> float:
    if body_sleep is not None:
        return float(body_sleep)
    return float(_conf.get("default_sleep_sec", 0.12))


@router.post("/analyze")
def analyze_post(body: AnalyzeRequest) -> dict[str, Any]:
    sym = body.symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    snap = compute_sec_growth(sym, sleep_sec=_sleep(body.sleep_sec))
    entity = (snap.get("growth") or {}).get("entity") or sym
    return skill_result(
        summary=f"SEC fundamentals for **{sym}** ({entity!r}).",
        **snap,
    )


@router.post("/analyze/batch")
def analyze_batch_post(body: BatchAnalyzeRequest) -> dict[str, Any]:
    sleep = _sleep(body.sleep_sec)
    snapshots: list[dict[str, Any]] = []
    for raw in body.symbols:
        sym = str(raw or "").strip().upper()
        if not sym:
            continue
        snapshots.append(compute_sec_growth(sym, sleep_sec=sleep))
    return skill_result(
        summary=f"SEC fundamentals for **{len(snapshots)}** symbol(s).",
        count=len(snapshots),
        snapshots=snapshots,
    )


def get_router() -> APIRouter:
    return router
