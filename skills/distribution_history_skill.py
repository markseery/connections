"""
License: MIT
Description: Distribution history skill for equity/ETF symbols.

Fetches per-share distribution history from yfinance and StockAnalysis,
compares source agreement, and returns confidence/scoring signals.

Input:
- GET /signal/{symbol}
- POST /signal with {"symbol": "..."}
- GET /compare/{symbol}
- POST /compare with {"symbol": "...", "limit": 50}

Requires:
- internet access to Yahoo Finance and StockAnalysis
- ``common.compound.finance_pipeline`` distribution-history modules
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.compound.finance_pipeline.distribution_history_comparison import (
    DistributionHistoryComparison,
)
from common.simple.skill_response import skill_result

router = APIRouter()


class SymbolRequest(BaseModel):
    symbol: str = Field(..., min_length=1, description="Ticker symbol (e.g. SCHD, JEPI, VOO)")


class CompareRequest(BaseModel):
    symbol: str = Field(..., min_length=1, description="Ticker symbol")
    limit: int = Field(default=50, ge=1, le=500, description="Maximum row count")


def _comparison(symbol: str) -> DistributionHistoryComparison:
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    try:
        return DistributionHistoryComparison(sym)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid symbol: {exc}") from exc


@router.get("/signal/{symbol}")
def signal_get(symbol: str) -> dict[str, Any]:
    """Dividend/distribution signal for one symbol. Replace {symbol} with ticker."""
    cmp = _comparison(symbol)
    try:
        signal = cmp.dividend_history_signal()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"distribution signal failed: {exc}") from exc

    sym = symbol.strip().upper()
    aum_part = ""
    if signal.get("aum_display") or signal.get("aum_usd") is not None:
        aum_part = f", AUM={signal.get('aum_display') or signal.get('aum_usd')}"
    summary = (
        f"Distribution signal for **{sym}**: "
        f"source={signal.get('source')}, "
        f"rate_30_day_per_share={signal.get('rate_30_day_per_share')}, "
        f"confidence={signal.get('confidence_score')}{aum_part}."
    )
    return skill_result(summary=summary, symbol=sym, signal=signal)


@router.post("/signal")
def signal_post(body: SymbolRequest) -> dict[str, Any]:
    """POST variant for workflow skill steps. Body: {"symbol": "..."}."""
    return signal_get(body.symbol)


@router.get("/compare/{symbol}")
def compare_get(symbol: str, limit: int = 50) -> dict[str, Any]:
    """Side-by-side yfinance vs StockAnalysis distributions for one symbol."""
    cmp = _comparison(symbol)
    try:
        rows = cmp.side_by_side()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"distribution compare failed: {exc}") from exc

    sym = symbol.strip().upper()
    clipped = rows[: max(1, min(int(limit), 500))]
    return skill_result(
        summary=f"Distribution comparison for **{sym}** with **{len(clipped)}** row(s).",
        symbol=sym,
        count=len(clipped),
        rows=clipped,
    )


@router.post("/compare")
def compare_post(body: CompareRequest) -> dict[str, Any]:
    """POST variant for workflow skill steps. Body: {"symbol": "...", "limit": 50}."""
    return compare_get(body.symbol, body.limit)


def get_router() -> APIRouter:
    return router

