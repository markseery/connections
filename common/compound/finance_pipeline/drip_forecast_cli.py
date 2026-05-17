"""CLI: project price/value + distributions with optional DRIP reinvestment."""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from common.simple import script_env

from common.compound.finance_pipeline.drip_forecast import (
    DripForecastInputs,
    build_drip_forecast,
    infer_monthly_price_growth_rate,
)

import yaml


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def _load_positions_from_yaml(path: Path) -> list[tuple[str, float]]:
    """
    Minimal loader for configs like application_files/config/rh.symbols.yaml:

      positions:
        - { symbol: VOO, shares: 10 }
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("positions") or []
    if not isinstance(items, list):
        raise ValueError("positions must be a list")
    out: list[tuple[str, float]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").strip().upper()
        if not sym:
            continue
        try:
            shares = float(row.get("shares") or 0.0)
        except (TypeError, ValueError):
            shares = 0.0
        if shares <= 0:
            continue
        out.append((sym, shares))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Forecast future distribution periods with optional DRIP.")
    p.add_argument(
        "symbol",
        nargs="?",
        default=None,
        help="Ticker symbol (e.g. VOO). Omit when using --config.",
    )
    p.add_argument(
        "--shares",
        type=float,
        default=None,
        help="Initial number of shares (required unless using --config).",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Positions YAML (e.g. application_files/config/rh.symbols.yaml). When set, runs all symbols and prints an aggregate.",
    )
    p.add_argument(
        "--price-growth-rate",
        type=float,
        default=None,
        help="Per-period price growth rate (e.g. 0.01 == +1%% per period).",
    )
    p.add_argument(
        "--infer-monthly-growth-rate",
        action="store_true",
        help="Infer a likely monthly growth rate from history and use it as --price-growth-rate.",
    )
    p.add_argument(
        "--lookback-months",
        type=int,
        default=36,
        help="Lookback window for --infer-monthly-growth-rate (default: 36).",
    )
    p.add_argument(
        "--price-series",
        choices=["close", "adjclose"],
        default="adjclose",
        help="Which monthly price series to use for growth inference (default: adjclose).",
    )
    p.add_argument(
        "--drip-rate",
        type=float,
        default=1.0,
        help="Fraction of each distribution reinvested (0..1). Default 1.0 (full DRIP).",
    )
    p.add_argument(
        "--period-unit",
        choices=["month", "distribution"],
        default="month",
        help="What one 'period' means: month (default) or one distribution event.",
    )
    p.add_argument(
        "--monthly-event-mode",
        choices=["count", "expected"],
        default="count",
        help="When --period-unit=month: count=integer payouts in the calendar month (4 vs 5 for weekly). "
        "expected=smooth using days_in_month/cadence_days.",
    )
    p.add_argument(
        "--show-event-counts",
        action="store_true",
        help="Print the per-period event_count used (only meaningful for --period-unit=month).",
    )
    p.add_argument("--periods", type=int, required=True, help="Number of future distribution periods.")
    p.add_argument("--as-of", default=None, help="As-of date YYYY-MM-DD (default: today).")
    p.add_argument(
        "--print-per-symbol",
        action="store_true",
        help="When using --config, also print the per-symbol tables after the aggregate.",
    )
    p.add_argument(
        "--no-symbol-breakdown",
        action="store_true",
        help="When using --config, print ONLY aggregate rows (no per-period per-symbol lines).",
    )
    args = p.parse_args()

    as_of = _parse_date(args.as_of)
    periods = int(args.periods)

    def _infer_growth(sym: str) -> tuple[float, dict | None]:
        growth_rate = args.price_growth_rate
        inferred = None
        if args.infer_monthly_growth_rate:
            try:
                inferred = infer_monthly_price_growth_rate(
                    symbol=sym,
                    lookback_months=int(args.lookback_months),
                    price_series=str(args.price_series),
                )
                if growth_rate is None:
                    growth_rate = float(inferred.get("monthly_cagr") or 0.0)
            except Exception as exc:
                # Do not fail the whole run due to one symbol's missing/short history.
                print(f"[{sym}] growth inference failed: {exc} (falling back to 0.0)")
                inferred = None
                if growth_rate is None:
                    growth_rate = 0.0
        if growth_rate is None:
            growth_rate = 0.0
        return float(growth_rate), inferred

    def _print_table(title: str, rows: list[dict]) -> None:
        print(title)
        headers = ["period", "date", "price", "shares", "total_value", "distribution", "drip"]
        if args.show_event_counts:
            headers = headers + ["events"]
        period_w = max(len("period"), max((len(str(r.get("period") or "")) for r in rows), default=0))
        date_w = max(len("date"), 10)
        price_w = max(len("price"), max((len(f"{float(r.get('price') or 0.0):.4f}") for r in rows), default=0))
        shares_w = max(
            len("shares"),
            max((len(f"{float(r.get('shares') or 0.0):.6f}") for r in rows), default=0),
        )
        value_w = max(
            len("total_value"),
            max((len(f"{float(r.get('total_value') or 0.0):.2f}") for r in rows), default=0),
        )
        dist_w = max(
            len("distribution"),
            max((len(f"{float(r.get('distribution_amount') or 0.0):.2f}") for r in rows), default=0),
        )
        drip_w = max(
            len("drip"),
            max((len(f"{float(r.get('drip_amount') or 0.0):.2f}") for r in rows), default=0),
        )
        fmt_header = (
            f"{{:>{period_w}}}  {{:<{date_w}}}  {{:>{price_w}}}  {{:>{shares_w}}}  "
            f"{{:>{value_w}}}  {{:>{dist_w}}}  {{:>{drip_w}}}"
        )
        fmt_row = (
            f"{{:>{period_w}d}}  {{:<{date_w}}}  {{:>{price_w}.4f}}  {{:>{shares_w}.6f}}  "
            f"{{:>{value_w}.2f}}  {{:>{dist_w}.2f}}  {{:>{drip_w}.2f}}"
        )
        if args.show_event_counts:
            ev_vals = []
            for r in rows:
                v = r.get("event_count")
                if v is None:
                    ev_vals.append("")
                else:
                    try:
                        ev_vals.append(f"{float(v):.4f}")
                    except (TypeError, ValueError):
                        ev_vals.append("")
            ev_w = max(len("events"), max((len(s) for s in ev_vals), default=0))
            fmt_header = fmt_header + f"  {{:>{ev_w}}}"
            fmt_row = fmt_row + f"  {{:>{ev_w}}}"
        print(fmt_header.format(*headers))
        for r in rows:
            base = (
                int(r.get("period") or 0),
                str(r.get("date") or ""),
                float(r.get("price") or 0.0),
                float(r.get("shares") or 0.0),
                float(r.get("total_value") or 0.0),
                float(r.get("distribution_amount") or 0.0),
                float(r.get("drip_amount") or 0.0),
            )
            if args.show_event_counts:
                v = r.get("event_count")
                ev_s = ""
                if v is not None:
                    try:
                        ev_s = f"{float(v):.4f}"
                    except (TypeError, ValueError):
                        ev_s = ""
                print(fmt_row.format(*base, ev_s))
            else:
                print(fmt_row.format(*base))
        print()

    def _print_aggregate_with_symbol_breakdown(
        *,
        title: str,
        aggregate_rows: list[dict],
        per_symbol_outputs: dict[str, dict],
    ) -> None:
        """
        Print aggregate rows, and after each aggregate row print per-symbol amounts for that period.
        """
        print(title)
        headers = ["period", "date", "price", "shares", "total_value", "distribution", "drip"]
        period_w = max(
            len("period"),
            max((len(str(r.get("period") or "")) for r in aggregate_rows), default=0),
        )
        date_w = max(len("date"), max((len(str(r.get("date") or "")) for r in aggregate_rows), default=10))
        price_w = max(
            len("price"),
            max((len(f"{float(r.get('price') or 0.0):.4f}") for r in aggregate_rows), default=0),
        )
        shares_w = max(
            len("shares"),
            max((len(f"{float(r.get('shares') or 0.0):.6f}") for r in aggregate_rows), default=0),
        )
        value_w = max(
            len("total_value"),
            max((len(f"{float(r.get('total_value') or 0.0):.2f}") for r in aggregate_rows), default=0),
        )
        dist_w = max(
            len("distribution"),
            max(
                (len(f"{float(r.get('distribution_amount') or 0.0):.2f}") for r in aggregate_rows),
                default=0,
            ),
        )
        drip_w = max(
            len("drip"),
            max((len(f"{float(r.get('drip_amount') or 0.0):.2f}") for r in aggregate_rows), default=0),
        )

        fmt_header = (
            f"{{:>{period_w}}}  {{:<{date_w}}}  {{:>{price_w}}}  {{:>{shares_w}}}  "
            f"{{:>{value_w}}}  {{:>{dist_w}}}  {{:>{drip_w}}}"
        )
        fmt_row = (
            f"{{:>{period_w}d}}  {{:<{date_w}}}  {{:>{price_w}.4f}}  {{:>{shares_w}.6f}}  "
            f"{{:>{value_w}.2f}}  {{:>{dist_w}.2f}}  {{:>{drip_w}.2f}}"
        )
        print(fmt_header.format(*headers))

        sym_w = max(6, max((len(s) for s in per_symbol_outputs.keys()), default=6))
        sub_value_w = max(10, value_w)
        sub_dist_w = max(12, dist_w)
        sub_drip_w = max(8, drip_w)
        fmt_sym = f"  {{:<{sym_w}}}  value={{:>{sub_value_w}.2f}}  dist={{:>{sub_dist_w}.2f}}  drip={{:>{sub_drip_w}.2f}}"
        if args.show_event_counts:
            fmt_sym = fmt_sym + "  events={}"

        for agg in aggregate_rows:
            period = int(agg.get("period") or 0)
            print(
                fmt_row.format(
                    period,
                    str(agg.get("date") or ""),
                    float(agg.get("price") or 0.0),
                    float(agg.get("shares") or 0.0),
                    float(agg.get("total_value") or 0.0),
                    float(agg.get("distribution_amount") or 0.0),
                    float(agg.get("drip_amount") or 0.0),
                )
            )

            # Gather each symbol's row for this period and print it.
            per_sym_rows: list[tuple[str, float, float, float]] = []
            per_sym_events: dict[str, str] = {}
            for sym, out in per_symbol_outputs.items():
                rows = list(out.get("rows") or [])
                r = next((x for x in rows if int(x.get("period") or 0) == period), None)
                if not isinstance(r, dict):
                    continue
                per_sym_rows.append(
                    (
                        sym,
                        float(r.get("total_value") or 0.0),
                        float(r.get("distribution_amount") or 0.0),
                        float(r.get("drip_amount") or 0.0),
                    )
                )
                ev = r.get("event_count")
                if ev is None:
                    per_sym_events[sym] = ""
                else:
                    try:
                        per_sym_events[sym] = f"{float(ev):.4f}"
                    except (TypeError, ValueError):
                        per_sym_events[sym] = ""
            per_sym_rows.sort(key=lambda t: (-t[2], t[0]))
            for sym, value, dist_amt, drip_amt in per_sym_rows:
                if args.show_event_counts:
                    print(fmt_sym.format(sym, value, dist_amt, drip_amt, per_sym_events.get(sym, "")))
                else:
                    print(fmt_sym.format(sym, value, dist_amt, drip_amt))
        print()

    if args.config:
        config_path = Path(str(args.config)).expanduser()
        if not config_path.is_absolute():
            config_path = (script_env.repo_root() / config_path).resolve()
        positions = _load_positions_from_yaml(config_path)
        if not positions:
            raise SystemExit(f"no valid positions found in {config_path}")

        per_symbol_outputs: dict[str, dict] = {}
        aggregate_by_period: list[dict] = [
            {
                "period": i,
                "date": "",
                "price": 0.0,
                "shares": 0.0,
                "total_value": 0.0,
                "distribution_amount": 0.0,
                "drip_amount": 0.0,
            }
            for i in range(1, periods + 1)
        ]

        for sym, shares in positions:
            growth_rate, inferred = _infer_growth(sym)
            if inferred is not None:
                print(
                    f"[{sym}] inferred monthly_cagr={float(inferred.get('monthly_cagr') or 0.0):.4%} "
                    f"(median_monthly_return={float(inferred.get('median_monthly_return') or 0.0):.4%})"
                )
            out = build_drip_forecast(
                DripForecastInputs(
                    symbol=sym,
                    initial_shares=float(shares),
                    price_growth_rate=float(growth_rate),
                    drip_rate=float(args.drip_rate),
                    periods=periods,
                    as_of_date=as_of,
                    period_unit=str(args.period_unit),
                    monthly_event_mode=str(args.monthly_event_mode),
                )
            )
            per_symbol_outputs[sym] = out
            rows = list(out.get("rows") or [])
            for r in rows:
                idx = int(r.get("period") or 0) - 1
                if idx < 0 or idx >= len(aggregate_by_period):
                    continue
                aggregate_by_period[idx]["shares"] += float(r.get("shares") or 0.0)
                aggregate_by_period[idx]["total_value"] += float(r.get("total_value") or 0.0)
                aggregate_by_period[idx]["distribution_amount"] += float(
                    r.get("distribution_amount") or 0.0
                )
                aggregate_by_period[idx]["drip_amount"] += float(r.get("drip_amount") or 0.0)
                # Optional: aggregate event counts (useful for debugging month-to-month variance).
                ev = r.get("event_count")
                if ev is not None:
                    aggregate_by_period[idx]["event_count"] = float(
                        aggregate_by_period[idx].get("event_count") or 0.0
                    ) + float(ev)
                # Keep a representative date from the first symbol that has one.
                if not aggregate_by_period[idx]["date"] and (r.get("date") or ""):
                    aggregate_by_period[idx]["date"] = str(r.get("date") or "")

        # Price is not meaningful to aggregate; keep blank/0.
        title = (
            f"\nAggregate across {len(positions)} symbols "
            f"(drip_rate={float(args.drip_rate):.2f}, periods={periods})\n"
        )
        if args.no_symbol_breakdown:
            _print_table(title, aggregate_by_period)
        else:
            _print_aggregate_with_symbol_breakdown(
                title=title,
                aggregate_rows=aggregate_by_period,
                per_symbol_outputs=per_symbol_outputs,
            )

        if args.print_per_symbol:
            for sym in sorted(per_symbol_outputs.keys()):
                out = per_symbol_outputs[sym]
                print(
                    f"{sym}: spot_price={float(out.get('spot_price') or 0.0):.4f} "
                    f"median_dist_per_share={out.get('median_distribution_per_share')}"
                )
                _print_table("", list(out.get("rows") or []))
        return 0

    # Single-symbol mode (original behavior)
    sym = str(args.symbol or "").strip().upper()
    if not sym or args.shares is None:
        raise SystemExit("provide SYMBOL and --shares, or use --config")
    growth_rate, inferred = _infer_growth(sym)
    if inferred is not None:
        print(
            "Inferred monthly growth rate from history "
            f"(lookback_months={inferred.get('months_observed')}, series={inferred.get('price_series')}): "
            f"monthly_cagr={float(inferred.get('monthly_cagr') or 0.0):.4%} "
            f"(median_monthly_return={float(inferred.get('median_monthly_return') or 0.0):.4%})\n"
        )
    out = build_drip_forecast(
        DripForecastInputs(
            symbol=sym,
            initial_shares=float(args.shares),
            price_growth_rate=float(growth_rate),
            drip_rate=float(args.drip_rate),
            periods=periods,
            as_of_date=as_of,
            period_unit=str(args.period_unit),
            monthly_event_mode=str(args.monthly_event_mode),
        )
    )
    rows = list(out.get("rows") or [])
    print(
        f"Symbol={out.get('symbol')} spot_price={out.get('spot_price'):.4f} "
        f"median_dist_per_share={out.get('median_distribution_per_share')}\n"
    )
    _print_table("", rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

