"""ETF top holdings via yfinance (Yahoo ``quoteSummary`` / ``topHoldings``), when HTML and other scrapes fail."""

from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFDataException

from common.compound.securitiesdb_etf_holdings import EtfHoldingsResult


class YfinanceEtfHoldingsError(RuntimeError):
    """yfinance has no fund/holdings data or parsing failed."""


def _sector_label(key: str) -> str:
    if not key or not str(key).strip():
        return str(key)
    k = str(key).replace("_", " ").strip()
    if not k:
        return str(key)
    return k.title()


def _funds_data_to_payload(_etf: str, *, funds_data: Any) -> dict[str, Any]:
    """Map ``yfinance`` ``FundsData`` to our ``data`` shape."""
    df: pd.DataFrame = funds_data.top_holdings
    if df is None or len(df) == 0:
        raise YfinanceEtfHoldingsError("empty top_holdings from yfinance")
    rows: list[dict[str, Any]] = []
    for sym_idx, row in df.iterrows():
        tick = str(sym_idx).strip().upper()
        if not tick:
            continue
        name = str(row["Name"]) if "Name" in row.index else ""
        hp = row["Holding Percent"] if "Holding Percent" in row.index else None
        if hp is None:
            continue
        try:
            w = float(hp) * 100.0
        except (TypeError, ValueError):
            continue
        rows.append({"ticker": tick, "name": name, "weight_pct": w})
    if not rows:
        raise YfinanceEtfHoldingsError("could not build holdings rows from yfinance DataFrame")
    out: dict[str, Any] = {
        "holdings": rows,
        "source": "yfinance",
        "data_backend": "yfinance_funds_data",
    }
    try:
        sw: dict[str, float] = funds_data.sector_weightings
        if sw and isinstance(sw, dict):
            out["sector_breakdown"] = {
                _sector_label(k): float(v) * 100.0 for k, v in sw.items() if v is not None
            }
    except Exception:  # noqa: BLE001
        pass
    return out


def fetch_etf_holdings_via_yfinance(ticker: str) -> EtfHoldingsResult:
    """
    Use ``yfinance.Ticker(…).funds_data`` (Yahoo querySummary API) for topHoldings.
    Weights are stored as ``weight_pct`` on the 0–100 scale like other providers.
    """
    sym = str(ticker or "").strip().upper()
    if not sym:
        raise ValueError("ticker must be non-empty")
    t = yf.Ticker(sym)
    try:
        fd = t.funds_data
    except (YFDataException, KeyError, IndexError) as ex:
        raise YfinanceEtfHoldingsError(f"yfinance has no fund data: {ex!s}") from ex
    except Exception as ex:  # noqa: BLE001
        raise YfinanceEtfHoldingsError(f"yfinance fund fetch failed: {ex!s}") from ex
    try:
        data = _funds_data_to_payload(sym, funds_data=fd)
    except YfinanceEtfHoldingsError:
        raise
    except Exception as ex:  # noqa: BLE001
        raise YfinanceEtfHoldingsError(str(ex)) from ex
    return EtfHoldingsResult(etf_ticker=sym, raw_data=data)


__all__ = [
    "YfinanceEtfHoldingsError",
    "fetch_etf_holdings_via_yfinance",
    "_funds_data_to_payload",
    "_sector_label",
]
