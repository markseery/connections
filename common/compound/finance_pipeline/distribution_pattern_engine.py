"""Deterministic distribution schedule inference and projection (heuristic fallback)."""

from __future__ import annotations

import calendar
import statistics
from datetime import date, datetime, timedelta
from typing import Any

from common.simple.yfinance_warnings import suppress_utcnow_deprecation_warning
from common.compound.finance_pipeline.distribution_history_comparison import DistributionHistoryComparison
from common.compound.finance_pipeline.stockanalysis_dividends import StockAnalysisDividendExtractor


def parse_any_date(text: str | None) -> date | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _cadence_days_from_dates(dates: list[date]) -> int | None:
    unique = sorted(set(dates))
    if len(unique) < 2:
        return None
    gaps = [(curr - prev).days for prev, curr in zip(unique, unique[1:]) if (curr - prev).days > 0]
    if not gaps:
        return None
    return max(1, int(round(statistics.median(gaps))))


def _cadence_days_from_frequency(freq: str | None) -> int | None:
    text = str(freq or "").strip().lower()
    if not text:
        return None
    if "weekly" in text:
        return 7
    if "biweekly" in text:
        return 14
    if "month" in text:
        return 30
    if "quarter" in text:
        return 91
    return None


def _project_distribution_dates(
    last_date: date,
    cadence_days: int,
    as_of_date: date,
    horizon_days: int,
) -> list[date]:
    out: list[date] = []
    horizon_end = as_of_date + timedelta(days=horizon_days)
    next_date = last_date
    while next_date <= horizon_end:
        next_date = next_date + timedelta(days=cadence_days)
        if next_date <= as_of_date:
            continue
        if next_date > horizon_end:
            break
        out.append(next_date)
    return out


def yahoo_dividend_history(symbol: str) -> tuple[list[date], list[float]]:
    suppress_utcnow_deprecation_warning()
    try:
        import yfinance as yf
    except Exception:
        return [], []
    try:
        series = yf.Ticker(symbol).dividends
    except Exception:
        return [], []
    if series is None or getattr(series, "empty", True):
        return [], []
    dates: list[date] = []
    amounts: list[float] = []
    for idx, value in series.items():
        parsed = parse_any_date(str(getattr(idx, "date", lambda: idx)()))
        if parsed is None:
            parsed = parse_any_date(str(idx))
        try:
            amt = float(value)
        except Exception:
            continue
        if parsed is None or amt <= 0:
            continue
        dates.append(parsed)
        amounts.append(amt)
    return sorted(set(dates)), amounts


def collect_symbol_dividend_history(
    *,
    symbol: str,
    extractor: StockAnalysisDividendExtractor,
) -> dict[str, Any]:
    """Load ex-dividend date series (StockAnalysis first, Yahoo if sparse) for pattern tools and analysis."""
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    snapshot = extractor.extract(symbol)
    rows = snapshot.history or []
    sa_dates = [parse_any_date(r.ex_dividend_date) for r in rows]
    sa_dates = [d for d in sa_dates if d is not None]
    sa_amounts = [
        float(r.cash_amount) for r in rows if r.cash_amount is not None and float(r.cash_amount) > 0
    ]
    history_source = "stockanalysis_history"
    history_dates = sorted(set(sa_dates))
    history_amounts = list(sa_amounts)
    if len(history_dates) < 2 and not str(snapshot.payout_frequency or "").strip():
        y_dates, y_amounts = yahoo_dividend_history(symbol)
        if y_dates:
            history_dates = y_dates
            history_amounts = y_amounts
            history_source = "yahoo_history_fallback"
    explicit_ex = parse_any_date(snapshot.ex_dividend_date)
    if explicit_ex is not None and explicit_ex not in history_dates:
        history_dates = sorted(set(history_dates + [explicit_ex]))
    return {
        "symbol": symbol,
        "history_dates": history_dates,
        "history_amounts": history_amounts,
        "history_source": history_source,
        "payout_frequency": snapshot.payout_frequency,
        "events_observed": len(rows),
        "explicit_ex_dividend_date": explicit_ex,
    }


def _mode_int(values: list[int], default: int) -> int:
    if not values:
        return default
    counts: dict[int, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _infer_month_step(dates: list[date], payout_frequency: str | None) -> int:
    deltas: list[int] = []
    for prev, curr in zip(dates, dates[1:]):
        delta = (curr.year - prev.year) * 12 + (curr.month - prev.month)
        if delta > 0:
            deltas.append(delta)
    if deltas:
        med = float(statistics.median(deltas))
        if med >= 9:
            return 12
        if med >= 5:
            return 6
        if med >= 2:
            return 3
        return 1
    freq = str(payout_frequency or "").strip().lower()
    if "quarter" in freq:
        return 3
    if "annual" in freq or "year" in freq:
        return 12
    return 1


def _score_day_of_month_rule(dates: list[date], day_of_month: int) -> float:
    diffs = [abs(d.day - day_of_month) for d in dates]
    return float(statistics.median(diffs)) if diffs else 999.0


def _score_nth_weekday_rule(dates: list[date], weekday: int, nth: int) -> float:
    diffs: list[int] = []
    for d in dates:
        candidate = nth_weekday_date_for_month(d.year, d.month, weekday, nth)
        diffs.append(abs((d - candidate).days))
    return float(statistics.median(diffs)) if diffs else 999.0


def infer_pattern(
    dates: list[date],
    payout_frequency: str | None,
) -> dict[str, Any] | None:
    if not dates:
        return None
    sorted_dates = sorted(set(dates))
    freq_cadence = _cadence_days_from_frequency(payout_frequency)
    if len(sorted_dates) < 2 and freq_cadence is not None:
        return {
            "kind": "day_cadence",
            "cadence_days": freq_cadence,
            "label": "weekly" if freq_cadence <= 10 else ("biweekly" if freq_cadence <= 17 else "monthly"),
        }
    cadence_days = _cadence_days_from_dates(sorted_dates)
    if cadence_days is not None and cadence_days <= 17:
        return {
            "kind": "day_cadence",
            "cadence_days": cadence_days,
            "label": "weekly" if cadence_days <= 10 else "biweekly",
        }

    months_step = _infer_month_step(sorted_dates, payout_frequency)
    dom = _mode_int([d.day for d in sorted_dates], sorted_dates[-1].day)
    weekday = _mode_int([d.weekday() for d in sorted_dates], sorted_dates[-1].weekday())
    nth = _mode_int(
        [((d.day - 1) // 7) + 1 for d in sorted_dates],
        ((sorted_dates[-1].day - 1) // 7) + 1,
    )

    dom_score = _score_day_of_month_rule(sorted_dates, dom)
    nth_score = _score_nth_weekday_rule(sorted_dates, weekday, nth)

    if dom_score <= nth_score:
        return {
            "kind": "month_dom",
            "months_step": months_step,
            "day_of_month": dom,
            "label": "monthly" if months_step == 1 else "quarterly",
        }
    return {
        "kind": "month_nth_weekday",
        "months_step": months_step,
        "weekday": weekday,
        "nth": nth,
        "label": "monthly" if months_step == 1 else "quarterly",
    }


def project_from_pattern(
    *,
    pattern: dict[str, Any],
    last_date: date,
    as_of_date: date,
    horizon_days: int,
) -> list[date]:
    horizon_end = as_of_date + timedelta(days=horizon_days)
    out: list[date] = []
    kind = str(pattern.get("kind") or "")
    if kind == "day_cadence":
        cadence_days = int(pattern.get("cadence_days") or 0)
        if cadence_days > 0:
            out.extend(
                _project_distribution_dates(
                    last_date=last_date,
                    cadence_days=cadence_days,
                    as_of_date=as_of_date - timedelta(days=1),
                    horizon_days=horizon_days,
                )
            )
        return sorted(set(out))

    months_step = max(1, int(pattern.get("months_step") or 1))
    y, m = last_date.year, last_date.month
    for _ in range(60):
        y, m = _add_months(y, m, months_step)
        if kind == "month_dom":
            dom = max(1, int(pattern.get("day_of_month") or 1))
            dom = min(dom, calendar.monthrange(y, m)[1])
            candidate = date(y, m, dom)
        elif kind == "month_nth_weekday":
            weekday = int(pattern.get("weekday") or 0)
            nth = int(pattern.get("nth") or 1)
            candidate = nth_weekday_date_for_month(y, m, weekday, nth)
        else:
            break
        if candidate < as_of_date:
            continue
        if candidate > horizon_end:
            break
        out.append(candidate)
    return sorted(set(out))


def normalize_projected_dates_for_frequency(
    projected_dates: list[date],
    payout_frequency: str | None,
) -> list[date]:
    unique_sorted = sorted(set(projected_dates))
    freq = str(payout_frequency or "").strip().lower()
    if "week" in freq:
        return unique_sorted
    by_month: dict[tuple[int, int], date] = {}
    for d in unique_sorted:
        ym = (d.year, d.month)
        prev = by_month.get(ym)
        if prev is None or d < prev:
            by_month[ym] = d
    return sorted(by_month.values())


def nth_weekday_of_month(year: int, month: int, weekday: int, nth: int) -> date:
    month_days = [
        day
        for day in range(1, calendar.monthrange(year, month)[1] + 1)
        if date(year, month, day).weekday() == weekday
    ]
    if not month_days:
        raise ValueError(f"no weekday={weekday} in {year}-{month}")
    idx = max(0, min(len(month_days) - 1, nth - 1))
    return date(year, month, month_days[idx])


def nth_weekday_date_for_month(year: int, month: int, weekday: int, nth: int) -> date:
    return nth_weekday_of_month(year, month, weekday, nth)


def _add_months(year: int, month: int, step: int) -> tuple[int, int]:
    m = month + step
    y = year + (m - 1) // 12
    mm = ((m - 1) % 12) + 1
    return y, mm


def build_forward_payout_schedule_with_drip(
    *,
    shares: float,
    median_per_share: float,
    payout_dates: list[date],
    drip_rate: float,
    spot_price: float,
) -> tuple[list[dict[str, Any]], float, float]:
    """
    Walk projected ex-dates in order: each payout = shares * median_per_share;
    reinvest drip_rate of that cash at spot_price to increase shares before the next payout.

    Returns (schedule rows, sum of payout cash over horizon, first_payout_amount).
    """
    dr = max(0.0, min(1.0, float(drip_rate)))
    px = float(spot_price)
    med = float(median_per_share)
    if dr <= 0 or px <= 0 or med <= 0:
        raise ValueError("drip_rate, spot_price, and median_per_share must be positive for DRIP schedule")
    ordered = sorted(set(payout_dates))
    schedule: list[dict[str, Any]] = []
    sh = float(shares)
    total_cash = 0.0
    for d in ordered:
        amt = sh * med
        total_cash += amt
        schedule.append(
            {
                "date": d.isoformat(),
                "amount": round(amt, 2),
                "shares_before": round(sh, 6),
            }
        )
        reinvest = amt * dr
        sh += reinvest / px
    first_amt = float(schedule[0]["amount"]) if schedule else 0.0
    return schedule, round(total_cash, 2), first_amt


def analyze_symbol_distribution(
    *,
    symbol: str,
    shares: int,
    as_of_date: date,
    horizon_days: int,
    extractor: StockAnalysisDividendExtractor,
    drip_rate: float = 0.0,
    spot_price: float | None = None,
) -> dict[str, Any]:
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    shares = max(0, int(shares))

    signal = DistributionHistoryComparison(symbol).dividend_history_signal()
    side_by_side = DistributionHistoryComparison(symbol).side_by_side()

    bundle = collect_symbol_dividend_history(symbol=symbol, extractor=extractor)
    history_source = str(bundle.get("history_source") or "unknown")
    history_dates: list[date] = list(bundle.get("history_dates") or [])
    history_amounts: list[float] = list(bundle.get("history_amounts") or [])

    median_per_share = statistics.median(history_amounts) if history_amounts else None
    raw_ex = bundle.get("explicit_ex_dividend_date")
    snapshot_ex_date: date | None = raw_ex if isinstance(raw_ex, date) else None
    last_date = max(history_dates) if history_dates else snapshot_ex_date

    pattern = infer_pattern(history_dates, bundle.get("payout_frequency"))

    projected_dates: list[date] = []
    if pattern is not None and last_date is not None:
        projected_dates = project_from_pattern(
            pattern=pattern,
            last_date=last_date,
            as_of_date=as_of_date,
            horizon_days=horizon_days,
        )
    pay_freq = bundle.get("payout_frequency")
    forward_projection_sequence = normalize_projected_dates_for_frequency(
        projected_dates,
        pay_freq or (pattern or {}).get("label"),
    )
    horizon_end = as_of_date + timedelta(days=horizon_days)
    if (
        snapshot_ex_date is not None
        and as_of_date <= snapshot_ex_date <= horizon_end
        and snapshot_ex_date not in forward_projection_sequence
    ):
        forward_projection_sequence = sorted(set(forward_projection_sequence + [snapshot_ex_date]))
        next_date_source = "stockanalysis_ex_dividend_date"
    else:
        next_date_source = (
            f"{history_source}_pattern_projection" if forward_projection_sequence else "none"
        )
    cadence_days = _cadence_days_from_dates(history_dates) or _cadence_days_from_frequency(
        pay_freq
    )

    per_payout_total = (median_per_share or 0.0) * shares
    dr = max(0.0, min(1.0, float(drip_rate or 0.0)))
    px = float(spot_price) if spot_price is not None else 0.0
    forward_payout_schedule: list[dict[str, Any]] | None = None
    drip_applied = 0.0
    spot_used: float | None = None

    if (
        dr > 0
        and px > 0
        and (median_per_share or 0) > 0
        and forward_projection_sequence
    ):
        forward_payout_schedule, projected_total, next_estimate_f = build_forward_payout_schedule_with_drip(
            shares=float(shares),
            median_per_share=float(median_per_share or 0.0),
            payout_dates=forward_projection_sequence,
            drip_rate=dr,
            spot_price=px,
        )
        next_estimate = round(next_estimate_f, 2)
        drip_applied = dr
        spot_used = px
    else:
        projected_total = round(per_payout_total * len(forward_projection_sequence), 2)
        next_estimate = round(per_payout_total, 2) if forward_projection_sequence else 0.0

    next_date = forward_projection_sequence[0].isoformat() if forward_projection_sequence else None

    out: dict[str, Any] = {
        "symbol": symbol,
        "shares": shares,
        "signal": signal,
        "confidence_score": float((signal or {}).get("confidence_score") or 0.0),
        "events_observed": int(bundle.get("events_observed") or 0),
        "last_ex_dividend_date": last_date.isoformat() if last_date else None,
        "cadence_days": cadence_days,
        "payout_frequency": pay_freq or (pattern or {}).get("label"),
        "median_distribution_per_share": round(float(median_per_share), 6) if median_per_share else None,
        "next_projected_distribution_date": next_date,
        "next_projected_distribution_amount": next_estimate,
        "forward_projection_sequence": [d.isoformat() for d in forward_projection_sequence],
        "projected_distribution_dates": [d.isoformat() for d in forward_projection_sequence],
        "projected_distribution_total_horizon": projected_total,
        "horizon_days": horizon_days,
        "next_distribution_source": (next_date_source if forward_projection_sequence else "none"),
        "source_comparison_rows": side_by_side[:40],
        "drip_rate_applied": drip_applied,
        "spot_price_used_for_drip": spot_used,
    }
    if forward_payout_schedule is not None:
        out["forward_payout_schedule"] = forward_payout_schedule
    return out
