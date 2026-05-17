"""
License: MIT
Description: Portfolio market analysis — indicators, price stability, SEC growth, summaries.

Workflow example (POST body to /run)::

    {"symbols": ["GLD", "SPY"], "period": "5y", "include_summaries": true}

CLI: ``python scripts/run_symbol_market_analysis.py --config ...``
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.compound.market_analysis_summaries import (
    build_summaries,
    format_summaries_text,
)
from common.compound.portfolio_market_run import run_portfolio_analysis
from common.compound.skill_config import SkillConfig
from common.simple.skill_response import skill_result

router = APIRouter()
_conf = SkillConfig("portfolio_market_analysis_skill")


class RunRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1)
    period: str | None = None
    stability_period: str | None = None
    sec_sleep_sec: float | None = Field(default=None, ge=0)
    include_summaries: bool = True
    summary_format: str = Field(default="text", description="text or structured")
    max_workers: int | None = Field(default=None, ge=1, le=32)
    bollinger_band_pct: float | None = None
    calmer_than_spy_max_ratio: float | None = None


class SummarizeRequest(BaseModel):
    snapshots: list[dict[str, Any]] = Field(..., min_length=1)
    summary_format: str = "text"
    bollinger_band_pct: float | None = None
    calmer_than_spy_max_ratio: float | None = None


def _cfg_float(key: str, override: float | None, default: float) -> float:
    if override is not None:
        return float(override)
    return float(_conf.get(key, default))


@router.post("/run")
def run_post(body: RunRequest) -> dict[str, Any]:
    syms = [str(s).strip().upper() for s in body.symbols if str(s).strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="symbols is required")
    period = (body.period or _conf.get("default_period", "5y")).strip()
    sec_sleep = (
        float(body.sec_sleep_sec)
        if body.sec_sleep_sec is not None
        else float(_conf.get("default_sec_sleep_sec", 0.12))
    )
    boll = _cfg_float("bollinger_band_pct", body.bollinger_band_pct, 5.0)
    calmer = _cfg_float("calmer_than_spy_max_ratio", body.calmer_than_spy_max_ratio, 0.9)
    max_workers = body.max_workers
    if max_workers is None:
        max_workers = int(_conf.get("max_workers", 8))

    result = run_portfolio_analysis(
        syms,
        period=period,
        stability_period=body.stability_period,
        sec_sleep_sec=sec_sleep,
        include_summaries=body.include_summaries,
        summary_format=body.summary_format,
        max_workers=max_workers,
        bollinger_band_pct=boll,
        calmer_than_spy_max_ratio=calmer,
    )
    n = len(result.get("snapshots") or [])
    return skill_result(
        summary=f"Portfolio market analysis for **{n}** symbol(s).",
        **result,
    )


@router.post("/summarize")
def summarize_post(body: SummarizeRequest) -> dict[str, Any]:
    boll = _cfg_float("bollinger_band_pct", body.bollinger_band_pct, 5.0)
    calmer = _cfg_float("calmer_than_spy_max_ratio", body.calmer_than_spy_max_ratio, 0.9)
    summaries = build_summaries(
        body.snapshots,
        bollinger_band_pct=boll,
        calmer_than_spy_max_ratio=calmer,
    )
    text = ""
    if body.summary_format == "text":
        text = format_summaries_text(
            summaries,
            bollinger_band_pct=boll,
            calmer_than_spy_max_ratio=calmer,
        )
    return skill_result(
        summary=f"Summaries for **{len(body.snapshots)}** snapshot(s).",
        summaries=summaries,
        summaries_text=text,
    )


def get_router() -> APIRouter:
    return router
