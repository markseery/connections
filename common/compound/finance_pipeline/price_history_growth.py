"""Symbol price-history growth analyzer with confidence scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from common.simple.yfinance_warnings import suppress_utcnow_deprecation_warning
import yfinance as yf

suppress_utcnow_deprecation_warning()


@dataclass
class PriceHistoryGrowthResult:
    symbol: str
    has_history: bool
    start_date: str | None
    end_date: str | None
    trading_days: int
    calendar_days: int
    years_of_history: float
    start_price: float | None
    end_price: float | None
    growth_pct: float
    annualized_growth_pct: float
    growth_direction: str
    confidence_score: float
    confidence_label: str


class PriceHistoryGrowthAnalyzer:
    """Compute whole-history growth and confidence for a symbol."""

    def __init__(self, symbol: str) -> None:
        sym = str(symbol or "").strip().upper()
        if not sym:
            raise ValueError("symbol must not be empty")
        self.symbol = sym

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _confidence_from_years(years: float) -> tuple[float, str]:
        # Slower non-linear saturation so short histories do not look overconfident.
        # ~1.4 for 1 month, ~15.4 for 1 year, ~56.5 for 5 years, ~81.1 for 10 years.
        years = max(0.0, years)
        score = (1.0 - math.exp(-(years / 6.0))) * 100.0
        score = round(score, 2)
        if score >= 85:
            label = "very_high"
        elif score >= 65:
            label = "high"
        elif score >= 40:
            label = "medium"
        elif score >= 20:
            label = "low"
        else:
            label = "very_low"
        return score, label

    def analyze(self) -> PriceHistoryGrowthResult:
        ticker = yf.Ticker(self.symbol)
        history = ticker.history(period="max", auto_adjust=True)
        if history is None or history.empty:
            return PriceHistoryGrowthResult(
                symbol=self.symbol,
                has_history=False,
                start_date=None,
                end_date=None,
                trading_days=0,
                calendar_days=0,
                years_of_history=0.0,
                start_price=None,
                end_price=None,
                growth_pct=0.0,
                annualized_growth_pct=0.0,
                growth_direction="unknown",
                confidence_score=0.0,
                confidence_label="none",
            )

        closes = history["Close"].dropna()
        if closes.empty or len(closes) < 2:
            return PriceHistoryGrowthResult(
                symbol=self.symbol,
                has_history=False,
                start_date=None,
                end_date=None,
                trading_days=int(len(closes)),
                calendar_days=0,
                years_of_history=0.0,
                start_price=None,
                end_price=None,
                growth_pct=0.0,
                annualized_growth_pct=0.0,
                growth_direction="unknown",
                confidence_score=0.0,
                confidence_label="none",
            )

        first_idx = closes.index[0]
        last_idx = closes.index[-1]
        start_price = self._to_float(closes.iloc[0])
        end_price = self._to_float(closes.iloc[-1])
        if start_price is None or end_price is None or start_price <= 0:
            return PriceHistoryGrowthResult(
                symbol=self.symbol,
                has_history=False,
                start_date=None,
                end_date=None,
                trading_days=int(len(closes)),
                calendar_days=0,
                years_of_history=0.0,
                start_price=None,
                end_price=None,
                growth_pct=0.0,
                annualized_growth_pct=0.0,
                growth_direction="unknown",
                confidence_score=0.0,
                confidence_label="none",
            )

        start_date = first_idx.date() if hasattr(first_idx, "date") else None
        end_date = last_idx.date() if hasattr(last_idx, "date") else None
        if not isinstance(start_date, date) or not isinstance(end_date, date):
            return PriceHistoryGrowthResult(
                symbol=self.symbol,
                has_history=False,
                start_date=None,
                end_date=None,
                trading_days=int(len(closes)),
                calendar_days=0,
                years_of_history=0.0,
                start_price=None,
                end_price=None,
                growth_pct=0.0,
                annualized_growth_pct=0.0,
                growth_direction="unknown",
                confidence_score=0.0,
                confidence_label="none",
            )

        calendar_days = max(0, (end_date - start_date).days)
        years = calendar_days / 365.25
        growth_pct = ((end_price / start_price) - 1.0) * 100.0
        annualized_growth_pct = (
            (((end_price / start_price) ** (1.0 / years)) - 1.0) * 100.0
            if years > 0
            else growth_pct
        )
        direction = (
            "positive"
            if growth_pct > 0
            else ("negative" if growth_pct < 0 else "flat")
        )
        confidence_score, confidence_label = self._confidence_from_years(years)

        return PriceHistoryGrowthResult(
            symbol=self.symbol,
            has_history=True,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            trading_days=int(len(closes)),
            calendar_days=int(calendar_days),
            years_of_history=round(years, 4),
            start_price=round(start_price, 6),
            end_price=round(end_price, 6),
            growth_pct=round(growth_pct, 4),
            annualized_growth_pct=round(annualized_growth_pct, 4),
            growth_direction=direction,
            confidence_score=confidence_score,
            confidence_label=confidence_label,
        )


__all__ = ["PriceHistoryGrowthAnalyzer", "PriceHistoryGrowthResult"]

