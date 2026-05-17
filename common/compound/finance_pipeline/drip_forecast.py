"""DRIP-style forward projection using existing distribution pattern tools.

This module is intentionally simple: it treats a "period" as one projected
distribution event and applies a constant per-period price growth rate plus an
optional dividend reinvestment (DRIP) fraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import yfinance as yf

from common.compound.finance_pipeline.distribution_pattern_engine import analyze_symbol_distribution
from common.compound.finance_pipeline.stockanalysis_dividends import StockAnalysisDividendExtractor


def get_current_price(symbol: str) -> float | None:
    """Return current market price using the same yfinance approach used elsewhere."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    try:
        ticker = yf.Ticker(sym)
        info = ticker.info or {}
        current_price = info.get("regularMarketPrice") or info.get("currentPrice")
        if current_price is None:
            hist = ticker.history(period="2d")
            if hist is not None and not hist.empty:
                current_price = float(hist.iloc[-1]["Close"])
        value = float(current_price) if current_price is not None else None
        if value is None or value <= 0:
            return None
        return value
    except Exception:
        return None


def infer_monthly_price_growth_rate(
    *,
    symbol: str,
    lookback_months: int = 36,
    price_series: str = "adjclose",
) -> dict[str, Any]:
    """
    Infer a likely monthly growth rate from history using monthly bars.

    Returns:
      - monthly_cagr: geometric average monthly rate over the lookback window
      - median_monthly_return: median month-to-month return (robust to outliers)
      - months_observed: number of month-to-month intervals used
      - start_price/end_price: endpoints used
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol is required")
    lb = max(2, int(lookback_months))
    series = str(price_series or "adjclose").strip().lower()
    if series not in ("close", "adjclose"):
        raise ValueError("price_series must be 'close' or 'adjclose'")

    try:
        ticker = yf.Ticker(sym)
        hist = ticker.history(period=f"{lb + 2}mo", interval="1mo", auto_adjust=False)
    except Exception as exc:
        raise RuntimeError(f"unable to load monthly history for {sym}: {exc}") from exc

    if hist is None or getattr(hist, "empty", True):
        raise RuntimeError(f"no monthly history returned for {sym}")

    col = "Adj Close" if series == "adjclose" else "Close"
    if col not in hist.columns:
        raise RuntimeError(f"monthly history missing column {col!r} for {sym}")
    prices = [float(v) for v in list(hist[col].dropna().values) if float(v) > 0]
    if len(prices) < 3:
        raise RuntimeError(f"insufficient monthly price points for {sym} (n={len(prices)})")

    # Keep only the last N+1 points to form N monthly intervals.
    prices = prices[-(lb + 1) :]
    months_observed = max(1, len(prices) - 1)
    start_price = float(prices[0])
    end_price = float(prices[-1])

    monthly_cagr = (end_price / start_price) ** (1.0 / months_observed) - 1.0
    rets: list[float] = []
    for a, b in zip(prices, prices[1:]):
        if a > 0:
            rets.append((b / a) - 1.0)
    rets_sorted = sorted(rets)
    if not rets_sorted:
        median_ret = 0.0
    else:
        mid = len(rets_sorted) // 2
        median_ret = (
            rets_sorted[mid]
            if len(rets_sorted) % 2 == 1
            else (rets_sorted[mid - 1] + rets_sorted[mid]) / 2.0
        )

    return {
        "symbol": sym,
        "lookback_months_requested": lb,
        "price_series": series,
        "months_observed": months_observed,
        "start_price": round(start_price, 6),
        "end_price": round(end_price, 6),
        "monthly_cagr": float(monthly_cagr),
        "median_monthly_return": float(median_ret),
    }


@dataclass(frozen=True)
class DripForecastInputs:
    symbol: str
    initial_shares: float
    price_growth_rate: float
    drip_rate: float
    periods: int
    as_of_date: date
    horizon_days: int = 3650
    period_unit: str = "month"  # "month" (default) or "distribution"
    monthly_event_mode: str = "count"  # "count" (default) or "expected"


@dataclass(frozen=True)
class DripForecastRow:
    period: int
    date: str | None
    price: float
    shares: float
    total_value: float
    distribution_amount: float
    drip_amount: float
    event_count: float | None = None


def _parse_iso_date(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _add_months(d: date, months: int) -> date:
    m = (d.month - 1) + int(months)
    y = d.year + (m // 12)
    mm = (m % 12) + 1
    # clamp day to end-of-month
    # get first day of next month then back up
    next_month = date(y + (1 if mm == 12 else 0), 1 if mm == 12 else (mm + 1), 1)
    last_day = next_month - timedelta(days=1)
    day = min(d.day, last_day.day)
    return date(y, mm, day)


def _first_day_next_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def build_drip_forecast(inputs: DripForecastInputs) -> dict[str, Any]:
    sym = str(inputs.symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol is required")
    periods = max(0, int(inputs.periods))
    if periods <= 0:
        return {"symbol": sym, "rows": [], "note": "periods=0"}

    initial_shares = float(inputs.initial_shares)
    if initial_shares < 0:
        raise ValueError("initial_shares must be >= 0")

    growth = float(inputs.price_growth_rate)
    drip_rate = float(inputs.drip_rate)
    if drip_rate < 0 or drip_rate > 1:
        raise ValueError("drip_rate must be in [0, 1]")
    unit = str(inputs.period_unit or "month").strip().lower()
    if unit not in ("month", "distribution"):
        raise ValueError("period_unit must be 'month' or 'distribution'")
    monthly_mode = str(inputs.monthly_event_mode or "count").strip().lower()
    if monthly_mode not in ("count", "expected"):
        raise ValueError("monthly_event_mode must be 'count' or 'expected'")

    spot_price = get_current_price(sym)
    if spot_price is None:
        raise RuntimeError(f"unable to resolve current price for {sym}")

    extractor = StockAnalysisDividendExtractor()
    dist = analyze_symbol_distribution(
        symbol=sym,
        shares=max(0, int(round(initial_shares))),
        as_of_date=inputs.as_of_date,
        horizon_days=max(30, int(inputs.horizon_days)),
        extractor=extractor,
    )

    per_share = dist.get("median_distribution_per_share")
    if per_share is None:
        next_total = float(dist.get("next_projected_distribution_amount") or 0.0)
        per_share = (next_total / initial_shares) if initial_shares > 0 else 0.0
    per_share = float(per_share or 0.0)

    projected_iso = list(dist.get("forward_projection_sequence") or [])
    projected_dates = [_parse_iso_date(v) for v in projected_iso]
    projected_dates = [d for d in projected_dates if d is not None]
    projected_dates.sort()

    rows: list[DripForecastRow] = []
    shares = initial_shares
    price = float(spot_price)

    if unit == "distribution":
        # One row == one projected distribution event (legacy behavior).
        if projected_dates and len(projected_dates) < periods:
            last = projected_dates[-1]
            projected_dates = projected_dates + [last] * (periods - len(projected_dates))
        for i in range(1, periods + 1):
            price = price * (1.0 + growth) if i > 1 else price
            distribution_amount = max(0.0, shares * per_share)
            drip_amount = distribution_amount * drip_rate
            if price > 0 and drip_amount > 0:
                shares += drip_amount / price
            total_value = shares * price
            dt = projected_dates[i - 1] if i - 1 < len(projected_dates) else None
            rows.append(
                DripForecastRow(
                    period=i,
                    date=dt.isoformat() if dt else None,
                    price=round(price, 6),
                    shares=round(shares, 6),
                    total_value=round(total_value, 2),
                    distribution_amount=round(distribution_amount, 2),
                    drip_amount=round(drip_amount, 2),
                )
            )
    else:
        # One row == one month. Count how many projected distribution events occur in that month.
        # Period 1 starts at the next calendar month to avoid partial-month artifacts.
        start = _first_day_next_month(inputs.as_of_date)
        # Ensure we have enough projected dates to cover the requested months; if not, fall back
        # to a cadence-derived approximation.
        cadence_days = dist.get("cadence_days")
        cadence_days = float(cadence_days) if isinstance(cadence_days, (int, float)) and cadence_days else None
        approx_next = projected_dates[-1] if projected_dates else start
        while cadence_days and (not projected_dates or projected_dates[-1] < _add_months(start, periods)):
            approx_next = approx_next + timedelta(days=int(round(cadence_days)))
            if approx_next > start and approx_next not in projected_dates:
                projected_dates.append(approx_next)
        projected_dates.sort()

        for i in range(1, periods + 1):
            end = _add_months(start, 1)
            # Apply price growth once per month-period.
            price = price * (1.0 + growth) if i > 1 else price
            n_events_count = float(sum(1 for d in projected_dates if start <= d < end))
            n_events: float
            if monthly_mode == "expected" and cadence_days and cadence_days > 0:
                # Smooth the calendar effects: expected payouts this month ~= days_in_month / cadence_days.
                n_events = float((end - start).days) / float(cadence_days)
            else:
                n_events = n_events_count

            # If we truly have no projection coverage and no cadence, assume 1 event/month as a conservative fallback.
            if n_events <= 0 and not projected_dates and not cadence_days:
                n_events = 1.0

            distribution_amount = max(0.0, shares * per_share * float(n_events))
            drip_amount = distribution_amount * drip_rate
            if price > 0 and drip_amount > 0:
                shares += drip_amount / price
            total_value = shares * price
            label = start.strftime("%Y-%m")
            rows.append(
                DripForecastRow(
                    period=i,
                    date=label,
                    price=round(price, 6),
                    shares=round(shares, 6),
                    total_value=round(total_value, 2),
                    distribution_amount=round(distribution_amount, 2),
                    drip_amount=round(drip_amount, 2),
                    event_count=round(float(n_events), 4),
                )
            )
            start = end

    return {
        "symbol": sym,
        "spot_price": float(spot_price),
        "median_distribution_per_share": round(per_share, 6),
        "payout_frequency": dist.get("payout_frequency"),
        "next_distribution_source": dist.get("next_distribution_source"),
        "period_unit": unit,
        "monthly_event_mode": monthly_mode if unit == "month" else None,
        "rows": [r.__dict__ for r in rows],
    }

