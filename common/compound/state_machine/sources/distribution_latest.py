from __future__ import annotations

from typing import Any

from common.compound.finance_pipeline.distribution_history_comparison import DistributionHistoryComparison
def pull_distribution_latest(
    entity: dict[str, str],
    params: dict[str, Any],
    *,
    timeout_sec: float,
) -> dict[str, Any]:
    symbol = str(entity.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("entity.symbol required")
    cmp = DistributionHistoryComparison(symbol)
    series = cmp.fetch_stockanalysis_series()
    latest_amount: float | None = None
    latest_date: str | None = None
    if series.points:
        pt = series.points[0]
        latest_amount = pt.amount_per_share
        latest_date = pt.ex_dividend_date
    else:
        yf = cmp.fetch_yfinance_series()
        if yf.points:
            pt = yf.points[0]
            latest_amount = pt.amount_per_share
            latest_date = pt.ex_dividend_date
            series = yf

    snap = cmp.fetch_stockanalysis_snapshot()
    return {
        "amount_per_share": latest_amount,
        "ex_dividend_date": latest_date,
        "history_source": series.source,
        "events_in_series": len(series.points),
        "payout_frequency": snap.payout_frequency,
        "display": (
            f"${latest_amount:.4f} ex {latest_date}"
            if latest_amount is not None and latest_date
            else None
        ),
        "source": series.source,
    }
