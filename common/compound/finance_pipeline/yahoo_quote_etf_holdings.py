"""Parse top ETF holdings from a Yahoo Finance /quote/{ticker}/ page (Holdings: TICKER + top-holdings)."""

from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from common.compound.securitiesdb_etf_holdings import EtfHoldingsResult

DEFAULT_BASE = "https://finance.yahoo.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class YahooQuoteEtfHoldingsError(RuntimeError):
    """Scrape failed or expected sections are missing."""


def _parse_weight_pct(s: str) -> float | None:
    t = (s or "").strip().replace(",", "")
    if t.endswith("%"):
        t = t[:-1].strip()
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


_HOLDINGS_H3 = re.compile(r"Holdings:\s*([A-Za-z0-9.\-]+)", re.I)


def _first_holdings_heading_ticker(soup: BeautifulSoup) -> str | None:
    """
    Yahoo sometimes labels the block ``Holdings: TSPY`` while the URL is another
    symbol; we only require *some* ``Holdings:`` line, not a match to the path ticker.
    """
    for h3 in soup.find_all("h3"):
        txt = h3.get_text(" ", strip=True)
        m = _HOLDINGS_H3.search(txt)
        if m:
            return m.group(1).upper()
    return None


def parse_yahoo_quote_etf_holdings_page(html: str, etf_ticker: str) -> dict[str, Any]:
    """
    Expect an ``<h3>`` containing ``Holdings: …`` (ticker in the label may differ from
    the URL symbol, e.g. ``Holdings: TSPY``) and ``<section data-testid="top-holdings">``.
    When the heading ticker differs, ``holdings_heading_ticker`` is set on the payload.
    Optionally fills ``sector_breakdown`` from ``etf-sector-weightings-overview``.
    """
    sym = str(etf_ticker or "").strip().upper()
    if not sym:
        raise ValueError("etf_ticker must be non-empty")
    soup = BeautifulSoup(html, "lxml")
    heading_tik = _first_holdings_heading_ticker(soup)
    if not heading_tik:
        raise YahooQuoteEtfHoldingsError(
            "no 'Holdings: …' heading (h3) on page"
        )
    top = soup.find("section", attrs={"data-testid": "top-holdings"})
    if top is None:
        raise YahooQuoteEtfHoldingsError('no <section data-testid="top-holdings">')
    holdings: list[dict[str, Any]] = []
    for row in top.select("div.content"):
        sym_el = row.select_one("span.symbol")
        data_el = row.select_one("span.data")
        name_el = row.select_one("span.name")
        if not sym_el or not data_el:
            continue
        tick = sym_el.get_text(strip=True).upper()
        w = _parse_weight_pct(data_el.get_text())
        name = name_el.get_text(strip=True) if name_el else ""
        if not tick or w is None:
            continue
        if not re.match(r"^[A-Z0-9.\-]{1,16}$", tick):
            continue
        holdings.append({"ticker": tick, "name": name, "weight_pct": w})
    if not holdings:
        raise YahooQuoteEtfHoldingsError("no rows parsed in top-holdings section")
    out: dict[str, Any] = {
        "holdings": holdings,
        "source": "finance.yahoo.com",
    }
    out["holdings_heading_ticker"] = heading_tik
    sectors: dict[str, float] = {}
    sw = soup.find("section", attrs={"data-testid": "etf-sector-weightings-overview"})
    if sw is not None:
        for row in sw.select("div.content"):
            link = row.select_one("a.primary-link")
            d_el = row.select_one("span.data")
            if not link or not d_el:
                continue
            label = link.get_text(strip=True)
            pct = _parse_weight_pct(d_el.get_text())
            if label and pct is not None:
                sectors[label] = pct
    if sectors:
        out["sector_breakdown"] = sectors
    return out


def fetch_etf_holdings_from_quote_page(
    *,
    ticker: str,
    base_url: str = DEFAULT_BASE,
    timeout_sec: float = 60.0,
) -> EtfHoldingsResult:
    """
    GET ``/quote/{TICKER}/`` and parse the **Holdings:** label and ``top-holdings`` list.
    The h3 may read ``Holdings: TSPY`` while ``TICKER`` in the URL is different.
    """
    sym = str(ticker or "").strip().upper()
    if not sym:
        raise ValueError("ticker must be non-empty")
    path = f"/quote/{sym}/"
    url = f"{base_url.rstrip('/')}{path}"
    with httpx.Client(
        timeout=timeout_sec,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
        follow_redirects=True,
    ) as client:
        response = client.get(url)
    if response.status_code >= 400:
        body = (response.text or "")[:800]
        raise YahooQuoteEtfHoldingsError(
            f"GET {url} failed ({response.status_code}): {body!s}"
        )
    data = parse_yahoo_quote_etf_holdings_page(response.text, sym)
    return EtfHoldingsResult(etf_ticker=sym, raw_data=data)


__all__ = [
    "DEFAULT_BASE",
    "YahooQuoteEtfHoldingsError",
    "fetch_etf_holdings_from_quote_page",
    "parse_yahoo_quote_etf_holdings_page",
]
