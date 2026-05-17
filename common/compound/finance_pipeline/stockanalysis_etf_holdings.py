"""Fetch ETF top holdings from StockAnalysis (HTML table on /etf/{ticker}/holdings/)."""

from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from common.compound.securitiesdb_etf_holdings import EtfHoldingsResult

DEFAULT_BASE = "https://stockanalysis.com"
USER_AGENT = (
    "Mozilla/5.0 (compatible; connections-etf-holdings/1.0; +https://github.com/)"
)


class StockAnalysisEtfHoldingsError(RuntimeError):
    """Scrape failed or table could not be parsed."""


def _parse_weight_pct(s: str) -> float | None:
    t = (s or "").strip().replace(",", "")
    if t.endswith("%"):
        t = t[:-1].strip()
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


def _parse_int_commas(s: str) -> int | None:
    t = re.sub(r"[^\d]", "", s or "")
    if not t:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def _extract_total_holdings(html: str) -> int | None:
    m = re.search(
        r"Total\s+Holdings\s*<div[^>]*>(\d+)</div>", html, flags=re.I
    )
    if m:
        return int(m.group(1))
    return None


def parse_etf_holdings_page(html: str, etf_ticker: str) -> dict[str, Any]:
    """
    Build a ``data`` object compatible with SecuritiesDB shape: ``holdings`` list
    of ``{ticker, name, weight_pct, shares?}``, plus optional ``total_holdings``.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        raise StockAnalysisEtfHoldingsError("no holdings <table> in page")
    rows = table.find_all("tr")
    if len(rows) < 2:
        raise StockAnalysisEtfHoldingsError("holdings table has no data rows")
    header_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
    # Normalize: "% Weight" and "%Weight" -> "%weight"
    col = {h.lower().replace(" ", ""): i for i, h in enumerate(header_cells)}
    i_sym = col.get("symbol", 1)
    i_name = col.get("name", 2)
    i_w = col.get("%weight")
    if i_w is None:
        i_w = col.get("weight", 3)
    if i_w is None:
        for i, h in enumerate(header_cells):
            if "%" in h or "weight" in h.lower():
                i_w = i
                break
    if i_w is None:
        raise StockAnalysisEtfHoldingsError(f"no weight column in {header_cells!r}")

    holdings: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        if len(cells) <= max(i_sym, i_name, i_w):
            continue
        tick = (cells[i_sym] or "").strip().upper()
        if not tick or not re.match(r"^[A-Z][A-Z0-9.\-]*$", tick):
            continue
        name = cells[i_name] if len(cells) > i_name else ""
        w = _parse_weight_pct(cells[i_w])
        item: dict[str, Any] = {
            "ticker": tick,
            "name": name,
            "weight_pct": w,
        }
        if "shares" in col and len(cells) > col["shares"]:
            sh = _parse_int_commas(cells[col["shares"]])
            if sh is not None:
                item["shares"] = sh
        holdings.append(item)

    if not holdings:
        raise StockAnalysisEtfHoldingsError("no holdings rows parsed")

    total = _extract_total_holdings(html)
    out: dict[str, Any] = {
        "holdings": holdings,
        "source": "stockanalysis.com",
    }
    if total is not None:
        out["total_holdings"] = total
    return out


def fetch_etf_holdings(
    *,
    ticker: str,
    base_url: str = DEFAULT_BASE,
    timeout_sec: float = 60.0,
) -> EtfHoldingsResult:
    """
    GET ``/etf/{ticker}/holdings/`` and map the first holdings table into ``EtfHoldingsResult``.
    """
    sym = str(ticker or "").strip().upper()
    if not sym:
        raise ValueError("ticker must be non-empty")
    path = f"/etf/{sym.lower()}/holdings/"
    url = f"{base_url.rstrip('/')}{path}"
    with httpx.Client(
        timeout=timeout_sec,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        follow_redirects=True,
    ) as client:
        response = client.get(url)
    if response.status_code >= 400:
        body = (response.text or "")[:800]
        raise StockAnalysisEtfHoldingsError(
            f"GET {url} failed ({response.status_code}): {body!s}"
        )
    data = parse_etf_holdings_page(response.text, sym)
    return EtfHoldingsResult(etf_ticker=sym, raw_data=data)


__all__ = [
    "DEFAULT_BASE",
    "StockAnalysisEtfHoldingsError",
    "fetch_etf_holdings",
    "parse_etf_holdings_page",
]
