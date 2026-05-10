"""
License: MIT
Description: NAV erosion analysis — classify whether NAV drifts vs an underlying
benchmark are primarily market-correlated vs distribution / ROC-heavy, using
aligned time series (e.g. from yfinance, issuer CSVs).

Example::

    from datetime import datetime
    import pandas as pd
    from common.compound.nav_erosion_analyzer import NAV_Erosion_Analyzer

    analyzer = NAV_Erosion_Analyzer(ticker="QQQI", underlying_ticker="QQQ")
    analyzer.load_data(
        nav_series=nav_df["QQQI_NAV"],
        underlying_series=nav_df["QQQ"],
        distributions=dist_df["QQQI_Dist"],
        inception_date=datetime(2024, 1, 29),
    )
    analyzer.add_distribution_breakdown(roc_series=dist_df["QQQI_ROC"])
    report = analyzer.generate_report()
    print(report["erosion_classification"])
    print(report["summary"])
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd

ErosionClassification = Literal[
    "insufficient_data",
    "primarily_market_correlated",
    "primarily_distribution_driven",
    "mixed_structural_and_market",
    "roc_signal_ambiguous",
]


class NAV_Erosion_Analyzer:
    """Analyze NAV path vs underlying and cash distributions (optional ROC breakdown)."""

    def __init__(self, ticker: str, underlying_ticker: str) -> None:
        self.ticker = ticker.strip().upper()
        self.underlying_ticker = underlying_ticker.strip().upper()
        self._inception: pd.Timestamp | None = None
        self._nav: pd.Series | None = None
        self._underlying: pd.Series | None = None
        self._distributions: pd.Series | None = None
        self._roc: pd.Series | None = None

    def load_data(
        self,
        nav_series: pd.Series,
        underlying_series: pd.Series,
        distributions: pd.Series,
        inception_date: datetime | pd.Timestamp,
    ) -> None:
        """Set NAV, underlying (price or TR index level), and per-period distributions.

        All series should use a DatetimeIndex (timezone-aware or naive — coerced to naive UTC).
        *distributions*: amounts per payment date (same currency as NAV context); zeros allowed.
        """
        self._inception = pd.Timestamp(inception_date).normalize()
        self._nav = self._prepare_series(nav_series, "nav_series")
        self._underlying = self._prepare_series(underlying_series, "underlying_series")
        self._distributions = self._prepare_series(distributions, "distributions")
        self._slice_from_inception()

    def add_distribution_breakdown(
        self,
        roc_series: pd.Series | None = None,
    ) -> None:
        """Optional: ROC component of distributions (same index semantics as *distributions*)."""
        if roc_series is None:
            self._roc = None
            return
        self._roc = self._prepare_series(roc_series, "roc_series")
        if self._inception is not None and self._roc is not None:
            self._roc = self._roc.loc[self._roc.index >= self._inception]

    def generate_report(self) -> dict[str, Any]:
        """Return classification, narrative summary, numeric metrics, and warnings."""
        warnings: list[str] = []
        metrics: dict[str, Any] = {}

        if not self._ready_for_core():
            return {
                "erosion_classification": "insufficient_data",
                "summary": "Insufficient overlapping NAV and underlying history after inception.",
                "metrics": metrics,
                "warnings": ["Load NAV and underlying series with a common date range."],
            }

        nav, und = self._aligned_nav_underlying()
        metrics["observations"] = int(len(nav))
        metrics["first_date"] = nav.index.min().isoformat()
        metrics["last_date"] = nav.index.max().isoformat()

        nav_r = nav.pct_change().dropna()
        und_r = und.pct_change().dropna()
        joined = pd.concat([nav_r, und_r], axis=1, join="inner").dropna()
        joined.columns = ["nav_ret", "und_ret"]

        if len(joined) < 10:
            return {
                "erosion_classification": "insufficient_data",
                "summary": "Too few overlapping return observations for stable classification.",
                "metrics": {**metrics, "return_pairs": len(joined)},
                "warnings": warnings + ["Need more overlapping daily (or step) returns."],
            }

        corr = float(joined["nav_ret"].corr(joined["und_ret"]))
        metrics["return_correlation"] = round(corr, 4)

        nav_f = nav.astype(float)
        und_f = und.astype(float)
        expected_nav = float(nav_f.iloc[0]) * (und_f / float(und_f.iloc[0]))
        track_ratio = nav_f / expected_nav.replace(0, np.nan)
        metrics["nav_vs_synthetic_tracking_ratio_end"] = round(float(track_ratio.iloc[-1]), 6)
        metrics["nav_vs_synthetic_tracking_ratio_min"] = round(float(track_ratio.min()), 6)

        # Shortfall when NAV ends below the path implied by scaling the underlying from inception.
        implied_shortfall = 1.0 - float(nav_f.iloc[-1]) / float(expected_nav.iloc[-1])
        metrics["cumulative_nav_shortfall_vs_synthetic_underlying_path"] = round(implied_shortfall, 6)

        years = (nav.index[-1] - nav.index[0]).days / 365.25
        metrics["years_spanned"] = round(years, 4) if years > 0 else 0.0

        dist_metrics = self._distribution_metrics(nav, warnings)
        metrics.update(dist_metrics)

        roc_share = metrics.get("roc_share_of_distributions")
        dist_yield = metrics.get("annualized_distribution_to_avg_nav")

        label, summary = self._classify(
            corr=corr,
            implied_shortfall=implied_shortfall,
            dist_yield=dist_yield,
            roc_share=roc_share,
        )
        return {
            "erosion_classification": label,
            "summary": summary,
            "metrics": metrics,
            "warnings": warnings,
        }

    # ── internals ─────────────────────────────────────────────────────────

    def _ready_for_core(self) -> bool:
        return (
            self._nav is not None
            and self._underlying is not None
            and len(self._nav.dropna()) >= 5
            and len(self._underlying.dropna()) >= 5
        )

    @staticmethod
    def _prepare_series(s: pd.Series, name: str) -> pd.Series:
        if not isinstance(s, pd.Series):
            raise TypeError(f"{name} must be a pandas Series")
        out = s.copy()
        if not isinstance(out.index, pd.DatetimeIndex):
            out.index = pd.to_datetime(out.index)
        out = out.sort_index()
        out = out[~out.index.duplicated(keep="last")]
        return pd.to_numeric(out, errors="coerce")

    def _slice_from_inception(self) -> None:
        if self._inception is None:
            return
        if self._nav is not None:
            self._nav = self._nav.loc[self._nav.index >= self._inception].dropna()
        if self._underlying is not None:
            self._underlying = self._underlying.loc[self._underlying.index >= self._inception].dropna()
        if self._distributions is not None:
            self._distributions = self._distributions.loc[
                self._distributions.index >= self._inception
            ]

    def _aligned_nav_underlying(self) -> tuple[pd.Series, pd.Series]:
        assert self._nav is not None and self._underlying is not None
        df = pd.concat([self._nav, self._underlying], axis=1, join="inner").dropna()
        df.columns = ["nav", "und"]
        return df["nav"], df["und"]

    def _distribution_metrics(self, nav: pd.Series, warnings: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self._distributions is None or self._distributions.empty:
            out["total_distributions"] = 0.0
            out["annualized_distribution_to_avg_nav"] = None
            out["roc_share_of_distributions"] = None
            warnings.append("No distribution series — classification ignores payout intensity.")
            return out

        dist = self._distributions.dropna()
        dist = dist[dist.index >= nav.index.min()]
        total_dist = float(dist.sum())
        out["total_distributions"] = round(total_dist, 6)
        avg_nav = float(nav.mean())
        years = (nav.index[-1] - nav.index[0]).days / 365.25
        if avg_nav > 0 and years > 0:
            out["annualized_distribution_to_avg_nav"] = round(
                (total_dist / years) / avg_nav,
                6,
            )
        else:
            out["annualized_distribution_to_avg_nav"] = None

        if self._roc is not None and not self._roc.empty:
            roc = self._roc.reindex(dist.index, fill_value=0.0)
            roc_sum = float(roc.sum())
            if total_dist > 0:
                out["roc_share_of_distributions"] = round(
                    max(0.0, min(1.0, roc_sum / total_dist)),
                    6,
                )
            else:
                out["roc_share_of_distributions"] = None
        else:
            out["roc_share_of_distributions"] = None

        return out

    def _classify(
        self,
        *,
        corr: float,
        implied_shortfall: float,
        dist_yield: float | None,
        roc_share: float | None,
    ) -> tuple[ErosionClassification, str]:
        """Heuristic boundary rules — tune thresholds with backtests for your universe."""
        high_corr = corr >= 0.85
        deep_shortfall = implied_shortfall >= 0.12
        moderate_shortfall = implied_shortfall >= 0.05
        high_yield_drag = dist_yield is not None and dist_yield >= 0.15
        high_roc = roc_share is not None and roc_share >= 0.35

        if deep_shortfall and (high_yield_drag or high_roc) and corr < 0.92:
            cls: ErosionClassification = "primarily_distribution_driven"
            summary = (
                f"{self.ticker} NAV ends materially below a path synced to {self.underlying_ticker} "
                f"(≈{implied_shortfall:.1%} gap vs synthetic benchmark path), with elevated "
                f"distribution load relative to average NAV"
                + (" and a large ROC share of payouts" if high_roc else "")
                + ". That pattern is more consistent with distribution-driven NAV erosion than pure beta tracking."
            )
            return cls, summary

        if high_corr and not deep_shortfall:
            cls = "primarily_market_correlated"
            summary = (
                f"NAV returns are tightly correlated with {self.underlying_ticker} (r≈{corr:.2f}) and "
                f"the cumulative gap vs a synthetic underlying-matched path is modest (≈{implied_shortfall:.1%}). "
                "Drawdowns are more consistent with shared market exposure than with dominant structural NAV decay."
            )
            return cls, summary

        if high_roc and roc_share is not None and moderate_shortfall:
            cls = "roc_signal_ambiguous"
            summary = (
                f"A large share of distributions is classified as ROC (≈{roc_share:.0%}), but NAV vs "
                f"{self.underlying_ticker} alone cannot prove economic principal return vs tax reclassification. "
                "Treat permanent impairment risk as uncertain without issuer tax breakdowns and NII coverage data."
            )
            return cls, summary

        cls = "mixed_structural_and_market"
        summary = (
            f"NAV shows a moderate gap vs a {self.underlying_ticker}-matched path (≈{implied_shortfall:.1%}) "
            f"with return correlation ≈{corr:.2f}. Market cycles and payout mechanics likely both matter; "
            "review distribution coverage, Section 19 / tax character, and peer NAV paths."
        )
        return cls, summary
