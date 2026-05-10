"""Client for the SecuritiesDB ETF holdings API.

See: https://securitiesdb.com/developers/etf-holdings-api
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


class SecuritiesDbEtfHoldingsError(RuntimeError):
    """Request failed or response could not be parsed."""


@dataclass(frozen=True)
class EtfHoldingRow:
    """One position in an ETF as returned by the API."""

    ticker: str
    weight_pct: float | None
    piotroski_f: float | int | None = None
    altman_z: float | None = None


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_piotroski(v: Any) -> float | int | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else f


def _holding_from_row(row: dict[str, Any]) -> EtfHoldingRow:
    t = str(row.get("ticker") or "").strip().upper()
    wpf = _coerce_float(row.get("weight_pct"))
    return EtfHoldingRow(
        ticker=t,
        weight_pct=wpf,
        piotroski_f=_coerce_piotroski(row.get("piotroski_f")),
        altman_z=_coerce_float(row.get("altman_z")),
    )


@dataclass
class EtfHoldingsResult:
    """Parsed ETF holdings payload (``data`` object from the API)."""

    etf_ticker: str
    sector_breakdown: dict[str, float] = field(default_factory=dict)
    holdings: list[EtfHoldingRow] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)


def _parse_sector_breakdown(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


class SecuritiesDbEtfHoldingsClient:
    """
    Fetches full constituent lists for US-listed ETFs from SecuritiesDB.

    GET ``/api/v1/etfs/{ticker}/holdings``
    """

    DEFAULT_BASE_URL = "https://securitiesdb.com/api/v1"

    def __init__(self, *, base_url: str | None = None, timeout_sec: float = 60.0) -> None:
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._timeout_sec = float(timeout_sec)

    def get_holdings(self, ticker: str) -> EtfHoldingsResult:
        sym = str(ticker or "").strip().upper()
        if not sym:
            raise ValueError("ticker must be non-empty")
        url = f"{self._base_url}/etfs/{sym}/holdings"
        with httpx.Client(timeout=self._timeout_sec) as client:
            response = client.get(url)
        if response.status_code >= 400:
            detail = (response.text or "").strip()
            if len(detail) > 800:
                detail = detail[:800] + "… [truncated]"
            raise SecuritiesDbEtfHoldingsError(
                f"GET {url} failed ({response.status_code}): {detail or 'no body'}"
            )
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise SecuritiesDbEtfHoldingsError(f"Invalid JSON from {url}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SecuritiesDbEtfHoldingsError("Expected JSON object at top level")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SecuritiesDbEtfHoldingsError("Missing or invalid 'data' object in response")
        raw_holdings = data.get("holdings")
        rows: list[EtfHoldingRow] = []
        if isinstance(raw_holdings, list):
            for item in raw_holdings:
                if isinstance(item, dict):
                    rows.append(_holding_from_row(item))
        sectors = _parse_sector_breakdown(data.get("sector_breakdown"))
        return EtfHoldingsResult(etf_ticker=sym, sector_breakdown=sectors, holdings=rows, raw_data=dict(data))


__all__ = [
    "EtfHoldingRow",
    "EtfHoldingsResult",
    "SecuritiesDbEtfHoldingsClient",
    "SecuritiesDbEtfHoldingsError",
]
