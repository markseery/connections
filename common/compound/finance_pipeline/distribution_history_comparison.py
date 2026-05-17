"""Utilities for normalized per-share distribution history comparisons."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from common.simple.yfinance_warnings import suppress_utcnow_deprecation_warning
import yfinance as yf

from common.compound.finance_pipeline.stockanalysis_dividends import StockAnalysisDividendExtractor

suppress_utcnow_deprecation_warning()


@dataclass
class DistributionPoint:
    ex_dividend_date: str
    amount_per_share: float


@dataclass
class DistributionSeries:
    symbol: str
    source: str
    points: list[DistributionPoint]


class DistributionHistoryComparison:
    """Fetch and compare per-share distribution history across sources."""

    def __init__(self, symbol: str) -> None:
        sym = str(symbol or "").strip().upper()
        if not sym:
            raise ValueError("symbol must not be empty")
        self.symbol = sym
        self._stockanalysis = StockAnalysisDividendExtractor()
        self._stockanalysis_snapshot: Any | None = None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_iso_date(value: Any) -> str | None:
        if value is None:
            return None
        # pandas Timestamp / datetime
        if hasattr(value, "date"):
            d = value.date()
            if isinstance(d, date):
                return d.isoformat()
        text = str(value).strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    def fetch_yfinance_series(self) -> DistributionSeries:
        ticker = yf.Ticker(self.symbol)
        dividends = ticker.dividends
        points: list[DistributionPoint] = []
        if dividends is not None and not dividends.empty:
            for idx, value in dividends.items():
                d = self._to_iso_date(idx)
                amount = self._to_float(value)
                if d is None or amount is None:
                    continue
                if amount <= 0:
                    continue
                points.append(
                    DistributionPoint(
                        ex_dividend_date=d,
                        amount_per_share=round(amount, 8),
                    )
                )
        points.sort(key=lambda p: p.ex_dividend_date, reverse=True)
        return DistributionSeries(symbol=self.symbol, source="yfinance", points=points)

    def fetch_stockanalysis_snapshot(self):
        from common.compound.finance_pipeline.stockanalysis_dividends import StockAnalysisDividendSnapshot

        if self._stockanalysis_snapshot is None:
            self._stockanalysis_snapshot = self._stockanalysis.extract(self.symbol)
        return self._stockanalysis_snapshot  # type: StockAnalysisDividendSnapshot

    def fetch_stockanalysis_series(self) -> DistributionSeries:
        snapshot = self.fetch_stockanalysis_snapshot()
        points: list[DistributionPoint] = []
        for row in snapshot.history:
            d = self._to_iso_date(row.ex_dividend_date)
            amount = self._to_float(row.cash_amount)
            if d is None or amount is None:
                continue
            if amount <= 0:
                continue
            points.append(
                DistributionPoint(
                    ex_dividend_date=d,
                    amount_per_share=round(amount, 8),
                )
            )
        points.sort(key=lambda p: p.ex_dividend_date, reverse=True)
        return DistributionSeries(
            symbol=self.symbol, source="stockanalysis", points=points
        )

    def side_by_side(self) -> list[dict[str, Any]]:
        yf_series = self.fetch_yfinance_series()
        sa_series = self.fetch_stockanalysis_series()
        yf_map = {p.ex_dividend_date: p.amount_per_share for p in yf_series.points}
        sa_map = {p.ex_dividend_date: p.amount_per_share for p in sa_series.points}
        all_dates = sorted(set(yf_map.keys()) | set(sa_map.keys()), reverse=True)

        rows: list[dict[str, Any]] = []
        for d in all_dates:
            yv = yf_map.get(d)
            sv = sa_map.get(d)
            abs_diff = None
            pct_diff = None
            if yv is not None and sv is not None:
                abs_diff = round(yv - sv, 8)
                pct_diff = round(((yv - sv) / sv) * 100.0, 6) if sv != 0 else None
            rows.append(
                {
                    "ex_dividend_date": d,
                    "yfinance_amount_per_share": yv,
                    "stockanalysis_amount_per_share": sv,
                    "absolute_diff": abs_diff,
                    "pct_diff_vs_stockanalysis": pct_diff,
                }
            )
        return rows

    @staticmethod
    def _confidence_from_months(months: float) -> float:
        # Slower non-linear saturation for dividend-history confidence.
        # ~3.3 at 1 month, ~33.0 at 12 months, ~86.5 at 5 years.
        if months <= 0:
            return 0.0
        score = (1.0 - math.exp(-(months / 30.0))) * 100.0
        return round(max(0.0, min(100.0, score)), 2)

    @staticmethod
    def _payout_volatility_penalty(amounts: list[float]) -> float:
        """
        Penalty based on coefficient of variation of payout amounts.
        Higher dispersion -> lower confidence.
        """
        clean = [float(a) for a in amounts if a is not None and a > 0]
        if len(clean) < 3:
            return 0.0
        mean_amt = statistics.mean(clean)
        if mean_amt <= 0:
            return 0.0
        cv = statistics.pstdev(clean) / mean_amt
        penalty = min(30.0, max(0.0, cv * 60.0))
        return round(penalty, 2)

    def _source_agreement_bonus(
        self,
        sa_values: dict[str, float],
        yf_values: dict[str, float],
    ) -> float:
        """
        Bonus when yfinance and StockAnalysis agree on overlapping dates.
        """
        overlap_dates = sorted(set(sa_values.keys()) & set(yf_values.keys()))
        if len(overlap_dates) < 2:
            return 0.0
        diffs_pct: list[float] = []
        for d in overlap_dates:
            sv = float(sa_values[d])
            yv = float(yf_values[d])
            if sv <= 0:
                continue
            diffs_pct.append(abs((yv - sv) / sv) * 100.0)
        if len(diffs_pct) < 2:
            return 0.0
        median_diff_pct = statistics.median(diffs_pct)
        if median_diff_pct <= 2.0:
            return 12.0
        if median_diff_pct <= 5.0:
            return 8.0
        if median_diff_pct <= 10.0:
            return 4.0
        return 0.0

    def dividend_history_signal(self) -> dict[str, Any]:
        """
        Build a 30-day per-share distribution-rate signal and confidence score.

        Preference:
        1) stockanalysis series
        2) yfinance series
        """

        def _build(series: DistributionSeries, source: str) -> dict[str, Any] | None:
            if not series.points:
                return None
            dated: list[tuple[date, float]] = []
            per_date_amount: dict[str, float] = {}
            for p in series.points:
                d = self._to_iso_date(p.ex_dividend_date)
                if d is None:
                    continue
                try:
                    dd = datetime.strptime(d, "%Y-%m-%d").date()
                except ValueError:
                    continue
                amt = self._to_float(p.amount_per_share)
                if amt is None or amt <= 0:
                    continue
                dated.append((dd, amt))
                per_date_amount[d] = float(amt)
            if len(dated) < 1:
                return None
            dated.sort(key=lambda x: x[0])
            unique_dates = sorted({d for d, _ in dated})
            if len(unique_dates) >= 2:
                gaps = [
                    (curr - prev).days
                    for prev, curr in zip(unique_dates, unique_dates[1:])
                    if (curr - prev).days > 0
                ]
                median_gap = statistics.median(gaps) if gaps else 30.0
            else:
                median_gap = 30.0
            median_gap = max(1.0, float(median_gap))
            amounts = [a for _, a in dated]
            median_amount = statistics.median(amounts)
            # Normalize to 30-day equivalent per-share rate.
            rate_30 = median_amount * (30.0 / median_gap)
            history_days = (
                max(0, (unique_dates[-1] - unique_dates[0]).days)
                if len(unique_dates) > 1
                else 0
            )
            months = history_days / 30.4375
            base_confidence = self._confidence_from_months(months)
            volatility_penalty = self._payout_volatility_penalty(amounts)
            return {
                "source": source,
                "rate_30_day_per_share": round(rate_30, 8),
                "confidence_base_score": round(base_confidence, 2),
                "confidence_volatility_penalty": round(volatility_penalty, 2),
                "history_months": round(months, 2),
                "events": len(dated),
                "median_gap_days": round(median_gap, 4),
                "amount_cv": round(
                    (
                        (statistics.pstdev(amounts) / statistics.mean(amounts))
                        if len(amounts) >= 2 and statistics.mean(amounts) > 0
                        else 0.0
                    ),
                    6,
                ),
                "values_by_date": per_date_amount,
            }

        sa_snapshot = self.fetch_stockanalysis_snapshot()
        sa = _build(self.fetch_stockanalysis_series(), "stockanalysis")
        yf_signal = _build(self.fetch_yfinance_series(), "yfinance")

        chosen = sa if sa is not None else yf_signal
        if chosen is not None:
            chosen.setdefault("aum_display", sa_snapshot.aum_display)
            chosen.setdefault("aum_usd", sa_snapshot.aum_usd)
            chosen.setdefault("aum_source", "stockanalysis_overview_assets")
            chosen.setdefault("overview_url", sa_snapshot.overview_url)
            agreement_bonus = 0.0
            if sa is not None and yf_signal is not None:
                agreement_bonus = self._source_agreement_bonus(
                    sa.get("values_by_date") or {},
                    yf_signal.get("values_by_date") or {},
                )
            confidence = (
                float(chosen.get("confidence_base_score") or 0.0)
                - float(chosen.get("confidence_volatility_penalty") or 0.0)
                + float(agreement_bonus or 0.0)
            )
            chosen["confidence_source_agreement_bonus"] = round(agreement_bonus, 2)
            chosen["confidence_score"] = round(max(0.0, min(100.0, confidence)), 2)
            chosen["source_agreement_used"] = bool(sa is not None and yf_signal is not None)
            chosen.pop("values_by_date", None)
            return chosen

        sa_snapshot = self.fetch_stockanalysis_snapshot()
        out = {
            "source": "none",
            "rate_30_day_per_share": 0.0,
            "confidence_score": 0.0,
            "confidence_base_score": 0.0,
            "confidence_volatility_penalty": 0.0,
            "confidence_source_agreement_bonus": 0.0,
            "history_months": 0.0,
            "events": 0,
            "median_gap_days": None,
            "amount_cv": 0.0,
            "source_agreement_used": False,
            "aum_display": sa_snapshot.aum_display,
            "aum_usd": sa_snapshot.aum_usd,
            "aum_source": "stockanalysis_overview_assets",
            "overview_url": sa_snapshot.overview_url,
        }
        return out


__all__ = [
    "DistributionHistoryComparison",
    "DistributionPoint",
    "DistributionSeries",
]

