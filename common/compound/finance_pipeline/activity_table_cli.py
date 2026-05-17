"""CLI: convert broker activity tables (CSV/TSV) to JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common.compound.finance_pipeline.activity_parser import ActivityParserConfig, ActivityTableParser
from common.simple import script_env


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Convert activity table (TSV/CSV) to JSON (stdlib only).",
    )
    ap.add_argument(
        "input",
        help="Input file path, or - for stdin",
    )
    ap.add_argument(
        "-d",
        "--delimiter",
        choices=("auto", "tab", "comma"),
        default="auto",
        help="Field delimiter (default: auto)",
    )
    ap.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent (default: 2; use 0 for compact)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory for default output when -o is omitted (default: "
            "application_files/data/activity_table_to_json). Relative paths are from the repo root."
        ),
    )
    ap.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=None,
        help=(
            "Write JSON to this path. Default: <stem>.json under --out-dir when input is a file. "
            "When input is stdin (-), default is stdin.json under --out-dir. Use -o - for stdout."
        ),
    )
    return ap


def run_activity_table_to_json(args: argparse.Namespace) -> int:
    out_dir = script_env.resolve_output_dir(args.out_dir, segment="activity_table_to_json")

    parser = ActivityTableParser(ActivityParserConfig(delimiter=args.delimiter))
    try:
        out = parser.parse_file(args.input)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    indent = None if args.indent == 0 else args.indent
    payload = json.dumps(out, indent=indent, ensure_ascii=False)

    if args.output == "-":
        print(payload)
    elif args.output:
        target = Path(args.output).expanduser()
        if not target.is_absolute():
            target = out_dir / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    else:
        if args.input == "-":
            out_path = out_dir / "stdin.json"
        else:
            out_path = out_dir / f"{Path(args.input).stem}.json"
        out_path.write_text(payload, encoding="utf-8")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run_activity_table_to_json(args)
