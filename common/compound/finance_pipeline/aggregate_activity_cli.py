"""CLI: aggregate activity JSON into totals JSON (+ optional CSV)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common.compound.finance_pipeline.aggregator import ActivityAggregator, read_json
from common.simple import script_env


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Sum amounts by instrument and trans_code from activity JSON.",
    )
    ap.add_argument(
        "input",
        help="JSON file path, or - for stdin",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory for default JSON/CSV when paths are omitted (default: "
            "application_files/data/aggregate_activity). Relative paths are from the repo root."
        ),
    )
    ap.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=None,
        help=(
            "Write result JSON here. Default: <stem>.totals.json under --out-dir. "
            "For stdin (-), default is stdin.totals.json under --out-dir. Use -o - for stdout."
        ),
    )
    ap.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent (default: 2; use 0 for compact)",
    )
    ap.add_argument(
        "--csv",
        metavar="FILE",
        default=None,
        help=(
            "Write flattened aggregates to this CSV path. "
            "Default: same path as the JSON output with a .csv suffix (when JSON is a file). "
            "Use --no-csv to skip."
        ),
    )
    ap.add_argument(
        "--no-csv",
        action="store_true",
        help="Do not write a CSV file.",
    )
    return ap


def run_aggregate_activity_json(args: argparse.Namespace) -> int:
    out_dir = script_env.resolve_output_dir(args.out_dir, segment="aggregate_activity")

    agg_engine = ActivityAggregator()
    try:
        data = read_json(args.input)
        out = agg_engine.aggregate(data)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    indent = None if args.indent == 0 else args.indent
    text = json.dumps(out, indent=indent, ensure_ascii=False)

    json_out_path: Path | None = None
    if args.output == "-":
        print(text)
    elif args.output:
        json_out_path = Path(args.output).expanduser()
        if not json_out_path.is_absolute():
            json_out_path = out_dir / json_out_path
        json_out_path.parent.mkdir(parents=True, exist_ok=True)
        json_out_path.write_text(text, encoding="utf-8")
    else:
        if args.input == "-":
            json_out_path = out_dir / "stdin.totals.json"
        else:
            json_out_path = out_dir / f"{Path(args.input).stem}.totals.json"
        json_out_path.write_text(text, encoding="utf-8")

    if not args.no_csv:
        csv_path: Path | None = None
        if args.csv:
            csv_path = Path(args.csv).expanduser()
            if not csv_path.is_absolute():
                csv_path = out_dir / csv_path
        elif json_out_path is not None:
            csv_path = json_out_path.with_suffix(".csv")
        if csv_path is not None:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            ActivityAggregator.write_csv(csv_path, out)

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run_aggregate_activity_json(args)
