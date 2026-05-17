from __future__ import annotations

from typing import Any

from common.compound.finance_pipeline.stockanalysis_dividends import StockAnalysisDividendExtractor


def pull_stockanalysis_aum(
    entity: dict[str, str],
    params: dict[str, Any],
    *,
    timeout_sec: float,
) -> dict[str, Any]:
    symbol = str(entity.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("entity.symbol required")
    timeout = max(5.0, float(timeout_sec))
    extractor = StockAnalysisDividendExtractor(timeout_seconds=timeout)
    display, usd, overview_url = extractor.extract_aum_from_overview(symbol)
    return {
        "aum_display": display,
        "aum_usd": usd,
        "overview_url": overview_url,
        "source": "stockanalysis_overview_assets",
    }
