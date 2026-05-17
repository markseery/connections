"""Fetch OHLCV history from yfinance as JSON-serializable record lists."""

from __future__ import annotations

from typing import Any, Dict, List


def history_to_records(hist: Any) -> List[Dict[str, Any]]:
    if hist is None or getattr(hist, "empty", True):
        return []
    df = hist.reset_index()
    records = df.to_dict("records")
    out: List[Dict[str, Any]] = []
    for row in records:
        if "Date" in row and row["Date"] is not None:
            row = dict(row)
            row["Date"] = str(row["Date"])
        out.append(row)
    return out


def fetch_history_records(symbol: str, period: str = "5y") -> List[Dict[str, Any]]:
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    import yfinance as yf  # type: ignore

    hist = yf.Ticker(sym).history(period=period, auto_adjust=True)
    return history_to_records(hist)


def fetch_histories_batch(
    tickers: List[str],
    period: str,
) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for tk in tickers:
        try:
            out[tk] = fetch_history_records(tk, period)
        except Exception:
            out[tk] = []
    return out
