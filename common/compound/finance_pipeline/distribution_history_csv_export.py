"""Export dividend history and summary CSVs for symbols in a positions YAML."""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

from application_files.portfolio_analyser.positions import PositionLoader
from common.compound.finance_pipeline.distribution_pattern_engine import (
    collect_symbol_dividend_history,
    parse_any_date,
)
from common.compound.finance_pipeline.stockanalysis_dividends import (
    StockAnalysisDividendExtractor,
    StockAnalysisDividendRow,
)
from common.simple import script_env


def _repo_root() -> Path:
    return script_env.repo_root()


def _resolve_positions_config(config: str) -> Path:
    repo = _repo_root()
    path = Path(config).expanduser()
    if not path.is_absolute():
        path = (repo / path).resolve()
    if not path.is_file():
        raise SystemExit(f"positions config not found: {path}")
    return path


def _ordered_symbols(positions: list[tuple[str, int]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for sym, _ in positions:
        u = str(sym or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _summary_csv_path(detail: Path) -> Path:
    return detail.with_name(f"{detail.stem}_summary{detail.suffix}")


def _amounts_time_ordered(history: list[StockAnalysisDividendRow]) -> list[float]:
    """Cash amounts ordered by ex-dividend date (oldest → newest)."""
    dated: list[tuple[int, float]] = []
    for r in history:
        if r.cash_amount is None:
            continue
        d = parse_any_date(r.ex_dividend_date)
        if d is None:
            continue
        try:
            amt = float(r.cash_amount)
        except (TypeError, ValueError):
            continue
        if amt <= 0:
            continue
        dated.append((d.toordinal(), amt))
    dated.sort(key=lambda t: t[0])
    return [amt for _, amt in dated]


def _median_amount(amounts: list[float]) -> float | None:
    if not amounts:
        return None
    return float(statistics.median(amounts))


def _ols_expected_next(amounts: list[float]) -> float | None:
    """Linear least squares on index 0..n-1; return prediction at index n (one step ahead)."""
    n = len(amounts)
    if n == 0:
        return None
    if n == 1:
        return float(amounts[0])
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(amounts) / n
    num = sum((xs[i] - mean_x) * (amounts[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den <= 1e-18:
        return float(mean_y)
    slope = num / den
    intercept = mean_y - slope * mean_x
    x_next = float(n)
    return float(intercept + slope * x_next)


def _recent_tail_median(amounts: list[float], window: int) -> tuple[float | None, int]:
    """Median of the most recent ``window`` payouts (chronological tail); ignores stale regimes."""
    if not amounts:
        return None, 0
    w = max(1, min(int(window), len(amounts)))
    tail = amounts[-w:]
    return float(statistics.median(tail)), w


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print per-symbol dividend history summary and write a CSV of history rows."
    )
    parser.add_argument(
        "--config",
        default="application_files/config/rh.symbols.yaml",
        help="Positions YAML (default: application_files/config/rh.symbols.yaml).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory for single-component ``--csv-out`` / ``--csv-summary-out`` paths "
            "(default: application_files/data/distribution_history). Relative paths are from the repo root."
        ),
    )
    parser.add_argument(
        "--csv-out",
        default="distribution_history.csv",
        help=(
            "Output CSV for per-event rows. A basename is written under --out-dir; "
            "paths with directories are resolved from the repo root (default: distribution_history.csv)."
        ),
    )
    parser.add_argument(
        "--csv-summary-out",
        default=None,
        help="Output CSV for one row per symbol (medians + next estimates). "
        "Default: same directory as --csv-out, name {stem}_summary{suffix}.",
    )
    parser.add_argument(
        "--recent-window",
        type=int,
        default=12,
        help="For expected_next_recent_median: use the last N chronological payouts (default: 12).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=25.0,
        help="HTTP timeout seconds for StockAnalysis requests (default: 25).",
    )
    return parser


def run_distribution_history_csv_export(args: argparse.Namespace) -> int:
    config_path = _resolve_positions_config(args.config)
    positions = PositionLoader.resolve([], config_path)
    if not positions:
        raise SystemExit(f"no positions found in config: {config_path}")
    symbols = _ordered_symbols(positions)
    if not symbols:
        print("No symbols found in positions config.", file=sys.stderr)
        return 1

    extractor = StockAnalysisDividendExtractor(timeout_seconds=max(5.0, float(args.timeout)))

    out_dir = script_env.resolve_output_dir(args.out_dir, segment="distribution_history")

    raw_detail = Path(args.csv_out).expanduser()
    if raw_detail.is_absolute():
        out_path = raw_detail
    elif len(raw_detail.parts) == 1:
        out_path = (out_dir / raw_detail.name).resolve()
    else:
        out_path = (_repo_root() / raw_detail).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.csv_summary_out:
        raw_summary = Path(args.csv_summary_out).expanduser()
        if raw_summary.is_absolute():
            summary_path = raw_summary
        elif len(raw_summary.parts) == 1:
            summary_path = (out_dir / raw_summary.name).resolve()
        else:
            summary_path = (_repo_root() / raw_summary).resolve()
    else:
        summary_path = _summary_csv_path(out_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "symbol",
        "history_source",
        "payout_frequency",
        "events_observed_bundle",
        "ex_dividend_date",
        "cash_amount",
        "record_date",
        "pay_date",
        "error",
    ]

    recent_w = max(1, int(args.recent_window))

    summary_fieldnames = [
        "symbol",
        "history_source",
        "payout_frequency",
        "n_amount_points",
        "median_cash_amount",
        "expected_next_recent_median",
        "recent_tail_count",
        "expected_next_ols_global",
        "error",
    ]
    summary_rows: list[dict[str, object]] = []

    rows_written = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for symbol in symbols:
            err = ""
            bundle: dict = {}
            try:
                bundle = collect_symbol_dividend_history(symbol=symbol, extractor=extractor)
                snapshot = extractor.extract(symbol)
            except Exception as exc:
                err = str(exc)
                print(f"** {symbol} **  ERROR: {err}", flush=True)
                writer.writerow(
                    {
                        "symbol": symbol,
                        "history_source": "",
                        "payout_frequency": "",
                        "events_observed_bundle": "",
                        "ex_dividend_date": "",
                        "cash_amount": "",
                        "record_date": "",
                        "pay_date": "",
                        "error": err,
                    }
                )
                rows_written += 1
                summary_rows.append(
                    {
                        "symbol": symbol,
                        "history_source": "",
                        "payout_frequency": "",
                        "n_amount_points": "",
                        "median_cash_amount": "",
                        "expected_next_recent_median": "",
                        "recent_tail_count": "",
                        "expected_next_ols_global": "",
                        "error": err,
                    }
                )
                continue

            src = str(bundle.get("history_source") or "")
            freq = str(bundle.get("payout_frequency") or "")
            ev = bundle.get("events_observed")
            hdates = bundle.get("history_dates") or []
            d_first = min(hdates).isoformat() if hdates else ""
            d_last = max(hdates).isoformat() if hdates else ""

            print(
                f"** {symbol} **  source={src!r}  payout_frequency={freq!r}  "
                f"events_observed={ev}  merged_history_dates={len(hdates)}  "
                f"range=[{d_first} … {d_last}]  stockanalysis_table_rows={len(snapshot.history)}",
                flush=True,
            )

            meta = {
                "symbol": symbol,
                "history_source": src,
                "payout_frequency": freq,
                "events_observed_bundle": ev if ev is not None else "",
                "error": "",
            }
            if not snapshot.history:
                writer.writerow(
                    {
                        **meta,
                        "ex_dividend_date": "",
                        "cash_amount": "",
                        "record_date": "",
                        "pay_date": "",
                    }
                )
                rows_written += 1
                summary_rows.append(
                    {
                        "symbol": symbol,
                        "history_source": src,
                        "payout_frequency": freq,
                        "n_amount_points": 0,
                        "median_cash_amount": "",
                        "expected_next_recent_median": "",
                        "recent_tail_count": "",
                        "expected_next_ols_global": "",
                        "error": "",
                    }
                )
                continue

            amounts = _amounts_time_ordered(list(snapshot.history))
            med = _median_amount(amounts)
            recent_med, tail_n = _recent_tail_median(amounts, recent_w)
            ols_next = _ols_expected_next(amounts) if amounts else None
            summary_rows.append(
                {
                    "symbol": symbol,
                    "history_source": src,
                    "payout_frequency": freq,
                    "n_amount_points": len(amounts),
                    "median_cash_amount": f"{med:.6f}" if med is not None else "",
                    "expected_next_recent_median": f"{recent_med:.6f}" if recent_med is not None else "",
                    "recent_tail_count": tail_n,
                    "expected_next_ols_global": f"{ols_next:.6f}" if ols_next is not None else "",
                    "error": "",
                }
            )

            for r in snapshot.history:
                writer.writerow(
                    {
                        **meta,
                        "ex_dividend_date": r.ex_dividend_date,
                        "cash_amount": r.cash_amount if r.cash_amount is not None else "",
                        "record_date": r.record_date or "",
                        "pay_date": r.pay_date or "",
                    }
                )
                rows_written += 1

    with summary_path.open("w", newline="", encoding="utf-8") as sf:
        sw = csv.DictWriter(sf, fieldnames=summary_fieldnames)
        sw.writeheader()
        for row in summary_rows:
            sw.writerow({k: row.get(k, "") for k in summary_fieldnames})

    print(f"\nWrote {rows_written} CSV row(s) to {out_path}", flush=True)
    print(f"Wrote {len(summary_rows)} summary row(s) to {summary_path}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run_distribution_history_csv_export(args)
