"""
License: MIT
Description: Stock skill (connections) — quotes/fundamentals/earnings via yfinance.

Adapted from apps/agents stock_skill:
- Exposes an APIRouter (worker-loadable), not a standalone FastAPI app.
- No CryptoMiddleware/manifest; plain JSON.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf
from fastapi import APIRouter, HTTPException, Response

from common.skill_response import skill_result


router = APIRouter()


def _safe_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (int, bool, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, pd.Timestamp):
        try:
            if value.tzinfo is None:
                value = value.tz_localize(timezone.utc)
            return value.tz_convert(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception as exc:
            print(f"[stock_skill] timestamp conversion failed: {exc}", flush=True)
            return str(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return str(value)


def _dataframe_to_records(df: Any, date_key: str = "Date") -> list[dict[str, Any]]:
    if df is None or (hasattr(df, "empty") and df.empty) or not hasattr(df, "iterrows"):
        return []
    records: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        rec: dict[str, Any] = {date_key: _safe_value(idx)}
        for col in row.index:
            rec[str(col)] = _safe_value(row[col])
        records.append(rec)
    return records


def _ticker(symbol: str) -> yf.Ticker:
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    try:
        return yf.Ticker(sym)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/quote/{symbol}")
def quote(symbol: str, response: Response) -> dict[str, Any]:
    """Stock quote: price, change, volume. Replace {symbol} with ticker (e.g. AAPL). Use when user asks for stock price or quote."""
    start = time.perf_counter()
    t = _ticker(symbol)
    try:
        info = t.info or {}
        hist = t.history(period="2d")
        prev_close = None
        if hist is not None and len(hist) >= 2:
            prev_close = float(hist.iloc[-2]["Close"])
        current_price = info.get("regularMarketPrice") or info.get("currentPrice")
        if current_price is None and hist is not None and not hist.empty:
            current_price = float(hist.iloc[-1]["Close"])

        change = None
        change_pct = None
        if current_price is not None and prev_close:
            change = round(float(current_price) - prev_close, 2)
            change_pct = round((change / prev_close) * 100, 2)

        sym = symbol.strip().upper()
        price_val = _safe_value(current_price)
        change_pct_val = _safe_value(change_pct)
        price_str = f"${price_val}" if price_val is not None else "N/A"
        summary = f"**{sym}**: {price_str}" + (f" ({change_pct_val:+.2f}%)" if change_pct_val is not None else "") + "."
        return skill_result(
            summary=summary,
            symbol=sym,
            price=price_val,
            previous_close=_safe_value(prev_close),
            change=_safe_value(change),
            change_pct=change_pct_val,
            volume=_safe_value(info.get("regularMarketVolume") or info.get("volume")),
            market_cap=_safe_value(info.get("marketCap")),
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
    finally:
        response.headers["X-Processing-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.2f}"


@router.get("/fundamentals/{symbol}")
def fundamentals(symbol: str, response: Response) -> dict[str, Any]:
    """Stock fundamentals: PE, margins, growth. Replace {symbol} with ticker. Use when user asks for fundamentals or valuation."""
    start = time.perf_counter()
    t = _ticker(symbol)
    try:
        info = t.info or {}
        def _num(k: str) -> float | None:
            v = info.get(k)
            try:
                fv = float(v)
                if math.isnan(fv) or math.isinf(fv):
                    return None
                return fv
            except Exception as exc:
                print(f"[stock_skill] float conversion failed for {k}={v}: {exc}", flush=True)
                return None

        out = {
            "symbol": symbol.strip().upper(),
            "company": {
                "name": info.get("longName") or info.get("shortName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "country": info.get("country"),
                "website": info.get("website"),
            },
            "valuation": {
                "pe_ratio": _num("trailingPE"),
                "forward_pe": _num("forwardPE"),
                "peg_ratio": _num("pegRatio"),
                "price_to_book": _num("priceToBook"),
            },
            "growth": {
                "revenue_growth": _num("revenueGrowth"),
                "earnings_growth": _num("earningsGrowth"),
            },
            "profitability": {
                "gross_margin": _num("grossMargins"),
                "operating_margin": _num("operatingMargins"),
                "net_margin": _num("profitMargins"),
            },
        }
        safe = {k: _safe_value(v) for k, v in out.items()}
        return skill_result(summary=f"Fundamentals for **{symbol.strip().upper()}**.", **safe)
    finally:
        response.headers["X-Processing-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.2f}"


@router.get("/earnings/{symbol}")
def earnings(symbol: str, response: Response) -> dict[str, Any]:
    """Stock earnings dates and quarterly financials. Replace {symbol} with ticker. Use when user asks for earnings."""
    start = time.perf_counter()
    t = _ticker(symbol)
    try:
        ed = getattr(t, "earnings_dates", None)
        qf = getattr(t, "quarterly_financials", None)
        sym = symbol.strip().upper()
        return skill_result(
            summary=f"Earnings for **{sym}**.",
            symbol=sym,
            earnings_dates=_dataframe_to_records(ed)[:10],
            quarterly_financials=_dataframe_to_records(qf.T)[:8] if qf is not None else [],
        )
    finally:
        response.headers["X-Processing-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.2f}"


def get_router() -> APIRouter:
    return router

