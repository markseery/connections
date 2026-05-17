"""SEC EDGAR company fundamentals via ``companyfacts`` (XBRL facts)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

LOGGER = logging.getLogger(__name__)

SEC_DATA_BASE = "https://data.sec.gov"
SEC_WWW_BASE = "https://www.sec.gov"
_TICKER_CACHE: Dict[str, str] = {}
_TICKER_MAP_LOADED_AT: float = 0.0
_TICKER_MAP_TTL_SEC = 86400


def _sec_user_agent() -> str:
    ua = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if ua:
        return ua
    return "connections-market-tools (contact: set SEC_EDGAR_USER_AGENT in environment)"


def _cik_10(cik: str | int) -> str:
    s = str(int(str(cik).lstrip("0") or "0"))
    return s.zfill(10)


def _load_ticker_to_cik_map(client: httpx.Client) -> Dict[str, str]:
    global _TICKER_CACHE, _TICKER_MAP_LOADED_AT
    now = time.time()
    if _TICKER_CACHE and (now - _TICKER_MAP_LOADED_AT) < _TICKER_MAP_TTL_SEC:
        return _TICKER_CACHE
    url = f"{SEC_WWW_BASE}/files/company_tickers.json"
    r = client.get(url, timeout=60.0)
    r.raise_for_status()
    data = r.json()
    rows: List[Dict[str, Any]]
    if isinstance(data, dict):
        rows = [v for v in data.values() if isinstance(v, dict)]
    elif isinstance(data, list):
        rows = [x for x in data if isinstance(x, dict)]
    else:
        rows = []
    m: Dict[str, str] = {}
    for row in rows:
        t = str(row.get("ticker", "")).strip().upper()
        cik = row.get("cik_str")
        if not t or cik is None:
            continue
        m[t] = str(int(str(cik)))
    _TICKER_CACHE = m
    _TICKER_MAP_LOADED_AT = now
    return m


@dataclass
class SecCompanyFundamentals:
    """
    Fetch and hold SEC ``companyfacts`` JSON for a US ticker.

    Requires a descriptive ``User-Agent``; set ``SEC_EDGAR_USER_AGENT`` to a string
    that identifies your app and includes contact info (SEC fair-access policy).
    """

    ticker: str
    timeout: float = 60.0
    _facts: Optional[Dict[str, Any]] = field(default=None, repr=False)
    _cik: Optional[str] = field(default=None, repr=False)
    _http_error: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.ticker = self.ticker.strip().upper()

    @property
    def cik(self) -> Optional[str]:
        return self._cik

    @property
    def http_error(self) -> Optional[str]:
        return self._http_error

    @property
    def companyfacts(self) -> Optional[Dict[str, Any]]:
        return self._facts

    def entity_name(self) -> Optional[str]:
        if not self._facts:
            return None
        name = self._facts.get("entityName")
        return str(name) if name else None

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={
                "User-Agent": _sec_user_agent(),
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=True,
        )

    def resolve_cik(self) -> Optional[str]:
        if self._cik:
            return self._cik
        with self._client() as client:
            m = _load_ticker_to_cik_map(client)
            self._cik = m.get(self.ticker)
            if not self._cik:
                LOGGER.info("No SEC CIK mapping for ticker %s", self.ticker)
            return self._cik

    def fetch(self) -> "SecCompanyFundamentals":
        """Load ``companyfacts`` from data.sec.gov; sets ``http_error`` on failure."""
        self._http_error = None
        self._facts = None
        cik = self.resolve_cik()
        if not cik:
            self._http_error = "no_cik_for_ticker"
            return self
        url = f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{_cik_10(cik)}.json"
        try:
            with self._client() as client:
                r = client.get(url, timeout=self.timeout)
                if r.status_code == 404:
                    self._http_error = "companyfacts_404"
                    return self
                r.raise_for_status()
                self._facts = r.json()
        except httpx.HTTPError as e:
            self._http_error = f"http:{e!s}"
            LOGGER.warning("SEC fetch failed for %s: %s", self.ticker, e)
        return self

    def get_fact_units(self, taxonomy: str, tag: str) -> List[Dict[str, Any]]:
        """Return raw unit rows (e.g. USD) for a concept, or empty list if missing."""
        if not self._facts:
            return []
        try:
            units = self._facts["facts"][taxonomy][tag]["units"]
        except (KeyError, TypeError):
            return []
        usd = units.get("USD") or units.get("usd") or units.get("shares")
        if not isinstance(usd, list):
            return []
        return usd
