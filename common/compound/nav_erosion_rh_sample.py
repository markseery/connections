#!/usr/bin/env python3
"""
Smoke-test NAV_Erosion_Analyzer on a sample of tickers from rh.symbols.yaml.

Uses yfinance: ETF Close as NAV proxy, benchmark Close as underlying, dividends as
distributions. BLOX / BTCI use **BTC-USD** (spot Bitcoin) as the benchmark. Not issuer
NAV — classifications are indicative only.

Usage (from repo root):
  python scripts/nav_erosion_rh_sample.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf

from common.simple import script_env

from common.compound.nav_erosion_analyzer import NAV_Erosion_Analyzer
from common.simple.yfinance_warnings import suppress_utcnow_deprecation_warning

suppress_utcnow_deprecation_warning()

# Rough benchmark map for names in rh.symbols.yaml (Nasdaq stack vs S&P stack).
_QQQ_LIKE = frozenset(
    {
        "QQQI",
        "GPIQ",
        "TDAQ",
        "JEPQ",
        "XQQI",
        "QYLD",
        "ROCQ",
        "TDAX",
        "QDTY",
        "KQQQ",
        "CHPY",
    }
)
_SPY_LIKE = frozenset(
    {
        "JEPI", "SPYH", "SPYI", "TSPY", "XSPI", "ROCY", "TSYX", "OMAH", "IWMI",
        "NEHI", "GDXY", "IAUI", "SLVX", "GIAX", "HYBI", "IYRI", "NIHI",
    }
)


def _underlying_for(sym: str) -> str:
    s = sym.strip().upper()
    if s in _QQQ_LIKE:
        return "QQQ"
    if s in _SPY_LIKE:
        return "SPY"
    if s in ("BLOX", "BTCI"):
        return "BTC-USD"
    return "SPY"


def _strip_tz(s: pd.Series) -> pd.Series:
    out = s.copy()
    if isinstance(out.index, pd.DatetimeIndex) and out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    return out


def _load_history(symbol: str, period: str = "730d") -> tuple[pd.Series, pd.Series]:
    t = yf.Ticker(symbol)
    hist = t.history(period=period, auto_adjust=True)
    if hist.empty:
        raise RuntimeError(f"No price history for {symbol}")
    nav = _strip_tz(hist["Close"].astype(float))
    divs = t.dividends
    if divs is None or len(divs) == 0:
        dist = pd.Series(dtype=float)
    else:
        dist = _strip_tz(divs.astype(float))
    return nav, dist


def main() -> int:
    rh_path = script_env.repo_root() / "application_files" / "config" / "rh.symbols.yaml"
    if not rh_path.is_file():
        print(f"Missing {rh_path}", file=sys.stderr)
        return 1
    raw = yaml.safe_load(rh_path.read_text(encoding="utf-8")) or {}
    positions = raw.get("positions") or []
    symbols = [str(p["symbol"]).strip().upper() for p in positions if isinstance(p, dict) and p.get("symbol")]

    # Diverse sample: mix QQQ/SPY benchmarks and one oddball.
    sample = ["QQQI", "JEPQ", "QYLD", "SPYI", "TSPY", "BLOX"]
    sample = [s for s in sample if s in symbols]
    if not sample:
        sample = symbols[:6]

    inception = datetime.now() - timedelta(days=540)

    print("NAV_Erosion_Analyzer check (yfinance proxy data)\n" + "=" * 72)
    for sym in sample:
        und_sym = _underlying_for(sym)
        try:
            nav, dist = _load_history(sym)
            und, _ = _load_history(und_sym)
        except Exception as exc:
            print(f"\n{sym} vs {und_sym}: FETCH ERROR — {exc}")
            continue

        analyzer = NAV_Erosion_Analyzer(ticker=sym, underlying_ticker=und_sym)
        try:
            analyzer.load_data(
                nav_series=nav,
                underlying_series=und,
                distributions=dist,
                inception_date=inception,
            )
        except Exception as exc:
            print(f"\n{sym} vs {und_sym}: LOAD ERROR — {exc}")
            continue

        report = analyzer.generate_report()
        cls = report["erosion_classification"]
        m = report["metrics"]
        print(f"\n{sym}  vs  {und_sym}")
        print(f"  classification: {cls}")
        print(f"  observations: {m.get('observations')}  corr: {m.get('return_correlation')}")
        print(f"  shortfall_vs_synthetic: {m.get('cumulative_nav_shortfall_vs_synthetic_underlying_path')}")
        print(f"  dist/avg_nav (ann.): {m.get('annualized_distribution_to_avg_nav')}")
        print(f"  summary: {report['summary'][:220]}...")
        for w in report.get("warnings") or []:
            print(f"  warning: {w}")

    print("\n" + "=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
