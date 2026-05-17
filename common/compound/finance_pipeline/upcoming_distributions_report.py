"""Deterministic report formatting and cache helpers for the upcoming-distributions agent."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any


def _fmt_amount(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "$0.00"


def _fmt_confidence(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return "0.00%"


def _parse_row_payout_date(row: dict[str, Any]) -> date | None:
    raw = str(row.get("date") or row.get("next_projected_distribution_date") or "").strip()
    if not raw:
        return None
    head = raw[:10]
    try:
        return datetime.strptime(head, "%Y-%m-%d").date()
    except ValueError:
        return None


def confidence_from_row(row: dict[str, Any]) -> float:
    top = row.get("confidence_score")
    if top is not None:
        try:
            return float(top)
        except Exception:
            pass
    nested = (row.get("signal") or {}).get("confidence_score")
    try:
        return float(nested or 0.0)
    except Exception:
        return 0.0


def _rows_in_inclusive_date_range(
    rows: list[dict[str, Any]], *, lo: date, hi: date
) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    for row in rows:
        d = _parse_row_payout_date(row)
        if d is None:
            continue
        if lo <= d <= hi:
            picked.append(row)
    picked.sort(
        key=lambda r: (
            _parse_row_payout_date(r) or lo,
            str(r.get("symbol") or ""),
        )
    )
    return picked


def _append_payout_table_section(
    lines: list[str],
    *,
    heading: str,
    subrows: list[dict[str, Any]],
) -> float:
    lines.extend(
        [
            "",
            heading,
            "",
            "| Date | Symbol | Amount | Frequency | Confidence |",
            "|------|--------|--------|-----------|------------|",
        ]
    )
    total_amount = 0.0
    if not subrows:
        lines.append("| - | - | $0.00 | - | 0.00% |")
        return total_amount
    for row in subrows:
        try:
            total_amount += float(row.get("next_projected_distribution_amount") or 0.0)
        except Exception:
            pass
        lines.append(
            f"| {row.get('date') or row.get('next_projected_distribution_date') or '-'} "
            f"| {row.get('symbol') or '-'} "
            f"| {_fmt_amount(row.get('next_projected_distribution_amount'))} "
            f"| {row.get('payout_frequency') or '-'} "
            f"| {_fmt_confidence(confidence_from_row(row))} |"
        )
    return total_amount


def render_near_term_table(analysis: dict[str, Any], *, days: int = 10) -> str:
    rows = list(analysis.get("near_term_rows") or analysis.get("near_term_next_10_days") or [])
    rows.sort(
        key=lambda row: (
            str(row.get("date") or row.get("next_projected_distribution_date") or "9999-12-31"),
            str(row.get("symbol") or ""),
        )
    )
    horizon_days = int(analysis.get("horizon_days") or days)
    lines = [
        "## Key Findings",
        "",
        f"### Nearest Payout Dates (Next {horizon_days} Days)",
        "| Date | Symbol | Amount | Frequency | Confidence |",
        "|------|--------|--------|-----------|------------|",
    ]
    if not rows:
        lines.append("| - | - | $0.00 | - | 0.00% |")
        lines.append("")
        lines.append(f"**Total Amount (Next {horizon_days} Days): $0.00**")
    else:
        total_amount = 0.0
        for row in rows:
            try:
                total_amount += float(row.get("next_projected_distribution_amount") or 0.0)
            except Exception:
                pass
            lines.append(
                f"| {row.get('date') or row.get('next_projected_distribution_date') or '-'} "
                f"| {row.get('symbol') or '-'} "
                f"| {_fmt_amount(row.get('next_projected_distribution_amount'))} "
                f"| {row.get('payout_frequency') or '-'} "
                f"| {_fmt_confidence(confidence_from_row(row))} |"
            )
        lines.append("")
        lines.append(f"**Total Amount (Next {horizon_days} Days): {_fmt_amount(total_amount)}**")

    if horizon_days > 60:
        raw_as_of = analysis.get("as_of_date")
        if raw_as_of:
            try:
                as_of = datetime.strptime(str(raw_as_of).strip()[:10], "%Y-%m-%d").date()
            except ValueError:
                as_of = None
            if as_of is not None:
                first_end = as_of + timedelta(days=30)
                last_start = as_of + timedelta(days=max(0, horizon_days - 30))
                horizon_end = as_of + timedelta(days=horizon_days)
                first_rows = _rows_in_inclusive_date_range(rows, lo=as_of, hi=first_end)
                last_rows = _rows_in_inclusive_date_range(rows, lo=last_start, hi=horizon_end)
                total_first = _append_payout_table_section(
                    lines,
                    heading=(
                        f"### First 30 Days of Horizon ({as_of.isoformat()} … {first_end.isoformat()})"
                    ),
                    subrows=first_rows,
                )
                lines.append("")
                lines.append(f"**Total Amount (First 30 Days): {_fmt_amount(total_first)}**")
                total_last = _append_payout_table_section(
                    lines,
                    heading=(
                        f"### Last 30 Days of Horizon ({last_start.isoformat()} … {horizon_end.isoformat()})"
                    ),
                    subrows=last_rows,
                )
                lines.append("")
                lines.append(f"**Total Amount (Last 30 Days): {_fmt_amount(total_last)}**")

    return "\n".join(lines)


def _symbol_rows_for_target_options(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows with symbol, shares, and horizon distribution total (prefer full results)."""
    raw = analysis.get("results")
    if isinstance(raw, list) and raw:
        rows = [r for r in raw if isinstance(r, dict) and not r.get("error")]
    else:
        rows = [r for r in (analysis.get("top_by_projected_total") or []) if isinstance(r, dict)]
    return rows


def _per_share_horizon_dollars(row: dict[str, Any]) -> float:
    sh = int(row.get("shares") or 0)
    if sh <= 0:
        return 0.0
    tot = float(row.get("projected_distribution_total_horizon") or 0.0)
    return tot / float(sh)


def _base_shares_by_symbol(analysis: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in _symbol_rows_for_target_options(analysis):
        sym = str(r.get("symbol") or "").strip().upper()
        if sym:
            out[sym] = int(r.get("shares") or 0)
    return out


def _last_30_horizon_bounds(analysis: dict[str, Any]) -> tuple[date, date] | None:
    """Inclusive [lo, hi] for the last 30 days inside the horizon (same window as the near-term report)."""
    raw_as_of = analysis.get("as_of_date")
    if not raw_as_of:
        return None
    try:
        as_of = datetime.strptime(str(raw_as_of).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    horizon_days = max(0, int(analysis.get("horizon_days") or 0))
    last_start = as_of + timedelta(days=max(0, horizon_days - 30))
    horizon_end = as_of + timedelta(days=horizon_days)
    return last_start, horizon_end


def _last_30_distribution_total_modelled(
    analysis: dict[str, Any], shares_map: dict[str, int]
) -> float | None:
    """Sum of payout rows in the last-30-days-of-horizon window, scaled by share counts in ``shares_map``."""
    rows = list(analysis.get("near_term_rows") or analysis.get("near_term_next_10_days") or [])
    if not rows:
        return None
    bounds = _last_30_horizon_bounds(analysis)
    if bounds is None:
        return None
    lo, hi = bounds
    filtered = _rows_in_inclusive_date_range(rows, lo=lo, hi=hi)
    base_from_results = _base_shares_by_symbol(analysis)
    total = 0.0
    for row in filtered:
        sym = str(row.get("symbol") or "").strip().upper()
        if not sym:
            continue
        base_row_sh = int(row.get("shares") or 0)
        base = base_row_sh or int(base_from_results.get(sym, 0) or 0)
        if base <= 0:
            continue
        newc = max(0, int(shares_map.get(sym, 0)))
        amt = float(row.get("next_projected_distribution_amount") or 0.0)
        total += amt * (float(newc) / float(base))
    return round(total, 2)


def _spot_prices_for_symbols(symbols: list[str]) -> dict[str, float]:
    """Best-effort spot prices for rotation math (yfinance)."""
    try:
        from common.simple.yfinance_warnings import suppress_utcnow_deprecation_warning

        suppress_utcnow_deprecation_warning()
    except Exception:
        pass
    from common.compound.finance_pipeline.drip_forecast import get_current_price

    out: dict[str, float] = {}
    for raw in symbols:
        sym = str(raw or "").strip().upper()
        if not sym:
            continue
        px = get_current_price(sym)
        if px is not None and float(px) > 0:
            out[sym] = float(px)
    return out


def _shortfall_rotation(
    src: dict[str, Any],
    dst: dict[str, Any],
    gap: float,
    prices: dict[str, float],
) -> tuple[int, int, float, float] | None:
    """Sell src shares, buy dst with proceeds; approximate horizon distribution lift (linear model)."""
    if gap <= 0:
        return None
    sf = str(src.get("symbol") or "").strip().upper()
    st = str(dst.get("symbol") or "").strip().upper()
    if sf == st:
        return None
    pf = prices.get(sf)
    pt = prices.get(st)
    if pf is None or pt is None or pf <= 0 or pt <= 0:
        return None
    d_s = _per_share_horizon_dollars(src)
    d_t = _per_share_horizon_dollars(dst)
    per_sell_share = (pf / pt) * d_t - d_s
    if per_sell_share <= 1e-9:
        return None
    max_n = int(src.get("shares") or 0)
    if max_n <= 0:
        return None
    n = int(math.ceil(gap / per_sell_share - 1e-9))
    n = max(1, min(n, max_n))
    m = int(math.floor(n * pf / pt + 1e-9))
    if m < 1:
        return None
    est = float(n) * per_sell_share
    notional = float(n) * float(pf)
    return n, m, est, notional


def _surplus_rotation(
    high: dict[str, Any],
    low: dict[str, Any],
    surplus: float,
    prices: dict[str, float],
) -> tuple[int, int, float, float] | None:
    """Sell high payer, buy low payer with proceeds; approximate horizon distribution cut."""
    if surplus <= 0:
        return None
    sh = str(high.get("symbol") or "").strip().upper()
    sl = str(low.get("symbol") or "").strip().upper()
    if sh == sl:
        return None
    ph = prices.get(sh)
    pl = prices.get(sl)
    if ph is None or pl is None or ph <= 0 or pl <= 0:
        return None
    d_h = _per_share_horizon_dollars(high)
    d_l = _per_share_horizon_dollars(low)
    den = d_h - (ph / pl) * d_l
    if den <= 1e-9:
        return None
    max_n = int(high.get("shares") or 0)
    if max_n <= 0:
        return None
    n = int(math.ceil(surplus / den - 1e-9))
    n = max(1, min(n, max_n))
    m = int(math.floor(n * ph / pl + 1e-9))
    if m < 1:
        return None
    est_drop = float(n) * den
    notional = float(n) * float(ph)
    return n, m, est_drop, notional


def _shortfall_pair_blocked_detail(
    src: dict[str, Any],
    dst: dict[str, Any],
    prices: dict[str, float],
    *,
    gap: float,
) -> str:
    """Short reason when _shortfall_rotation returns None (for UI)."""
    sf = str(src.get("symbol") or "").strip().upper()
    st = str(dst.get("symbol") or "").strip().upper()
    if sf == st:
        return "same symbol."
    pf, pt = prices.get(sf), prices.get(st)
    if pf is None or pt is None or pf <= 0 or pt <= 0:
        return "missing spot price(s) for one or both legs."
    d_s = _per_share_horizon_dollars(src)
    d_t = _per_share_horizon_dollars(dst)
    per = (float(pf) / float(pt)) * d_t - d_s
    if per <= 1e-9:
        return (
            f"marginal modeled lift per sold **{sf}** share is **{_fmt_amount(per)}** "
            f"(need > $0; proceeds buy {(pf / pt):.4f}× **{st}** @ **{_fmt_amount(d_t)}**/sh vs **{_fmt_amount(d_s)}**/sh lost on **{sf}**)"
        )
    max_n = int(src.get("shares") or 0)
    if max_n <= 0:
        return "no source shares."
    n = max(1, min(int(math.ceil(gap / per - 1e-9)), max_n))
    m = int(math.floor(n * float(pf) / float(pt) + 1e-9))
    if m < 1:
        return (
            f"at ~{_fmt_px(float(pf))} vs ~{_fmt_px(float(pt))}, selling even **1** **{sf}** funds **<1** **{st}** share "
            f"for this **{_fmt_amount(gap)}** gap."
        )
    return "unknown sizing constraint."


def _surplus_pair_blocked_detail(
    high: dict[str, Any],
    low: dict[str, Any],
    prices: dict[str, float],
    *,
    surplus: float,
) -> str:
    sh = str(high.get("symbol") or "").strip().upper()
    sl = str(low.get("symbol") or "").strip().upper()
    if sh == sl:
        return "same symbol."
    ph, pl = prices.get(sh), prices.get(sl)
    if ph is None or pl is None or ph <= 0 or pl <= 0:
        return "missing spot price(s) for one or both legs."
    d_h = _per_share_horizon_dollars(high)
    d_l = _per_share_horizon_dollars(low)
    den = d_h - (float(ph) / float(pl)) * d_l
    if den <= 1e-9:
        return (
            f"marginal modeled cut per sold **{sh}** share is **{_fmt_amount(den)}** "
            f"(need > $0; buy **{sl}** adds **{_fmt_amount((ph / pl) * d_l)}**/sh vs **{_fmt_amount(d_h)}**/sh lost on **{sh}**)"
        )
    max_n = int(high.get("shares") or 0)
    if max_n <= 0:
        return "no high-payer shares to sell."
    n = max(1, min(int(math.ceil(surplus / den - 1e-9)), max_n))
    m = int(math.floor(n * float(ph) / float(pl) + 1e-9))
    if m < 1:
        return (
            f"at ~{_fmt_px(float(ph))} vs ~{_fmt_px(float(pl))}, selling **1** **{sh}** funds **<1** **{sl}** share."
        )
    return "unknown sizing constraint."


def _midpack_shortfall_rank_pairs(i_lo: int, i_hi: int, n_sym: int) -> list[tuple[int, int]]:
    """(src_idx, dst_idx) with src_idx > dst_idx: sell worse-ranked line, buy better-ranked."""
    seeds = [
        (i_lo, i_hi),
        (i_lo + 1, i_hi),
        (i_lo, i_hi - 1),
        (i_lo + 1, i_hi - 1),
        (i_lo + 2, i_hi),
        (i_lo, i_hi - 2),
        (i_lo - 1, i_hi),
        (i_lo, i_hi + 1),
    ]
    out: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for a, b in seeds:
        if not (0 <= a < n_sym and 0 <= b < n_sym):
            continue
        if a <= b:
            continue
        if (a, b) in seen:
            continue
        seen.add((a, b))
        out.append((a, b))
    return out


def _midpack_surplus_rank_pairs(i_hi: int, i_lo: int, n_sym: int) -> list[tuple[int, int]]:
    """(high_idx, low_idx) with high_idx < low_idx: sell better payer, buy worse-ranked."""
    seeds = [
        (i_hi, i_lo),
        (i_hi + 1, i_lo),
        (i_hi, i_lo - 1),
        (i_hi + 1, i_lo - 1),
        (i_hi - 1, i_lo),
        (i_hi, i_lo + 1),
    ]
    out: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for a, b in seeds:
        if not (0 <= a < n_sym and 0 <= b < n_sym):
            continue
        if a >= b:
            continue
        if (a, b) in seen:
            continue
        seen.add((a, b))
        out.append((a, b))
    return out


def _fmt_px(px: float) -> str:
    try:
        return f"${float(px):,.2f}"
    except Exception:
        return "$0.00"


def _leg_line_sell_buy(
    sell_sym: str,
    n: int,
    px_s: float,
    buy_sym: str,
    m: int,
    px_b: float,
    notional: float,
    est_effect: float,
    *,
    effect_label: str,
    analysis: dict[str, Any],
    base_shares: dict[str, int],
) -> str:
    su = str(sell_sym or "").strip().upper()
    bu = str(buy_sym or "").strip().upper()
    last30_clause = ""
    cur30 = _last_30_distribution_total_modelled(analysis, base_shares)
    if cur30 is not None:
        adj = dict(base_shares)
        adj[su] = max(0, int(adj.get(su, 0)) - int(n))
        adj[bu] = int(adj.get(bu, 0)) + int(m)
        after30 = _last_30_distribution_total_modelled(analysis, adj)
        if after30 is not None:
            last30_clause = (
                f" **Last 30 days of horizon (modeled):** {_fmt_amount(cur30)} → **{_fmt_amount(after30)}** "
                f"if this recommendation were applied (scales each payout in the report’s last-30-day window "
                f"by share count)."
            )
    return (
        f"- **Sell {n:,} {sell_sym}** @ ~{_fmt_px(px_s)} (~{_fmt_amount(notional)} notional) → "
        f"**buy {m:,} {buy_sym}** @ ~{_fmt_px(px_b)}. "
        f"Approx. horizon distribution {effect_label}: **{_fmt_amount(est_effect)}** "
        f"(linear per-share model; ignores taxes, slippage, and future payout changes)."
        f"{last30_clause}"
    )


def _missing_px_note(symbols: list[str], prices: dict[str, float]) -> str:
    missing = [s for s in symbols if s not in prices or prices[s] <= 0]
    if not missing:
        return ""
    return " _(Could not load a spot price for: " + ", ".join(missing) + ".)_"


def _concrete_shortfall_options(
    ranked: list[dict[str, Any]],
    gap: float,
    prices: dict[str, float],
    analysis: dict[str, Any],
) -> str:
    lines: list[str] = []
    n_sym = len(ranked)
    if n_sym < 2 or gap <= 0:
        return ""

    base_shares = _base_shares_by_symbol(analysis)

    lines.extend(
        [
            "",
            "### Options B–F — concrete rotations (same-dollar, no new capital)",
            "",
            "Each line is a **two-leg swap**: proceeds from the sale fund the buy so **cash in ≈ cash "
            "out**. Share counts use **spot prices from yfinance** right now; distribution effect uses "
            "this report’s **horizon $ per share** (projected horizon total ÷ shares). "
            "**Last 30 days of horizon** figures scale payout rows in that window when share counts change.",
            "",
        ]
    )

    # B — worst → best
    lines.append("#### B — Worst → best (single pair)")
    b = _shortfall_rotation(ranked[-1], ranked[0], gap, prices)
    if b:
        n, m, est, notional = b
        lines.append(
            _leg_line_sell_buy(
                str(ranked[-1].get("symbol")),
                n,
                prices[str(ranked[-1].get("symbol")).strip().upper()],
                str(ranked[0].get("symbol")),
                m,
                prices[str(ranked[0].get("symbol")).strip().upper()],
                notional,
                est,
                effect_label="lift vs current model",
                analysis=analysis,
                base_shares=base_shares,
            )
        )
    else:
        lines.append(
            f"- Could not size **{ranked[-1].get('symbol')} → {ranked[0].get('symbol')}** for "
            f"a **{_fmt_amount(gap)}** lift (pair does not improve payouts in this model, or prices missing)."
            f"{_missing_px_note([str(ranked[-1].get('symbol')), str(ranked[0].get('symbol'))], prices)}"
        )

    # C — bottom 3 → top 3 distinct targets
    if n_sym >= 3:
        lines.extend(["", "#### C — Bottom three → top three (split gap in thirds)"])
        third = gap / 3.0
        pairs = [(-3, 2), (-2, 1), (-1, 0)]
        for si, di in pairs:
            src, dst = ranked[si], ranked[di]
            if str(src.get("symbol")) == str(dst.get("symbol")):
                continue
            c = _shortfall_rotation(src, dst, third, prices)
            if c:
                n, m, est, notional = c
                lines.append(
                    _leg_line_sell_buy(
                        str(src.get("symbol")),
                        n,
                        prices[str(src.get("symbol")).strip().upper()],
                        str(dst.get("symbol")),
                        m,
                        prices[str(dst.get("symbol")).strip().upper()],
                        notional,
                        est,
                        effect_label="lift (⅓ of gap slice)",
                        analysis=analysis,
                        base_shares=base_shares,
                    )
                )
            else:
                lines.append(
                    f"- Slice **{_fmt_amount(third)}**: could not size **{src.get('symbol')} → {dst.get('symbol')}**."
                    f"{_missing_px_note([str(src.get('symbol')), str(dst.get('symbol'))], prices)}"
                )

    # D — bottom half → best (each slice equal)
    lines.extend(["", "#### D — Many small trims from the weak half → all into #1"])
    k = min(6, max(1, n_sym // 2))
    sources = ranked[-k:]
    slice_gap = gap / float(k)
    any_d = False
    for src in sources:
        dst = ranked[0]
        if str(src.get("symbol")) == str(dst.get("symbol")):
            continue
        d = _shortfall_rotation(src, dst, slice_gap, prices)
        if d:
            n, m, est, notional = d
            any_d = True
            lines.append(
                _leg_line_sell_buy(
                    str(src.get("symbol")),
                    n,
                    prices[str(src.get("symbol")).strip().upper()],
                    str(dst.get("symbol")),
                    m,
                    prices[str(dst.get("symbol")).strip().upper()],
                    notional,
                    est,
                    effect_label="lift (weak-half slice)",
                    analysis=analysis,
                    base_shares=base_shares,
                )
            )
    if not any_d:
        lines.append("- No sized legs for this pattern (prices or payout spread blocked every pair).")

    # E — bottom two → top two (quarters)
    if n_sym >= 2:
        lines.extend(["", "#### E — Bottom two fund top two (quarters of the gap)"])
        q = gap / 4.0
        e_pairs = [(-2, 1), (-1, 0), (-2, 0), (-1, 1)]
        seen: set[tuple[str, str]] = set()
        for si, di in e_pairs:
            src, dst = ranked[si], ranked[di]
            key = (str(src.get("symbol")), str(dst.get("symbol")))
            if key in seen or key[0] == key[1]:
                continue
            seen.add(key)
            e = _shortfall_rotation(src, dst, q, prices)
            if e:
                n, m, est, notional = e
                lines.append(
                    _leg_line_sell_buy(
                        key[0],
                        n,
                        prices[key[0]],
                        key[1],
                        m,
                        prices[key[1]],
                        notional,
                        est,
                        effect_label="lift (¼ gap slice)",
                        analysis=analysis,
                        base_shares=base_shares,
                    )
                )

    # F — mid-pack tilt (upper third vs lower third)
    if n_sym >= 3:
        lines.extend(["", "#### F — Mid-pack tilt (lower third → upper third)"])
        i_hi = max(0, (n_sym - 1) // 3)
        i_lo = min(n_sym - 1, (2 * (n_sym - 1)) // 3)
        if i_hi != i_lo:
            primary = (i_lo, i_hi)
            tried = 0
            for lo_i, hi_i in _midpack_shortfall_rank_pairs(i_lo, i_hi, n_sym):
                tried += 1
                src, dst = ranked[lo_i], ranked[hi_i]
                f = _shortfall_rotation(src, dst, gap, prices)
                if not f:
                    continue
                n, m, est, notional = f
                if (lo_i, hi_i) != primary:
                    lines.append(
                        f"- _Sized using **{src.get('symbol')} → {dst.get('symbol')}** (ranks {lo_i + 1} sell → "
                        f"{hi_i + 1} buy) after primary **{ranked[primary[0]].get('symbol')} → "
                        f"{ranked[primary[1]].get('symbol')}** could not._"
                    )
                lines.append(
                    _leg_line_sell_buy(
                        str(src.get("symbol")),
                        n,
                        prices[str(src.get("symbol")).strip().upper()],
                        str(dst.get("symbol")),
                        m,
                        prices[str(dst.get("symbol")).strip().upper()],
                        notional,
                        est,
                        effect_label="lift (single mid-rank pair)",
                        analysis=analysis,
                        base_shares=base_shares,
                    )
                )
                break
            else:
                p_src, p_dst = ranked[primary[0]], ranked[primary[1]]
                lines.append(
                    f"- Could not size a mid-pack **{p_src.get('symbol')} → {p_dst.get('symbol')}** rotation "
                    f"for the full **{_fmt_amount(gap)}** gap ({_shortfall_pair_blocked_detail(p_src, p_dst, prices, gap=gap)}). "
                    f"Tried **{tried}** nearby rank pairs; none worked."
                    f"{_missing_px_note([str(p_src.get('symbol')), str(p_dst.get('symbol'))], prices)}"
                )

    return "\n".join(lines)


def _concrete_surplus_options(
    ranked: list[dict[str, Any]],
    surplus: float,
    prices: dict[str, float],
    analysis: dict[str, Any],
) -> str:
    """surplus = projected - target (positive); reduce distributions."""
    lines: list[str] = []
    n_sym = len(ranked)
    if n_sym < 2 or surplus <= 0:
        return ""

    base_shares = _base_shares_by_symbol(analysis)

    lines.extend(
        [
            "",
            "### Options B–F — concrete rotations (same-dollar, no new capital)",
            "",
            "Each line **cuts** horizon cash by selling a **stronger** payer and reinvesting into a **weaker** "
            "one at the same dollar amount. Spot prices from **yfinance**; effect size uses this report’s "
            "horizon $/share model. **Last 30 days of horizon** figures scale payout rows in that window when "
            "share counts change.",
            "",
        ]
    )

    lines.append("#### B — Best → worst (single pair)")
    b = _surplus_rotation(ranked[0], ranked[-1], surplus, prices)
    if b:
        n, m, est, notional = b
        lines.append(
            _leg_line_sell_buy(
                str(ranked[0].get("symbol")),
                n,
                prices[str(ranked[0].get("symbol")).strip().upper()],
                str(ranked[-1].get("symbol")),
                m,
                prices[str(ranked[-1].get("symbol")).strip().upper()],
                notional,
                est,
                effect_label="reduction vs current model",
                analysis=analysis,
                base_shares=base_shares,
            )
        )
    else:
        lines.append(
            f"- Could not size **{ranked[0].get('symbol')} → {ranked[-1].get('symbol')}** for a "
            f"**{_fmt_amount(surplus)}** cut."
            f"{_missing_px_note([str(ranked[0].get('symbol')), str(ranked[-1].get('symbol'))], prices)}"
        )

    if n_sym >= 3:
        lines.extend(["", "#### C — Top three → bottom three (split surplus in thirds)"])
        third = surplus / 3.0
        pairs = [(0, -3), (1, -2), (2, -1)]
        for hi, lo in pairs:
            high, low = ranked[hi], ranked[lo]
            if str(high.get("symbol")) == str(low.get("symbol")):
                continue
            c = _surplus_rotation(high, low, third, prices)
            if c:
                n, m, est, notional = c
                lines.append(
                    _leg_line_sell_buy(
                        str(high.get("symbol")),
                        n,
                        prices[str(high.get("symbol")).strip().upper()],
                        str(low.get("symbol")),
                        m,
                        prices[str(low.get("symbol")).strip().upper()],
                        notional,
                        est,
                        effect_label="reduction (⅓ of surplus slice)",
                        analysis=analysis,
                        base_shares=base_shares,
                    )
                )
            else:
                lines.append(
                    f"- Slice **{_fmt_amount(third)}**: could not size **{high.get('symbol')} → {low.get('symbol')}**."
                    f"{_missing_px_note([str(high.get('symbol')), str(low.get('symbol'))], prices)}"
                )

    lines.extend(["", "#### D — Many small trims from the strong half → all into worst"])
    k = min(6, max(1, n_sym // 2))
    donors = ranked[:k]
    sink = ranked[-1]
    slice_s = surplus / float(k)
    any_d = False
    for high in donors:
        if str(high.get("symbol")) == str(sink.get("symbol")):
            continue
        d = _surplus_rotation(high, sink, slice_s, prices)
        if d:
            n, m, est, notional = d
            any_d = True
            lines.append(
                _leg_line_sell_buy(
                    str(high.get("symbol")),
                    n,
                    prices[str(high.get("symbol")).strip().upper()],
                    str(sink.get("symbol")),
                    m,
                    prices[str(sink.get("symbol")).strip().upper()],
                    notional,
                    est,
                    effect_label="reduction (strong-half slice)",
                    analysis=analysis,
                    base_shares=base_shares,
                )
            )
    if not any_d:
        lines.append("- No sized legs for this pattern.")

    if n_sym >= 2:
        lines.extend(["", "#### E — Top two → bottom two (quarters of the surplus)"])
        q = surplus / 4.0
        e_pairs = [(1, -2), (0, -1), (1, -1), (0, -2)]
        seen: set[tuple[str, str]] = set()
        for hi, lo in e_pairs:
            high, low = ranked[hi], ranked[lo]
            key = (str(high.get("symbol")), str(low.get("symbol")))
            if key in seen or key[0] == key[1]:
                continue
            seen.add(key)
            e = _surplus_rotation(high, low, q, prices)
            if e:
                n, m, est, notional = e
                lines.append(
                    _leg_line_sell_buy(
                        key[0],
                        n,
                        prices[key[0]],
                        key[1],
                        m,
                        prices[key[1]],
                        notional,
                        est,
                        effect_label="reduction (¼ surplus slice)",
                        analysis=analysis,
                        base_shares=base_shares,
                    )
                )

    if n_sym >= 3:
        lines.extend(["", "#### F — Mid-pack tilt (upper third → lower third)"])
        i_hi = max(0, (n_sym - 1) // 3)
        i_lo = min(n_sym - 1, (2 * (n_sym - 1)) // 3)
        if i_hi != i_lo:
            primary = (i_hi, i_lo)
            tried = 0
            for hi_i, lo_i in _midpack_surplus_rank_pairs(i_hi, i_lo, n_sym):
                tried += 1
                high, low = ranked[hi_i], ranked[lo_i]
                f = _surplus_rotation(high, low, surplus, prices)
                if not f:
                    continue
                n, m, est, notional = f
                if (hi_i, lo_i) != primary:
                    lines.append(
                        f"- _Sized using **{high.get('symbol')} → {low.get('symbol')}** (ranks {hi_i + 1} sell → "
                        f"{lo_i + 1} buy) after primary **{ranked[primary[0]].get('symbol')} → "
                        f"{ranked[primary[1]].get('symbol')}** could not._"
                    )
                lines.append(
                    _leg_line_sell_buy(
                        str(high.get("symbol")),
                        n,
                        prices[str(high.get("symbol")).strip().upper()],
                        str(low.get("symbol")),
                        m,
                        prices[str(low.get("symbol")).strip().upper()],
                        notional,
                        est,
                        effect_label="reduction (single mid-rank pair)",
                        analysis=analysis,
                        base_shares=base_shares,
                    )
                )
                break
            else:
                p_hi, p_lo = ranked[primary[0]], ranked[primary[1]]
                lines.append(
                    f"- Could not size a mid-pack **{p_hi.get('symbol')} → {p_lo.get('symbol')}** rotation "
                    f"for the full **{_fmt_amount(surplus)}** surplus ({_surplus_pair_blocked_detail(p_hi, p_lo, prices, surplus=surplus)}). "
                    f"Tried **{tried}** nearby rank pairs; none worked."
                    f"{_missing_px_note([str(p_hi.get('symbol')), str(p_lo.get('symbol'))], prices)}"
                )

    return "\n".join(lines)


def render_distribution_target_options(analysis: dict[str, Any], *, target: float) -> str:
    """Markdown comparing a target horizon distribution total to projections and suggesting trades."""
    if target <= 0:
        return ""
    projected = float(analysis.get("total_projected_distributions_horizon") or 0.0)
    horizon_days = int(analysis.get("horizon_days") or 0)
    as_of = str(analysis.get("as_of_date") or "").strip()
    gap = round(target - projected, 2)
    lines: list[str] = [
        "",
        "## Target vs projected (full horizon)",
        "",
        f"- **Target total distributions by horizon end:** {_fmt_amount(target)}",
        f"- **Projected total (current positions):** {_fmt_amount(projected)}",
        f"- **Gap (target − projected):** {_fmt_amount(gap)}",
    ]
    if as_of:
        lines.append(f"- **As-of:** {as_of}" + (f" · **Horizon:** {horizon_days} days" if horizon_days else ""))
    elif horizon_days:
        lines.append(f"- **Horizon:** {horizon_days} days")

    rows = _symbol_rows_for_target_options(analysis)
    usable = [
        r
        for r in rows
        if int(r.get("shares") or 0) > 0
        and float(r.get("projected_distribution_total_horizon") or 0.0) > 0
    ]

    if projected <= 0:
        lines.extend(
            [
                "",
                "No positive projected horizon total was available, so scaling options cannot be computed.",
            ]
        )
        return "\n".join(lines)

    ranked = sorted(usable, key=_per_share_horizon_dollars, reverse=True)
    lines.extend(
        [
            "",
            "### Ranking (for context)",
            "",
            "Sorted by **approximate horizon distribution dollars per share** (projected horizon total ÷ "
            "shares). Rank **1** = most cash per share over the horizon in this model.",
            "",
            "| Rank | Symbol | Shares | Horizon $ total | ~$/share (horizon) |",
            "|-----:|--------|-------:|----------------:|------------------:|",
        ]
    )
    if not ranked:
        lines.append("| - | - | - | - | - |")
    else:
        for i, r in enumerate(ranked, start=1):
            sym = str(r.get("symbol") or "-")
            sh = int(r.get("shares") or 0)
            tot = float(r.get("projected_distribution_total_horizon") or 0.0)
            ps = _per_share_horizon_dollars(r)
            lines.append(f"| {i} | {sym} | {sh:,} | {_fmt_amount(tot)} | {_fmt_amount(ps)} |")

    if len(ranked) < 2:
        if gap == 0:
            lines.extend(["", "**Projected horizon total already matches the target.**"])
        else:
            lines.append(
                "\n\n**Cannot propose rotations:** need at least **two** holdings with positive horizon "
                "distribution projections to build funded sell→buy pairs."
            )
        return "\n".join(lines)

    syms = [str(r.get("symbol") or "").strip().upper() for r in ranked if r.get("symbol")]
    prices = _spot_prices_for_symbols(syms)

    if gap > 0:
        lines.append(_concrete_shortfall_options(ranked, float(gap), prices, analysis))
    elif gap < 0:
        surplus_amt = round(projected - target, 2)
        lines.extend(
            [
                "",
                f"**Surplus vs target:** projected horizon cash is about **{_fmt_amount(surplus_amt)}** above "
                "the target before any trades.",
            ]
        )
        lines.append(_concrete_surplus_options(ranked, float(surplus_amt), prices, analysis))
    else:
        lines.extend(["", "**Projected horizon total already matches the target.**"])

    return "\n".join(lines)


def extract_cached_portfolio_analysis(
    observations: list[dict[str, Any]],
    *,
    window_days: int,
) -> dict[str, Any] | None:
    for obs in reversed(observations):
        if str(obs.get("type") or "") != "tool_result":
            continue
        if str(obs.get("tool") or "") != "analyze_portfolio_upcoming_distributions":
            continue
        result = obs.get("result")
        if isinstance(result, dict):
            cached_days = int(result.get("near_term_window_days") or 10)
            if cached_days != int(window_days):
                continue
            return result
    return None
