"""On-disk JSON cache for SecuritiesDB ETF holdings responses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class CachedEtfHoldings:
    """Payload read from a cache file."""

    etf_ticker: str
    cached_at: str
    data: dict[str, Any]
    # When set, `data` is the holdings payload for this underlying ETF (proxy for etf_ticker).
    underlying_ticker: str | None = None


def default_cache_dir(repo_root: Path) -> Path:
    return (repo_root / "application_files" / "data" / "etf_holdings_cache").resolve()


class EtfHoldingsFileCache:
    """One JSON file per symbol: ``{SYMBOL}.json``."""

    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir.resolve()
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, symbol: str) -> Path:
        sym = str(symbol or "").strip().upper()
        if not sym:
            raise ValueError("symbol must be non-empty")
        return self._dir / f"{sym}.json"

    def exists(self, symbol: str) -> bool:
        return self.path_for(symbol).is_file()

    def load(self, symbol: str) -> CachedEtfHoldings | None:
        path = self.path_for(symbol)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        data = raw.get("data")
        if not isinstance(data, dict):
            return None
        etf = str(raw.get("etf_ticker") or symbol).strip().upper()
        cached_at = str(raw.get("cached_at") or "")
        und = raw.get("underlying_ticker")
        underlying = str(und).strip().upper() if isinstance(und, str) and und.strip() else None
        return CachedEtfHoldings(
            etf_ticker=etf, cached_at=cached_at, data=data, underlying_ticker=underlying
        )

    def save(
        self,
        symbol: str,
        *,
        data: dict[str, Any],
        underlying_ticker: str | None = None,
        source: str = "securitiesdb",
    ) -> Path:
        sym = str(symbol or "").strip().upper()
        if not sym:
            raise ValueError("symbol must be non-empty")
        payload: dict[str, Any] = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source or "securitiesdb").strip() or "securitiesdb",
            "etf_ticker": sym,
            "data": data,
        }
        if underlying_ticker:
            payload["underlying_ticker"] = str(underlying_ticker).strip().upper()
        path = self.path_for(sym)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path
