"""Portfolio market analysis: indicators, stability, SEC growth, summaries."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

import httpx

from common.compound.market_analysis_summaries import build_summaries, format_summaries_text
from common.compound.market_indicators_compute import compute_indicators_snapshot
from common.compound.price_stability import (
    DEFAULT_EQUITY_BENCHMARKS,
    TREASURY_10Y_TICKER,
    PriceStabilityAnalyzer,
    PriceStabilityResult,
    median_annual_price_growth,
)
from common.compound.sec_fundamentals_compute import compute_sec_growth
from common.compound.yfinance_history import fetch_histories_batch, fetch_history_records


def _dc(obj: Any) -> Any:
    if obj is None:
        return None
    if is_dataclass(obj):
        return asdict(obj)
    return obj


def _unwrap_skill_data(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


class PortfolioMarketHttpClient:
    """Call worker skills over HTTP (CLI / external orchestration)."""

    def __init__(self, worker_url: str, *, timeout: float = 120.0) -> None:
        self.base = worker_url.rstrip("/")
        self.timeout = timeout

    def fetch_history(self, symbol: str, period: str) -> List[Dict[str, Any]]:
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(
                f"{self.base}/skills/stock_skill/history/{symbol}",
                params={"period": period},
            )
            r.raise_for_status()
            data = _unwrap_skill_data(r.json())
            hist = data.get("history")
            return hist if isinstance(hist, list) else []

    def compute_indicators(self, symbol: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(
                f"{self.base}/skills/market_indicators_skill/analyze",
                json={"symbol": symbol, "history": history},
            )
            r.raise_for_status()
            data = _unwrap_skill_data(r.json())
            snap = data.get("snapshot")
            return snap if isinstance(snap, dict) else data

    def compute_sec(self, symbol: str, sleep_sec: float) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(
                f"{self.base}/skills/sec_fundamentals_skill/analyze",
                json={"symbol": symbol, "sleep_sec": sleep_sec},
            )
            r.raise_for_status()
            return _unwrap_skill_data(r.json())


def build_stability_analyzer(stability_period: str) -> PriceStabilityAnalyzer:
    bench_tickers = list(DEFAULT_EQUITY_BENCHMARKS) + [TREASURY_10Y_TICKER]
    bench_hist = fetch_histories_batch(bench_tickers, stability_period)
    treasury_hist = bench_hist.get(TREASURY_10Y_TICKER) or []
    stability = PriceStabilityAnalyzer(treasury_yield_history=treasury_hist)
    for b in DEFAULT_EQUITY_BENCHMARKS:
        stability.set_benchmark_history(b, bench_hist.get(b) or [])
    return stability


def analyze_symbol(
    symbol: str,
    history: List[Dict[str, Any]],
    *,
    period: str = "5y",
    stability: PriceStabilityAnalyzer | None = None,
    sec_sleep_sec: float = 0.12,
    http_client: PortfolioMarketHttpClient | None = None,
) -> Dict[str, Any]:
    sym = symbol.strip().upper()
    if http_client is not None:
        if not history:
            history = http_client.fetch_history(sym, period)
        ind = http_client.compute_indicators(sym, history)
        sec_part = http_client.compute_sec(sym, sec_sleep_sec)
    else:
        ind = compute_indicators_snapshot(sym, history)
        sec_part = compute_sec_growth(sym, sleep_sec=sec_sleep_sec)

    ps_r: PriceStabilityResult | None = None
    if stability is not None:
        ps_r = stability.analyze(sym, history)

    snap: Dict[str, Any] = {
        **ind,
        "price_stability": _dc(ps_r) if ps_r else None,
        "price_growth": median_annual_price_growth(history, years=5),
        "sec": sec_part.get("sec"),
        "growth": sec_part.get("growth"),
    }
    return snap


def run_portfolio_analysis(
    symbols: List[str],
    *,
    period: str = "5y",
    stability_period: str | None = None,
    sec_sleep_sec: float = 0.12,
    include_summaries: bool = True,
    summary_format: str = "text",
    max_workers: int | None = None,
    http_client: PortfolioMarketHttpClient | None = None,
    bollinger_band_pct: float = 5.0,
    calmer_than_spy_max_ratio: float = 0.9,
) -> Dict[str, Any]:
    """
    Run full portfolio analysis.

    Price history and indicators are fetched/computed in parallel; SEC requests run
    sequentially to respect rate limits.
    """
    stab_period = (stability_period or period).strip()
    syms = [(s or "").strip().upper() for s in symbols if (s or "").strip()]
    if not syms:
        return {
            "snapshots": [],
            "summaries": {},
            "summaries_text": "",
            "benchmarks_loaded": list(DEFAULT_EQUITY_BENCHMARKS) + [TREASURY_10Y_TICKER],
        }

    stability = build_stability_analyzer(stab_period)
    workers = max_workers if max_workers is not None else min(8, max(1, len(syms)))
    histories: Dict[str, List[Dict[str, Any]]] = {}

    def _load_history(sym: str) -> tuple[str, List[Dict[str, Any]]]:
        if http_client is not None:
            return sym, http_client.fetch_history(sym, period)
        try:
            return sym, fetch_history_records(sym, period)
        except Exception:
            return sym, []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_load_history, s) for s in syms]
        for fut in as_completed(futs):
            sym, hist = fut.result()
            histories[sym] = hist

    snapshots: List[Dict[str, Any]] = []
    for sym in syms:
        history = histories.get(sym) or []
        snap = analyze_symbol(
            sym,
            history,
            period=period,
            stability=stability,
            sec_sleep_sec=sec_sleep_sec,
            http_client=http_client,
        )
        snapshots.append(snap)

    result: Dict[str, Any] = {
        "snapshots": snapshots,
        "benchmarks_loaded": list(DEFAULT_EQUITY_BENCHMARKS) + [TREASURY_10Y_TICKER],
        "period": period,
        "stability_period": stab_period,
    }

    if include_summaries:
        summaries = build_summaries(
            snapshots,
            bollinger_band_pct=bollinger_band_pct,
            calmer_than_spy_max_ratio=calmer_than_spy_max_ratio,
        )
        result["summaries"] = summaries
        if summary_format == "text":
            result["summaries_text"] = format_summaries_text(
                summaries,
                bollinger_band_pct=bollinger_band_pct,
                calmer_than_spy_max_ratio=calmer_than_spy_max_ratio,
            )
        else:
            result["summaries_text"] = ""

    return result


def run_via_worker(
    worker_url: str,
    body: Dict[str, Any],
    *,
    timeout: float = 600.0,
) -> Dict[str, Any]:
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            f"{worker_url.rstrip('/')}/skills/portfolio_market_analysis_skill/run",
            json=body,
        )
        r.raise_for_status()
        return _unwrap_skill_data(r.json())
