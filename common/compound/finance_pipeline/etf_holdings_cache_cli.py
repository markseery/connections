"""Load symbols from a YAML config, print cached ETF holdings or fetch and cache via SecuritiesDB."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from common.simple import script_env

_REPO_ROOT = script_env.repo_root()

from application_files.portfolio_analyser.positions import PositionLoader
from common.compound.securitiesdb_etf_holdings import (
    EtfHoldingsResult,
    SecuritiesDbEtfHoldingsClient,
    SecuritiesDbEtfHoldingsError,
)
from common.compound.finance_pipeline.etf_holdings_cache import EtfHoldingsFileCache, default_cache_dir
from common.compound.finance_pipeline.stockanalysis_etf_holdings import (
    DEFAULT_BASE as DEFAULT_STOCKANALYSIS_BASE,
    StockAnalysisEtfHoldingsError,
    fetch_etf_holdings,
)
from common.compound.finance_pipeline.yahoo_quote_etf_holdings import (
    DEFAULT_BASE as DEFAULT_YAHOO_BASE,
    YahooQuoteEtfHoldingsError,
    fetch_etf_holdings_from_quote_page,
)
from common.compound.finance_pipeline.yfinance_etf_holdings import (
    YfinanceEtfHoldingsError,
    fetch_etf_holdings_via_yfinance,
)


def _is_no_holdings_data_error(exc: BaseException) -> bool:
    return "no holdings data for etf" in str(exc).lower()


def _print_holdings(
    etf_ticker: str,
    data: dict[str, Any],
    *,
    source: str,
    config_symbol: str | None = None,
    underlying_ticker: str | None = None,
) -> None:
    print()
    print(f"--- {etf_ticker}  ({source}) ---")
    if underlying_ticker and config_symbol:
        print(
            f"Note: config symbol {config_symbol} — holdings shown are for underlying {underlying_ticker}."
        )
    src = data.get("source")
    if src in ("stockanalysis.com", "finance.yahoo.com", "yfinance") and config_symbol:
        print(
            f"Note: top holdings from {src} (subset of full portfolio; full list may be on the provider site)."
        )
    hht = data.get("holdings_heading_ticker")
    if src == "finance.yahoo.com" and hht and str(hht).upper() != str(etf_ticker or "").upper():
        print(
            f"Note: Yahoo 'Holdings:' label is {hht!r} (section title) vs {etf_ticker!r} (quote in URL)."
        )
    th = data.get("total_holdings")
    aum = data.get("aum")
    if th is not None or aum is not None:
        print(f"total_holdings={th!r}  aum_usd={aum!r}")
    sb = data.get("sector_breakdown")
    if isinstance(sb, dict) and sb:
        print("sector_breakdown:", sb)
    holdings = data.get("holdings")
    if not isinstance(holdings, list):
        print("(no holdings list)")
        return
    print(f"{'ticker':<14} {'weight_pct':>10}  name")
    print("-" * 60)
    for row in holdings:
        if not isinstance(row, dict):
            continue
        t = str(row.get("ticker") or "")
        w = row.get("weight_pct")
        w_s = f"{float(w):.4f}" if isinstance(w, (int, float)) else str(w)
        n = str(row.get("name") or "")
        print(f"{t:<14} {w_s:>10}  {n}")


def _fetched_label(csource: str, *, refetch: bool) -> str:
    if csource == "securitiesdb":
        return "refetched (bad cache)" if refetch else "fetched + saved"
    if csource == "stockanalysis.com":
        return "refetched (stockanalysis.com, bad cache)" if refetch else "fetched (stockanalysis.com) + saved"
    if csource == "finance.yahoo.com":
        return "refetched (yahoo quote, bad cache)" if refetch else "fetched (finance.yahoo.com quote) + saved"
    if csource == "yfinance":
        return "refetched (yfinance, bad cache)" if refetch else "fetched (yfinance) + saved"
    return "fetched + saved"


def _fetch_holdings(
    *,
    symbol: str,
    client: SecuritiesDbEtfHoldingsClient,
    stockanalysis_base: str,
    yahoo_base: str,
    allow_web_fallback: bool,
    http_timeout: float,
) -> tuple[EtfHoldingsResult | None, str | None, str | None, str | None]:
    """
    Returns (result, err_message, cache_source, None).

    Order: SecuritiesDB, StockAnalysis, yfinance (Yahoo API), then Yahoo HTML (may 404 in headless).
    """
    try:
        r = client.get_holdings(symbol)
        return (r, None, "securitiesdb", None)
    except SecuritiesDbEtfHoldingsError as exc:
        if not allow_web_fallback or not _is_no_holdings_data_error(exc):
            return (None, str(exc), None, None)
        sym = str(symbol).strip()
        print(
            f"[{symbol}] SecuritiesDB: {exc!s}\n"
            f"[{symbol}] Fallback: https://stockanalysis.com/etf/{sym.lower()}/holdings/",
            file=sys.stderr,
            flush=True,
        )
        try:
            r = fetch_etf_holdings(
                ticker=symbol, base_url=stockanalysis_base, timeout_sec=http_timeout
            )
            return (r, None, "stockanalysis.com", None)
        except StockAnalysisEtfHoldingsError as exc2:
            print(
                f"[{symbol}] StockAnalysis failed: {exc2!s}\n"
                f"[{symbol}] Fallback: yfinance (Yahoo topHoldings API)",
                file=sys.stderr,
                flush=True,
            )
            try:
                r = fetch_etf_holdings_via_yfinance(symbol)
                return (r, None, "yfinance", None)
            except YfinanceEtfHoldingsError as exc3:
                print(
                    f"[{symbol}] yfinance failed: {exc3!s}\n"
                    f"[{symbol}] Fallback: https://finance.yahoo.com/quote/{sym.upper()}/ (Holdings section)",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    r = fetch_etf_holdings_from_quote_page(
                        ticker=symbol, base_url=yahoo_base, timeout_sec=http_timeout
                    )
                    return (r, None, "finance.yahoo.com", None)
                except YahooQuoteEtfHoldingsError as exc4:
                    return (
                        None,
                        f"{exc!s} (stockanalysis: {exc2!s}; yfinance: {exc3!s}; yahoo: {exc4!s})",
                        None,
                        None,
                    )
    except ValueError as exc:
        return (None, str(exc), None, None)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="For each symbol in a positions YAML, show cached ETF holdings or fetch and cache."
    )
    parser.add_argument(
        "--config",
        default="application_files/config/rh.symbols.yaml",
        help="Path to positions YAML (default: application_files/config/rh.symbols.yaml).",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Cache directory (default: application_files/data/etf_holdings_cache under repo root).",
    )
    mx = parser.add_mutually_exclusive_group()
    mx.add_argument(
        "--force",
        action="store_true",
        help="Refetch from API even when a cache file exists.",
    )
    mx.add_argument(
        "--no-refresh",
        action="store_true",
        help="Never fetch or overwrite the cache: only read existing cache files. "
        "Error if a symbol has no file or a file is unreadable.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout for SecuritiesDB / StockAnalysis / Yahoo HTML (yfinance uses its own client; default: 60).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="SEC",
        help="Seconds to sleep between each symbol (default: 0). Reduces API request rate.",
    )
    parser.add_argument(
        "--no-stockanalysis-fallback",
        action="store_true",
        help="When SecuritiesDB has no ETF holdings, do not fall back to StockAnalysis or Yahoo Finance.",
    )
    parser.add_argument(
        "--stockanalysis-base",
        default=DEFAULT_STOCKANALYSIS_BASE,
        help=f"Base URL for StockAnalysis (default: {DEFAULT_STOCKANALYSIS_BASE!r}).",
    )
    parser.add_argument(
        "--yahoo-finance-base",
        default=DEFAULT_YAHOO_BASE,
        help=f"Base URL for Yahoo Finance (default: {DEFAULT_YAHOO_BASE!r}).",
    )
    args = parser.parse_args()
    delay_sec = max(0.0, float(args.delay))
    http_timeout = float(args.timeout)
    sa_base = str(args.stockanalysis_base).strip() or DEFAULT_STOCKANALYSIS_BASE
    yh_base = str(args.yahoo_finance_base).strip() or DEFAULT_YAHOO_BASE

    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = (_REPO_ROOT / config_path).resolve()
    if not config_path.is_file():
        print(f"error: config not found: {config_path}", file=sys.stderr)
        return 2

    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else default_cache_dir(_REPO_ROOT)
    if not cache_dir.is_absolute():
        cache_dir = (_REPO_ROOT / cache_dir).resolve()
    cache = EtfHoldingsFileCache(cache_dir)
    client = SecuritiesDbEtfHoldingsClient(timeout_sec=http_timeout)

    try:
        positions = PositionLoader.load_from_yaml(config_path)
    except (OSError, ValueError) as exc:
        print(f"error loading config: {exc}", file=sys.stderr)
        return 2

    if not positions:
        print("No positions in config.", file=sys.stderr)
        return 1

    errors = 0
    allow_scrape = not args.no_stockanalysis_fallback
    for i, (symbol, _shares) in enumerate(positions):
        if i > 0 and delay_sec > 0:
            time.sleep(delay_sec)
        if args.no_refresh:
            if not cache.exists(symbol):
                print(
                    f"[{symbol}] no cache file (use without --no-refresh to fetch)",
                    file=sys.stderr,
                )
                errors += 1
                continue
            cached = cache.load(symbol)
            if cached is None:
                print(
                    f"[{symbol}] cache file unreadable (not refetching; use without --no-refresh to rebuild)",
                    file=sys.stderr,
                )
                errors += 1
                continue
            _print_holdings(
                cached.etf_ticker,
                cached.data,
                source=f"cache {cached.cached_at}",
                config_symbol=symbol,
                underlying_ticker=cached.underlying_ticker,
            )
            continue
        if args.force or not cache.exists(symbol):
            result, err, csource, _und = _fetch_holdings(
                symbol=symbol,
                client=client,
                stockanalysis_base=sa_base,
                yahoo_base=yh_base,
                allow_web_fallback=allow_scrape,
                http_timeout=http_timeout,
            )
            if err or result is None or csource is None:
                print(f"[{symbol}] fetch failed: {err}", file=sys.stderr)
                errors += 1
                continue
            cache.save(
                symbol,
                data=result.raw_data,
                source=csource,
            )
            _print_holdings(
                result.etf_ticker,
                result.raw_data,
                source=_fetched_label(csource, refetch=False),
                config_symbol=symbol,
                underlying_ticker=None,
            )
        else:
            cached = cache.load(symbol)
            if cached is None:
                result, err, csource, _und = _fetch_holdings(
                    symbol=symbol,
                    client=client,
                    stockanalysis_base=sa_base,
                    yahoo_base=yh_base,
                    allow_web_fallback=allow_scrape,
                    http_timeout=http_timeout,
                )
                if err or result is None or csource is None:
                    print(f"[{symbol}] cache corrupt, refetch failed: {err}", file=sys.stderr)
                    errors += 1
                    continue
                cache.save(symbol, data=result.raw_data, source=csource)
                _print_holdings(
                    result.etf_ticker,
                    result.raw_data,
                    source=_fetched_label(csource, refetch=True),
                    config_symbol=symbol,
                    underlying_ticker=None,
                )
            else:
                src = f"cache {cached.cached_at}"
                _print_holdings(
                    cached.etf_ticker,
                    cached.data,
                    source=src,
                    config_symbol=symbol,
                    underlying_ticker=cached.underlying_ticker,
                )

    print(f"\nCache directory: {cache_dir}", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
