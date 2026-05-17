"""
License: MIT
Description: Technical indicators (SMA, EMA, RSI, MACD, Bollinger) from price history.

Routes:
- POST /analyze — one symbol (optional inline history or yfinance period)
- POST /analyze/batch — multiple symbols
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.compound.market_indicators_compute import compute_indicators_snapshot
from common.compound.yfinance_history import fetch_history_records
from common.compound.skill_config import SkillConfig
from common.simple.skill_response import skill_result

router = APIRouter()
_conf = SkillConfig("market_indicators_skill")


class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    history: list[dict[str, Any]] | None = None
    period: str | None = Field(
        default=None,
        description="yfinance period when history is omitted (default from skill config)",
    )


class BatchAnalyzeRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1)
    period: str | None = None


def _period(body_period: str | None) -> str:
    return (body_period or _conf.get("default_period", "5y")).strip()


@router.post("/analyze")
def analyze_post(body: AnalyzeRequest) -> dict[str, Any]:
    sym = body.symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    history = body.history
    if history is None:
        history = fetch_history_records(sym, _period(body.period))
    snap = compute_indicators_snapshot(sym, history or [])
    bars = snap.get("bars", 0)
    return skill_result(
        summary=f"Indicators for **{sym}** ({bars} bars).",
        symbol=sym,
        snapshot=snap,
        **{k: v for k, v in snap.items() if k != "symbol"},
    )


@router.post("/analyze/batch")
def analyze_batch_post(body: BatchAnalyzeRequest) -> dict[str, Any]:
    period = _period(body.period)
    snapshots: list[dict[str, Any]] = []
    for raw in body.symbols:
        sym = str(raw or "").strip().upper()
        if not sym:
            continue
        hist = fetch_history_records(sym, period)
        snapshots.append(compute_indicators_snapshot(sym, hist))
    return skill_result(
        summary=f"Indicators for **{len(snapshots)}** symbol(s).",
        count=len(snapshots),
        snapshots=snapshots,
    )


def get_router() -> APIRouter:
    return router
