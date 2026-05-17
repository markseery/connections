"""Long-horizon price stability stats and benchmark comparisons (equity + 10Y yield)."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

TRADING_DAYS_PER_YEAR = 252
TRADING_DAYS_PER_MONTH = 21  # ~252 / 12
DEFAULT_EQUITY_BENCHMARKS: tuple[str, ...] = ("QQQ", "SPY", "IWM")
TREASURY_10Y_TICKER = "^TNX"  # CBOE 10-year Treasury yield (%)

# Display order for end-of-run correlation summaries (daily return correlation).
CORRELATION_BUCKET_ORDER: tuple[str, ...] = (
    "very negative (< -0.50)",
    "negative (-0.50 to -0.30)",
    "weak negative (-0.30 to -0.10)",
    "near zero (-0.10 to 0.10)",
    "weak positive (0.10 to 0.30)",
    "low (0.30 to 0.50)",
    "moderate (0.50 to 0.70)",
    "high (0.70 to 0.90)",
    "very high (≥ 0.90)",
)


def correlation_bucket_label(corr: float) -> str:
    """Map a Pearson correlation to a human-readable range label."""
    c = float(corr)
    if c < -0.50:
        return CORRELATION_BUCKET_ORDER[0]
    if c < -0.30:
        return CORRELATION_BUCKET_ORDER[1]
    if c < -0.10:
        return CORRELATION_BUCKET_ORDER[2]
    if c < 0.10:
        return CORRELATION_BUCKET_ORDER[3]
    if c < 0.30:
        return CORRELATION_BUCKET_ORDER[4]
    if c < 0.50:
        return CORRELATION_BUCKET_ORDER[5]
    if c < 0.70:
        return CORRELATION_BUCKET_ORDER[6]
    if c < 0.90:
        return CORRELATION_BUCKET_ORDER[7]
    return CORRELATION_BUCKET_ORDER[8]


def _close(record: Dict[str, Any]) -> Optional[float]:
    v = record.get("Close") if record.get("Close") is not None else record.get("close")
    if v is None:
        return None
    try:
        c = float(v)
        return c if c > 0 else None
    except (TypeError, ValueError):
        return None


def _date_key(record: Dict[str, Any]) -> Optional[str]:
    d = record.get("Date") or record.get("date")
    if d is None:
        return None
    return str(d)[:10]


def returns_by_date(history: List[Dict[str, Any]]) -> Dict[str, float]:
    """Map ISO date -> simple daily return from prior close."""
    out: Dict[str, float] = {}
    prev_close: Optional[float] = None
    prev_date: Optional[str] = None
    for row in history:
        dk = _date_key(row)
        c = _close(row)
        if not dk or c is None:
            continue
        if prev_close is not None and prev_date:
            out[dk] = (c / prev_close) - 1.0
        prev_close = c
        prev_date = dk
    return out


def closes_by_date(history: List[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for row in history:
        dk = _date_key(row)
        c = _close(row)
        if dk and c is not None:
            out[dk] = c
    return out


def _align_pair(a: Dict[str, float], b: Dict[str, float]) -> Tuple[List[float], List[float]]:
    keys = sorted(set(a.keys()) & set(b.keys()))
    if len(keys) < 2:
        return [], []
    return [a[k] for k in keys], [b[k] for k in keys]


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs)


def _sample_std(xs: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mu = _mean(xs)
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)


def _annualized_vol_pct(daily_returns: List[float]) -> Optional[float]:
    sd = _sample_std(daily_returns)
    if sd is None:
        return None
    return round(sd * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 2)


def _downside_deviation_pct(daily_returns: List[float]) -> Optional[float]:
    neg = [r for r in daily_returns if r < 0]
    if len(neg) < 2:
        return 0.0 if daily_returns else None
    mu = _mean(neg)
    var = sum((r - mu) ** 2 for r in neg) / (len(neg) - 1)
    return round(math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 2)


def _max_drawdown_pct(closes: List[float]) -> Optional[float]:
    if len(closes) < 2:
        return None
    peak = closes[0]
    mdd = 0.0
    for c in closes:
        peak = max(peak, c)
        dd = (c / peak) - 1.0
        mdd = min(mdd, dd)
    return round(mdd * 100.0, 2)


def _trailing_period_growths(
    closes: List[float],
    *,
    leg_sessions: int,
    max_legs: int,
) -> List[float]:
    n = len(closes)
    growths: List[float] = []
    for i in range(max_legs):
        end_idx = n - 1 - i * leg_sessions
        start_idx = end_idx - leg_sessions
        if start_idx < 0:
            break
        start_c, end_c = closes[start_idx], closes[end_idx]
        if start_c <= 0:
            continue
        growths.append(round((end_c / start_c - 1.0) * 100.0, 2))
    return growths


def _price_growth_result(
    *,
    growth_period: str,
    growths: List[float],
    periods_requested: int,
) -> Dict[str, Any]:
    empty: Dict[str, Any] = {
        "growth_period": growth_period,
        "median_growth_pct": None,
        "growth_rates_pct": [],
        "periods_requested": periods_requested,
        "periods_used": 0,
        "insufficient_history": True,
    }
    if not growths:
        return empty
    med = round(float(statistics.median(growths)), 2)
    out: Dict[str, Any] = {
        "growth_period": growth_period,
        "median_growth_pct": med,
        "growth_rates_pct": growths,
        "periods_requested": periods_requested,
        "periods_used": len(growths),
        "insufficient_history": False,
    }
    if growth_period == "annual":
        out["median_annual_growth_pct"] = med
        out["annual_growth_rates_pct"] = growths
        out["years_requested"] = periods_requested
        out["years_used"] = len(growths)
    else:
        out["median_monthly_growth_pct"] = med
        out["monthly_growth_rates_pct"] = growths
        out["months_used"] = len(growths)
    return out


def median_annual_price_growth(
    history: List[Dict[str, Any]],
    *,
    years: int = 5,
) -> Dict[str, Any]:
    """
    Median trailing price growth over available history.

    - **≥ 1 year** of sessions: median of up to ``years`` trailing 12-month (~252d) legs.
    - **< 1 year**: median of trailing ~1-month (~21d) legs (labeled monthly in output).
    """
    closes_map = closes_by_date(history)
    dates = sorted(closes_map.keys())
    closes = [closes_map[d] for d in dates]
    n = len(closes)

    if n >= TRADING_DAYS_PER_YEAR + 1 and years >= 1:
        growths = _trailing_period_growths(
            closes, leg_sessions=TRADING_DAYS_PER_YEAR, max_legs=years
        )
        result = _price_growth_result(
            growth_period="annual",
            growths=growths,
            periods_requested=years,
        )
        if growths:
            result["insufficient_history"] = len(growths) < years
        return result

    # Shorter than one year: monthly legs over all available complete months
    max_months = max(0, (n - 1) // TRADING_DAYS_PER_MONTH)
    if max_months < 1:
        return _price_growth_result(
            growth_period="monthly", growths=[], periods_requested=max_months
        )
    growths = _trailing_period_growths(
        closes, leg_sessions=TRADING_DAYS_PER_MONTH, max_legs=max_months
    )
    return _price_growth_result(
        growth_period="monthly",
        growths=growths,
        periods_requested=max_months,
    )


def _cagr_pct(closes: List[float], n_returns: int) -> Optional[float]:
    if len(closes) < 2 or n_returns < 1:
        return None
    start, end = closes[0], closes[-1]
    if start <= 0:
        return None
    years = n_returns / TRADING_DAYS_PER_YEAR
    if years <= 0:
        return None
    total = end / start
    if total <= 0:
        return None
    cagr = total ** (1.0 / years) - 1.0
    return round(cagr * 100.0, 2)


def _beta_and_corr(sym: List[float], bench: List[float]) -> Tuple[Optional[float], Optional[float]]:
    n = len(sym)
    if n < 20 or n != len(bench):
        return None, None
    ms, mb = _mean(sym), _mean(bench)
    cov = sum((sym[i] - ms) * (bench[i] - mb) for i in range(n)) / (n - 1)
    vs = sum((sym[i] - ms) ** 2 for i in range(n)) / (n - 1)
    vb = sum((bench[i] - mb) ** 2 for i in range(n)) / (n - 1)
    if vs <= 0 or vb <= 0:
        return None, None
    beta = cov / vs
    corr = cov / (math.sqrt(vs) * math.sqrt(vb))
    return round(beta, 3), round(corr, 3)


@dataclass
class BenchmarkComparison:
    ticker: str
    beta: Optional[float] = None
    correlation: Optional[float] = None
    benchmark_ann_vol_pct: Optional[float] = None
    vol_ratio_vs_symbol: Optional[float] = None  # symbol_vol / benchmark_vol


@dataclass
class PriceStabilityResult:
    symbol: str
    trading_days: int = 0
    years_span: Optional[float] = None
    ann_volatility_pct: Optional[float] = None
    downside_deviation_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    cagr_pct: Optional[float] = None
    vs_equity: Dict[str, BenchmarkComparison] = field(default_factory=dict)
    treasury_10y_yield_pct: Optional[float] = None
    corr_vs_10y_yield_change: Optional[float] = None
    stability_note: Optional[str] = None
    insufficient_history: bool = False

    def format_lines(self) -> List[str]:
        if self.insufficient_history:
            return ["  PriceStability: insufficient history for long-term stats"]
        lines = [
            f"  PriceStability ({self.trading_days} sessions, ~{self.years_span or 0:.1f}y):",
            f"    ann_vol={self.ann_volatility_pct}% downside_dev={self.downside_deviation_pct}% "
            f"max_drawdown={self.max_drawdown_pct}% CAGR={self.cagr_pct}%",
        ]
        if self.stability_note:
            lines.append(f"    note: {self.stability_note}")
        for tk, bc in sorted(self.vs_equity.items()):
            lines.append(
                f"    vs {tk}: beta={bc.beta} corr={bc.correlation} "
                f"(benchmark vol={bc.benchmark_ann_vol_pct}%, vol_ratio={bc.vol_ratio_vs_symbol})"
            )
        if self.treasury_10y_yield_pct is not None:
            lines.append(
                f"    vs 10Y Treasury: latest_yield={self.treasury_10y_yield_pct}% "
                f"corr(daily_return, yield_change)={self.corr_vs_10y_yield_change}"
            )
        return lines


def _stability_note(ann_vol: Optional[float], max_dd: Optional[float], vol_ratio_spy: Optional[float]) -> Optional[str]:
    if ann_vol is None:
        return None
    parts: List[str] = []
    if ann_vol < 12:
        parts.append("low realized volatility")
    elif ann_vol > 35:
        parts.append("high realized volatility")
    else:
        parts.append("moderate realized volatility")
    if max_dd is not None:
        if max_dd > -15:
            parts.append("shallow historical drawdowns")
        elif max_dd < -40:
            parts.append("deep historical drawdowns")
    if vol_ratio_spy is not None:
        if vol_ratio_spy < 0.85:
            parts.append("calmer than SPY over the window")
        elif vol_ratio_spy > 1.25:
            parts.append("more volatile than SPY over the window")
    return "; ".join(parts)


class PriceStabilityAnalyzer:
    """Compute stability metrics vs equity benchmarks and 10Y Treasury yield."""

    def __init__(
        self,
        equity_benchmarks: tuple[str, ...] = DEFAULT_EQUITY_BENCHMARKS,
        treasury_yield_history: Optional[List[Dict[str, Any]]] = None,
        min_sessions: int = 60,
    ) -> None:
        self.equity_benchmarks = equity_benchmarks
        self.treasury_yield_history = treasury_yield_history or []
        self.min_sessions = min_sessions
        self._bench_returns: Dict[str, Dict[str, float]] = {}
        self._bench_ann_vol: Dict[str, Optional[float]] = {}
        self._yield_returns: Dict[str, float] = {}
        self._yield_chg_returns: Dict[str, float] = {}
        self._latest_yield: Optional[float] = None
        self._prepare_treasury()

    def _prepare_treasury(self) -> None:
        for row in self.treasury_yield_history:
            dk = _date_key(row)
            c = _close(row)
            if dk and c is not None:
                self._yield_returns[dk] = c
        if self._yield_returns:
            dates = sorted(self._yield_returns.keys())
            self._latest_yield = round(self._yield_returns[dates[-1]], 3)
            # daily change in yield (percentage points)
            prev = None
            chg: Dict[str, float] = {}
            for d in dates:
                y = self._yield_returns[d]
                if prev is not None:
                    chg[d] = y - prev
                prev = y
            self._yield_chg_returns = chg

    def set_benchmark_history(self, ticker: str, history: List[Dict[str, Any]]) -> None:
        rets = returns_by_date(history)
        self._bench_returns[ticker] = rets
        aligned = list(rets.values())
        self._bench_ann_vol[ticker] = _annualized_vol_pct(aligned) if len(aligned) >= 2 else None

    def analyze(self, symbol: str, history: List[Dict[str, Any]]) -> PriceStabilityResult:
        sym_rets = returns_by_date(history)
        closes_map = closes_by_date(history)
        dates = sorted(closes_map.keys())
        closes = [closes_map[d] for d in dates]
        daily = list(sym_rets.values())

        if len(daily) < self.min_sessions:
            return PriceStabilityResult(symbol=symbol, insufficient_history=True)

        ann_vol = _annualized_vol_pct(daily)
        max_dd = _max_drawdown_pct(closes)
        cagr = _cagr_pct(closes, len(daily))
        ddown = _downside_deviation_pct(daily)

        vs: Dict[str, BenchmarkComparison] = {}
        vol_ratio_spy: Optional[float] = None
        for tk in self.equity_benchmarks:
            if tk.upper() == symbol.upper():
                continue
            bench = self._bench_returns.get(tk)
            if not bench:
                vs[tk] = BenchmarkComparison(ticker=tk)
                continue
            s_aligned, b_aligned = _align_pair(sym_rets, bench)
            beta, corr = _beta_and_corr(s_aligned, b_aligned)
            b_vol = self._bench_ann_vol.get(tk)
            v_ratio = round(ann_vol / b_vol, 2) if ann_vol is not None and b_vol and b_vol > 0 else None
            if tk == "SPY":
                vol_ratio_spy = v_ratio
            vs[tk] = BenchmarkComparison(
                ticker=tk,
                beta=beta,
                correlation=corr,
                benchmark_ann_vol_pct=b_vol,
                vol_ratio_vs_symbol=v_ratio,
            )

        corr_yield: Optional[float] = None
        if getattr(self, "_yield_chg_returns", None):
            s2, y2 = _align_pair(sym_rets, self._yield_chg_returns)
            _, corr_yield = _beta_and_corr(s2, y2)

        years = round(len(daily) / TRADING_DAYS_PER_YEAR, 2)
        note = _stability_note(ann_vol, max_dd, vol_ratio_spy)

        return PriceStabilityResult(
            symbol=symbol,
            trading_days=len(daily),
            years_span=years,
            ann_volatility_pct=ann_vol,
            downside_deviation_pct=ddown,
            max_drawdown_pct=max_dd,
            cagr_pct=cagr,
            vs_equity=vs,
            treasury_10y_yield_pct=self._latest_yield,
            corr_vs_10y_yield_change=corr_yield,
            stability_note=note,
            insufficient_history=False,
        )
