"""SEC EDGAR companyfacts + fundamental growth for one symbol."""

from __future__ import annotations

import time
from dataclasses import asdict, is_dataclass
from typing import Any, Dict

from common.compound.fundamental_growth import FundamentalGrowth
from common.compound.sec_company_fundamentals import SecCompanyFundamentals


def _dc(obj: Any) -> Any:
    if obj is None:
        return None
    if is_dataclass(obj):
        return asdict(obj)
    return obj


def compute_sec_growth(symbol: str, *, sleep_sec: float = 0.12) -> Dict[str, Any]:
    sym = (symbol or "").strip().upper()
    if sleep_sec > 0:
        time.sleep(sleep_sec)
    sec = SecCompanyFundamentals(sym).fetch()
    growth = FundamentalGrowth().analyze(sec)
    return {
        "symbol": sym,
        "sec": {
            "cik": sec.cik,
            "http_error": sec.http_error,
            "entity": sec.entity_name(),
        },
        "growth": {
            "entity": growth.entity_name,
            "metrics": [_dc(m) for m in growth.metrics],
        },
    }
