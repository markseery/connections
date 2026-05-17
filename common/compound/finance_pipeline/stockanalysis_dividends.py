"""StockAnalysis dividend page extractor utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup


@dataclass
class StockAnalysisDividendRow:
    ex_dividend_date: str
    cash_amount: float | None
    record_date: str | None
    pay_date: str | None


@dataclass
class StockAnalysisDividendSnapshot:
    symbol: str
    url: str
    dividend_yield_pct: float | None
    annual_dividend: float | None
    ex_dividend_date: str | None
    payout_frequency: str | None
    history: list[StockAnalysisDividendRow]
    overview_url: str | None = None
    aum_display: str | None = None  # StockAnalysis label: "Assets" (e.g. "$11.76B")
    aum_usd: float | None = None  # parsed USD


class StockAnalysisDividendExtractor:
    """Extract dividend summary + history rows from StockAnalysis ETF pages."""

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._client = httpx.Client(
            timeout=self.timeout_seconds,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
            follow_redirects=True,
        )

    @staticmethod
    def _build_url(symbol_or_url: str) -> tuple[str, str]:
        raw = str(symbol_or_url or "").strip()
        if not raw:
            raise ValueError("symbol_or_url must not be empty")
        if raw.startswith("http://") or raw.startswith("https://"):
            symbol_guess = raw.rstrip("/").split("/")[-2] if raw.rstrip("/").endswith("dividend") else raw.rstrip("/").split("/")[-1]
            return symbol_guess.upper(), raw
        symbol = raw.upper()
        return symbol, f"https://stockanalysis.com/etf/{symbol.lower()}/dividend/"

    @staticmethod
    def _build_overview_url(symbol_or_url: str) -> tuple[str, str]:
        raw = str(symbol_or_url or "").strip()
        if not raw:
            raise ValueError("symbol_or_url must not be empty")
        if raw.startswith("http://") or raw.startswith("https://"):
            parts = [p for p in raw.rstrip("/").split("/") if p]
            symbol_guess = parts[-1]
            if symbol_guess.lower() == "dividend" and len(parts) >= 2:
                symbol_guess = parts[-2]
            return symbol_guess.upper(), f"https://stockanalysis.com/etf/{symbol_guess.lower()}/"
        symbol = raw.upper()
        return symbol, f"https://stockanalysis.com/etf/{symbol.lower()}/"

    @staticmethod
    def _parse_money(text: str | None) -> float | None:
        if not text:
            return None
        cleaned = (
            text.replace("$", "")
            .replace(",", "")
            .replace("%", "")
            .strip()
        )
        if not cleaned or cleaned.lower() == "n/a":
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _parse_scaled_money(text: str | None) -> float | None:
        """Parse values like ``$11.76B``, ``91.35M``, or plain dollars."""
        if not text:
            return None
        cleaned = text.replace("$", "").replace(",", "").strip()
        if not cleaned or cleaned.lower() == "n/a":
            return None
        multiplier = 1.0
        suffix = cleaned[-1].upper()
        if suffix in {"B", "M", "K"}:
            cleaned = cleaned[:-1].strip()
            multiplier = {"B": 1e9, "M": 1e6, "K": 1e3}[suffix]
        try:
            return round(float(cleaned) * multiplier, 2)
        except ValueError:
            return None

    @staticmethod
    def _parse_percent(text: str | None) -> float | None:
        if not text:
            return None
        cleaned = text.replace("%", "").strip()
        if not cleaned or cleaned.lower() == "n/a":
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _first_value_for_label(soup: BeautifulSoup, label: str) -> str | None:
        needle = label.strip().lower()
        for container in soup.find_all("div"):
            strings = list(container.stripped_strings)
            if len(strings) < 2:
                continue
            if strings[0].strip().lower() == needle:
                return strings[1].strip()
        return None

    @staticmethod
    def _table_value_for_label(soup: BeautifulSoup, label: str) -> str | None:
        needle = label.strip().lower()
        for tr in soup.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            key = cells[0].get_text(" ", strip=True).strip().lower()
            if key != needle:
                continue
            return cells[1].get_text(" ", strip=True).strip()
        return None

    @staticmethod
    def _extract_dividend_history_table(
        soup: BeautifulSoup,
    ) -> list[StockAnalysisDividendRow]:
        for table in soup.find_all("table"):
            headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
            if not headers:
                continue
            if "cash amount" not in headers:
                continue
            rows: list[StockAnalysisDividendRow] = []
            for tr in table.find_all("tr"):
                cells = tr.find_all("td")
                if len(cells) < 2:
                    continue
                texts = [c.get_text(" ", strip=True) for c in cells]
                ex_div = texts[0] if len(texts) >= 1 else ""
                cash_amount = StockAnalysisDividendExtractor._parse_money(
                    texts[1] if len(texts) >= 2 else None
                )
                record_date = texts[2] if len(texts) >= 3 else None
                pay_date = texts[3] if len(texts) >= 4 else None
                rows.append(
                    StockAnalysisDividendRow(
                        ex_dividend_date=ex_div,
                        cash_amount=cash_amount,
                        record_date=record_date,
                        pay_date=pay_date,
                    )
                )
            if rows:
                return rows
        return []

    @staticmethod
    def _normalize_date(text: str | None) -> str | None:
        if not text:
            return None
        raw = text.strip()
        if not raw:
            return None
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                continue
        return raw

    def _fetch_overview_soup(self, symbol_or_url: str) -> tuple[str, str, BeautifulSoup]:
        symbol, overview_url = self._build_overview_url(symbol_or_url)
        response = self._client.get(overview_url)
        response.raise_for_status()
        return symbol, overview_url, BeautifulSoup(response.text, "lxml")

    def extract_aum_from_overview(self, symbol_or_url: str) -> tuple[str | None, float | None, str]:
        """
        AUM from the ETF overview page (StockAnalysis label ``Assets``).

        Returns ``(display_text, usd, overview_url)``.
        """
        _symbol, overview_url, soup = self._fetch_overview_soup(symbol_or_url)
        assets_text = self._table_value_for_label(soup, "Assets")
        if assets_text is None:
            assets_text = self._first_value_for_label(soup, "Assets")
        return assets_text, self._parse_scaled_money(assets_text), overview_url

    def extract(self, symbol_or_url: str) -> StockAnalysisDividendSnapshot:
        symbol, url = self._build_url(symbol_or_url)
        response = self._client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        dividend_yield_text = self._first_value_for_label(soup, "Dividend Yield")
        annual_dividend_text = self._first_value_for_label(soup, "Annual Dividend")
        ex_div_date_text = self._first_value_for_label(soup, "Ex-Dividend Date")
        payout_frequency = self._first_value_for_label(soup, "Payout Frequency")
        history_rows = self._extract_dividend_history_table(soup)

        aum_display, aum_usd, overview_url = self.extract_aum_from_overview(symbol)

        return StockAnalysisDividendSnapshot(
            symbol=symbol,
            url=url,
            dividend_yield_pct=self._parse_percent(dividend_yield_text),
            annual_dividend=self._parse_money(annual_dividend_text),
            ex_dividend_date=self._normalize_date(ex_div_date_text),
            payout_frequency=payout_frequency,
            history=history_rows,
            overview_url=overview_url,
            aum_display=aum_display,
            aum_usd=aum_usd,
        )

    def extract_overview_annual_yield(self, symbol_or_url: str) -> float | None:
        """Extract annualized dividend yield decimal from ETF overview page."""
        _symbol, _url, soup = self._fetch_overview_soup(symbol_or_url)

        yield_text = self._table_value_for_label(soup, "Dividend Yield")
        if yield_text is None:
            yield_text = self._first_value_for_label(soup, "Dividend Yield")
        pct = self._parse_percent(yield_text)
        if pct is None or pct <= 0:
            return None
        return pct / 100.0


__all__ = [
    "StockAnalysisDividendExtractor",
    "StockAnalysisDividendSnapshot",
    "StockAnalysisDividendRow",
]

