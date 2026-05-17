"""Portfolio-level summaries from per-symbol market analysis snapshots."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from common.compound.market_indicators import EMA_PERIODS, SMA_PERIODS
from common.compound.market_signal import Signal
from common.compound.price_stability import (
    CORRELATION_BUCKET_ORDER,
    DEFAULT_EQUITY_BENCHMARKS,
    TREASURY_10Y_TICKER,
    correlation_bucket_label,
)


def _signal_value(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj.lower().strip() or None
    if isinstance(obj, Signal):
        return str(obj.value).lower()
    val = getattr(obj, "value", None)
    if isinstance(val, str):
        return val.lower()
    return str(obj).lower()


def overbought_notes(snap: Dict[str, Any]) -> List[str]:
    notes: List[str] = []
    rsi = snap.get("rsi14")
    if isinstance(rsi, dict):
        val = rsi.get("value")
        if isinstance(val, (int, float)) and val >= 70:
            notes.append(f"RSI(14) overbought ({val})")
    return notes


def oversold_notes(snap: Dict[str, Any]) -> List[str]:
    notes: List[str] = []
    rsi = snap.get("rsi14")
    if isinstance(rsi, dict):
        val = rsi.get("value")
        if isinstance(val, (int, float)) and val <= 30:
            notes.append(f"RSI(14) oversold ({val})")
    return notes


def sma_above_below_tie(
    snap: Dict[str, Any],
) -> tuple[list[int], list[int], list[int], list[int]]:
    sma = snap.get("sma")
    if not isinstance(sma, dict):
        return [], [], [], list(SMA_PERIODS)
    above: list[int] = []
    below: list[int] = []
    tie: list[int] = []
    missing: list[int] = []
    for p in SMA_PERIODS:
        row = sma.get(str(p))
        if not isinstance(row, dict):
            missing.append(p)
            continue
        cp, val = row.get("current_price"), row.get("value")
        if not isinstance(cp, (int, float)) or not isinstance(val, (int, float)):
            missing.append(p)
            continue
        if cp > val:
            above.append(p)
        elif cp < val:
            below.append(p)
        else:
            tie.append(p)
    return above, below, tie, missing


def bollinger_position_pct(snap: Dict[str, Any]) -> float | None:
    bb = snap.get("bollinger")
    if not isinstance(bb, dict):
        return None
    pos = bb.get("position_pct")
    return float(pos) if isinstance(pos, (int, float)) else None


def stability_corr(
    snap: Dict[str, Any], *, benchmark: str | None = None, treasury: bool = False
) -> float | None:
    ps = snap.get("price_stability")
    if not isinstance(ps, dict) or ps.get("insufficient_history"):
        return None
    if treasury:
        c = ps.get("corr_vs_10y_yield_change")
        return float(c) if isinstance(c, (int, float)) else None
    vs = ps.get("vs_equity")
    if not isinstance(vs, dict) or not benchmark:
        return None
    row = vs.get(benchmark)
    if row is None:
        return None
    if isinstance(row, dict):
        c = row.get("correlation")
    else:
        c = getattr(row, "correlation", None)
    return float(c) if isinstance(c, (int, float)) else None


def _append_calmer_than_benchmark(
    out: List[Dict[str, Any]],
    vs: Dict[str, Any],
    *,
    benchmark: str,
    symbol: str,
    max_ratio: float,
) -> None:
    row = vs.get(benchmark)
    if not isinstance(row, dict):
        return
    ratio = row.get("vol_ratio_vs_symbol")
    if isinstance(ratio, (int, float)) and ratio <= max_ratio:
        out.append(
            {
                "symbol": symbol,
                f"vol_ratio_vs_{benchmark}": float(ratio),
            }
        )


def build_summaries(
    snapshots: List[Dict[str, Any]],
    *,
    bollinger_band_pct: float = 5.0,
    calmer_than_spy_max_ratio: float = 0.9,
) -> Dict[str, Any]:
    """Structured summary buckets for a portfolio run."""
    overbought: List[Dict[str, Any]] = []
    oversold: List[Dict[str, Any]] = []
    bollinger_near_upper: List[Dict[str, Any]] = []
    bollinger_near_lower: List[Dict[str, Any]] = []
    sma_all_above: List[str] = []
    sma_all_below: List[str] = []
    sma_mixed: List[Dict[str, Any]] = []
    macd_buy: List[Dict[str, Any]] = []
    macd_sell: List[Dict[str, Any]] = []
    price_stability: List[Dict[str, Any]] = []
    median_price_growth_annual: List[Dict[str, Any]] = []
    median_price_growth_monthly: List[Dict[str, Any]] = []
    median_price_growth_missing: List[str] = []
    calmer_than_spy: List[Dict[str, Any]] = []
    calmer_than_qqq: List[Dict[str, Any]] = []

    floor = 100.0 - bollinger_band_pct
    ceiling = bollinger_band_pct
    n_sma = len(SMA_PERIODS)

    for snap in snapshots:
        sym = str(snap.get("symbol") or "")
        ob = overbought_notes(snap)
        if ob:
            overbought.append({"symbol": sym, "notes": ob})
        osn = oversold_notes(snap)
        if osn:
            oversold.append({"symbol": sym, "notes": osn})

        pos = bollinger_position_pct(snap)
        if pos is not None and pos >= floor:
            bollinger_near_upper.append({"symbol": sym, "position_pct": pos})
        if pos is not None and pos <= ceiling:
            bollinger_near_lower.append({"symbol": sym, "position_pct": pos})

        above, below, tie, missing = sma_above_below_tie(snap)
        if not missing and not tie and len(above) == n_sma and not below:
            sma_all_above.append(sym)
        if not missing and not tie and not above and len(below) == n_sma:
            sma_all_below.append(sym)
        if not missing and above and below:
            if not (len(above) == n_sma and not below and not tie):
                if not (len(below) == n_sma and not above and not tie):
                    sma_mixed.append(
                        {"symbol": sym, "above": sorted(above), "below": sorted(below)}
                    )

        m = snap.get("macd")
        if isinstance(m, dict):
            if _signal_value(m.get("signal")) == Signal.BULLISH.value:
                macd_buy.append(
                    {
                        "symbol": sym,
                        "histogram": m.get("histogram"),
                        "macd_line": m.get("macd_line"),
                        "signal_line": m.get("signal_line"),
                    }
                )
            if _signal_value(m.get("signal")) == Signal.BEARISH.value:
                macd_sell.append(
                    {
                        "symbol": sym,
                        "histogram": m.get("histogram"),
                        "macd_line": m.get("macd_line"),
                        "signal_line": m.get("signal_line"),
                    }
                )

        ps = snap.get("price_stability")
        if isinstance(ps, dict) and not ps.get("insufficient_history"):
            vol = ps.get("ann_volatility_pct")
            if isinstance(vol, (int, float)):
                price_stability.append(
                    {
                        "symbol": sym,
                        "ann_volatility_pct": float(vol),
                        "max_drawdown_pct": ps.get("max_drawdown_pct"),
                        "stability_note": ps.get("stability_note"),
                    }
                )
            vs = ps.get("vs_equity")
            if isinstance(vs, dict):
                _append_calmer_than_benchmark(
                    calmer_than_spy,
                    vs,
                    benchmark="SPY",
                    symbol=sym,
                    max_ratio=calmer_than_spy_max_ratio,
                )
                _append_calmer_than_benchmark(
                    calmer_than_qqq,
                    vs,
                    benchmark="QQQ",
                    symbol=sym,
                    max_ratio=calmer_than_spy_max_ratio,
                )

        pg = snap.get("price_growth")
        if isinstance(pg, dict):
            med = pg.get("median_growth_pct")
            if med is None:
                med = pg.get("median_annual_growth_pct") or pg.get("median_monthly_growth_pct")
            if isinstance(med, (int, float)):
                period = str(pg.get("growth_period") or "annual")
                row = {
                    "symbol": sym,
                    "growth_period": period,
                    "median_growth_pct": float(med),
                    "growth_rates_pct": pg.get("growth_rates_pct")
                    or pg.get("annual_growth_rates_pct")
                    or pg.get("monthly_growth_rates_pct")
                    or [],
                    "periods_used": pg.get("periods_used")
                    or pg.get("years_used")
                    or pg.get("months_used"),
                }
                if period == "monthly":
                    median_price_growth_monthly.append(row)
                else:
                    median_price_growth_annual.append(row)
            else:
                median_price_growth_missing.append(sym)
        else:
            median_price_growth_missing.append(sym)

    correlations: Dict[str, Any] = {}
    for bench in DEFAULT_EQUITY_BENCHMARKS:
        correlations[bench] = _correlation_bucket_map(
            snapshots, benchmark=bench, treasury=False
        )
    correlations["10Y_Treasury"] = _correlation_bucket_map(
        snapshots, benchmark=None, treasury=True
    )

    return {
        "_meta": {"symbol_count": len(snapshots)},
        "overbought": overbought,
        "oversold": oversold,
        "bollinger_near_upper": sorted(
            bollinger_near_upper, key=lambda x: -x["position_pct"]
        ),
        "bollinger_near_lower": sorted(
            bollinger_near_lower, key=lambda x: x["position_pct"]
        ),
        "sma_all_above": sorted(sma_all_above),
        "sma_all_below": sorted(sma_all_below),
        "sma_mixed": sorted(sma_mixed, key=lambda x: x["symbol"]),
        "macd_buy": sorted(macd_buy, key=lambda x: x["symbol"]),
        "macd_sell": sorted(macd_sell, key=lambda x: x["symbol"]),
        "price_stability": sorted(price_stability, key=lambda x: x["ann_volatility_pct"]),
        "median_price_growth_annual": sorted(
            median_price_growth_annual, key=lambda x: -x["median_growth_pct"]
        ),
        "median_price_growth_monthly": sorted(
            median_price_growth_monthly, key=lambda x: -x["median_growth_pct"]
        ),
        "median_price_growth": sorted(
            median_price_growth_annual + median_price_growth_monthly,
            key=lambda x: -x["median_growth_pct"],
        ),
        "median_price_growth_missing": sorted(median_price_growth_missing),
        "calmer_than_spy": sorted(calmer_than_spy, key=lambda x: x["vol_ratio_vs_SPY"]),
        "calmer_than_qqq": sorted(calmer_than_qqq, key=lambda x: x["vol_ratio_vs_QQQ"]),
        "correlations": correlations,
    }


def _format_median_growth_line(row: Dict[str, Any], *, monthly: bool | None = None) -> str:
    sym = row.get("symbol", "")
    med = row.get("median_growth_pct")
    rates = row.get("growth_rates_pct") or []
    periods_used = row.get("periods_used")
    if monthly is None:
        monthly = str(row.get("growth_period") or "annual") == "monthly"
    if monthly:
        base = f"  {sym}: median_monthly_growth={med:g}%"
        leg_label = "trailing months"
    else:
        base = f"  {sym}: median_annual_growth={med:g}%"
        leg_label = "trailing years"
    if rates:
        legs = ", ".join(f"{r:+.2f}%" for r in rates)
        suffix = f" ({periods_used}mo)" if monthly and periods_used else ""
        if not monthly and periods_used:
            suffix = f" ({periods_used}y)"
        return f"{base}{suffix} — {leg_label}: {legs}"
    return base


def _correlation_bucket_map(
    snapshots: List[Dict[str, Any]],
    *,
    benchmark: str | None,
    treasury: bool,
) -> Dict[str, Any]:
    by_bucket: dict[str, list[tuple[str, float]]] = {
        label: [] for label in CORRELATION_BUCKET_ORDER
    }
    missing: list[str] = []
    skipped_benchmark: list[str] = []
    for snap in snapshots:
        sym = str(snap.get("symbol") or "")
        if treasury and sym.upper() in {TREASURY_10Y_TICKER.upper(), "^TNX"}:
            continue
        if benchmark and sym.upper() == benchmark.upper():
            skipped_benchmark.append(sym)
            continue
        corr = stability_corr(snap, benchmark=benchmark, treasury=treasury)
        if corr is None:
            missing.append(sym)
            continue
        by_bucket[correlation_bucket_label(corr)].append((sym, corr))
    return {
        "buckets": {
            label: [{"symbol": s, "correlation": c} for s, c in sorted(rows, key=lambda x: x[1])]
            for label, rows in by_bucket.items()
        },
        "missing": sorted(missing),
        "skipped_benchmark": skipped_benchmark,
        "n_classified": sum(len(v) for v in by_bucket.values()),
    }


def _header(title: str) -> List[str]:
    return ["", "=" * len(title), title, "=" * len(title)]


def format_summaries_text(
    summaries: Dict[str, Any],
    *,
    bollinger_band_pct: float = 5.0,
    calmer_than_spy_max_ratio: float = 0.9,
) -> str:
    lines: List[str] = []

    def section(title: str, body: List[str]) -> None:
        lines.extend(_header(title))
        lines.extend(body)

    ob = summaries.get("overbought") or []
    if not ob:
        section(
            "Summary: overbought",
            ["  (none) — no symbol had RSI(14) ≥70 (insufficient history counts as none)."],
        )
    else:
        section(
            "Summary: overbought",
            [f"  {r['symbol']}: " + "; ".join(r["notes"]) for r in ob],
        )

    os_rows = summaries.get("oversold") or []
    if not os_rows:
        section(
            "Summary: oversold",
            ["  (none) — no symbol had RSI(14) ≤30 (insufficient history counts as none)."],
        )
    else:
        section(
            "Summary: oversold",
            [f"  {r['symbol']}: " + "; ".join(r["notes"]) for r in os_rows],
        )

    floor = 100.0 - bollinger_band_pct
    bu = summaries.get("bollinger_near_upper") or []
    section(
        f"Summary: within {bollinger_band_pct:g}% of upper Bollinger band (%B ≥ {floor:g})",
        (
            [f"  {r['symbol']}: %B={r['position_pct']:.2f}" for r in bu]
            if bu
            else [
                f"  (none) — no symbol had Bollinger %B ≥ {floor:g} "
                "(need price history for 20-period bands)."
            ]
        ),
    )

    bl = summaries.get("bollinger_near_lower") or []
    section(
        f"Summary: within {bollinger_band_pct:g}% of lower Bollinger band (%B ≤ {bollinger_band_pct:g})",
        (
            [f"  {r['symbol']}: %B={r['position_pct']:.2f}" for r in bl]
            if bl
            else [
                f"  (none) — no symbol had Bollinger %B ≤ {bollinger_band_pct:g} "
                "(need price history for 20-period bands)."
            ]
        ),
    )

    sa = summaries.get("sma_all_above") or []
    section(
        "Summary: close above all SMAs",
        (
            [f"  {s}" for s in sa]
            if sa
            else [
                f"  (none) — no symbol had valid SMA({', '.join(map(str, SMA_PERIODS))}) "
                "with close strictly above every SMA."
            ]
        ),
    )

    sb = summaries.get("sma_all_below") or []
    section(
        "Summary: close below all SMAs",
        (
            [f"  {s}" for s in sb]
            if sb
            else [
                f"  (none) — no symbol had valid SMA({', '.join(map(str, SMA_PERIODS))}) "
                "with close strictly below every SMA."
            ]
        ),
    )

    sm = summaries.get("sma_mixed") or []
    section(
        "Summary: close above some SMAs and below others",
        (
            [
                f"  {r['symbol']}: above SMA({','.join(map(str, r['above']))}); "
                f"below SMA({','.join(map(str, r['below']))})"
                for r in sm
            ]
            if sm
            else [
                "  (none) — no symbol had all SMA periods computed with at least one close>SMA "
                "and at least one close<SMA."
            ]
        ),
    )

    mb = summaries.get("macd_buy") or []
    section(
        "Summary: MACD buy signal (bullish)",
        (
            [
                f"  {r['symbol']}: histogram={r.get('histogram')} macd={r.get('macd_line')} "
                f"signal={r.get('signal_line')}"
                for r in mb
            ]
            if mb
            else ["  (none) — no symbol had MACD.signal == bullish (or insufficient history)."]
        ),
    )

    ms = summaries.get("macd_sell") or []
    section(
        "Summary: MACD sell signal (bearish)",
        (
            [
                f"  {r['symbol']}: histogram={r.get('histogram')} macd={r.get('macd_line')} "
                f"signal={r.get('signal_line')}"
                for r in ms
            ]
            if ms
            else ["  (none) — no symbol had MACD.signal == bearish (or insufficient history)."]
        ),
    )

    ps = summaries.get("price_stability") or []
    section(
        "Summary: price stability (annualized volatility, low to high)",
        (
            [
                f"  {r['symbol']}: ann_vol={r['ann_volatility_pct']}% "
                f"max_drawdown={r.get('max_drawdown_pct')}%"
                + (f" — {r['stability_note']}" if r.get("stability_note") else "")
                for r in ps
            ]
            if ps
            else ["  (none) — need at least ~60 trading days of history per symbol."]
        ),
    )

    all_growth = summaries.get("median_price_growth") or []
    n_annual = len(summaries.get("median_price_growth_annual") or [])
    n_monthly = len(summaries.get("median_price_growth_monthly") or [])
    missing_growth = summaries.get("median_price_growth_missing") or []
    growth_body: List[str] = []
    if all_growth:
        growth_body.extend(_format_median_growth_line(r) for r in all_growth)
        growth_body.append(
            f"  ({n_annual} annual, {n_monthly} monthly — "
            "monthly = < 1 year of history)"
        )
    else:
        growth_body.append(
            "  (none) — no symbol had enough price history for a growth median."
        )
    if missing_growth:
        growth_body.append(
            f"  (no growth median — insufficient history): {', '.join(missing_growth)}"
        )
    section(
        "Summary: median price growth (annual + monthly, high to low)",
        growth_body,
    )

    cts = summaries.get("calmer_than_spy") or []
    section(
        f"Summary: calmer than SPY (vol ratio ≤ {calmer_than_spy_max_ratio:g})",
        (
            [f"  {r['symbol']}: vol_ratio_vs_SPY={r['vol_ratio_vs_SPY']}" for r in cts]
            if cts
            else [
                f"  (none) — no symbol had ann_vol/SPY_vol ≤ {calmer_than_spy_max_ratio:g} "
                "over the window."
            ]
        ),
    )

    ctq = summaries.get("calmer_than_qqq") or []
    section(
        f"Summary: calmer than QQQ (vol ratio ≤ {calmer_than_spy_max_ratio:g})",
        (
            [f"  {r['symbol']}: vol_ratio_vs_QQQ={r['vol_ratio_vs_QQQ']}" for r in ctq]
            if ctq
            else [
                f"  (none) — no symbol had ann_vol/QQQ_vol ≤ {calmer_than_spy_max_ratio:g} "
                "over the window."
            ]
        ),
    )

    meta = summaries.get("_meta") or {}
    n_syms = int(meta.get("symbol_count") or 0)
    corr_root = summaries.get("correlations") or {}
    for bench in DEFAULT_EQUITY_BENCHMARKS:
        lines.extend(
            _format_correlation_section(
                corr_root.get(bench) or {},
                title=f"Summary: correlation vs {bench} (daily returns)",
                n_total=n_syms,
            )
        )
    lines.extend(
        _format_correlation_section(
            corr_root.get("10Y_Treasury") or {},
            title="Summary: correlation vs 10Y Treasury yield changes",
            n_total=n_syms,
        )
    )

    return "\n".join(lines).strip() + "\n"


def _format_correlation_section(
    block: Dict[str, Any],
    *,
    title: str,
    n_total: int,
) -> List[str]:
    out = _header(title)
    buckets = block.get("buckets") or {}
    n_classified = block.get("n_classified", 0)
    skipped = block.get("skipped_benchmark") or []
    total_note = f" of {n_total} analyzed" if n_total else ""
    out.append(
        f"  ({n_classified} symbol(s) with correlation{total_note}; "
        f"benchmark self-match skipped: {len(skipped)})"
    )
    any_listed = False
    for label in CORRELATION_BUCKET_ORDER:
        rows = buckets.get(label) or []
        if not rows:
            out.append(f"  {label}: (none)")
            continue
        any_listed = True
        parts = [f"{r['symbol']} ({r['correlation']:+.2f})" for r in rows]
        out.append(f"  {label}: " + ", ".join(parts))
    if not any_listed and not block.get("missing"):
        out.append("  (none) — no symbols with correlation data for this comparison.")
    missing = block.get("missing") or []
    if missing:
        out.append(f"  (no correlation — insufficient overlap): {', '.join(missing)}")
    return out


def format_symbol_detail_lines(snap: Dict[str, Any]) -> List[str]:
    """Human-readable per-symbol block (no SEC metric dump)."""
    sym = str(snap.get("symbol") or "")
    lines = _header(sym)
    bars = snap.get("bars", 0)
    if not bars:
        lines.append("price_history: (empty — check ticker / market data)")
    else:
        lines.append(f"price_history: {bars} bars")
    sma = snap.get("sma")
    if isinstance(sma, dict):
        for p in SMA_PERIODS:
            row = sma.get(str(p))
            if row is not None:
                lines.append(f"  SMA({p}): {row}")
    ema = snap.get("ema")
    if isinstance(ema, dict):
        for p in EMA_PERIODS:
            row = ema.get(str(p))
            if row is not None:
                lines.append(f"  EMA({p}): {row}")
    for key, label in (("rsi14", "RSI(14)"), ("macd", "MACD"), ("bollinger", "Bollinger")):
        val = snap.get(key)
        if val is not None:
            lines.append(f"  {label}: {val}")
    ps = snap.get("price_stability")
    if isinstance(ps, dict) and not ps.get("insufficient_history"):
        vol = ps.get("ann_volatility_pct")
        if vol is not None:
            lines.append(f"  ann_volatility_pct: {vol}")
        mdd = ps.get("max_drawdown_pct")
        if mdd is not None:
            lines.append(f"  max_drawdown_pct: {mdd}")
    pg = snap.get("price_growth")
    if isinstance(pg, dict) and pg.get("median_growth_pct") is not None:
        med = pg.get("median_growth_pct")
        rates = pg.get("growth_rates_pct") or []
        legs = ", ".join(f"{r:+.2f}%" for r in rates) if rates else "n/a"
        if pg.get("growth_period") == "monthly":
            n = pg.get("months_used") or pg.get("periods_used") or 0
            lines.append(
                f"  median_monthly_price_growth: {med}% "
                f"[{n} trailing month(s): {legs}]"
            )
        else:
            n = pg.get("years_used") or pg.get("periods_used") or 0
            lines.append(
                f"  median_annual_price_growth (5y): {med}% "
                f"[{n} trailing year(s): {legs}]"
            )
    sec = snap.get("sec")
    if isinstance(sec, dict):
        lines.append(
            f"  SecCompanyFundamentals: ticker={snap.get('symbol')} cik={sec.get('cik')} "
            f"error={sec.get('http_error')}"
        )
    growth = snap.get("growth")
    if isinstance(growth, dict):
        lines.append(f"  FundamentalGrowth: entity={growth.get('entity')!r}")
        for m in growth.get("metrics") or []:
            if not isinstance(m, dict):
                continue
            lines.append(
                f"    {m.get('metric')}: latest={m.get('latest_value')} "
                f"({m.get('latest_fp')} FY{m.get('latest_fy')} end={m.get('latest_end')}) "
                f"QoQ%={m.get('qoq_pct')} YoY%={m.get('yoy_pct')} note={m.get('note')!r}"
            )
    return lines
