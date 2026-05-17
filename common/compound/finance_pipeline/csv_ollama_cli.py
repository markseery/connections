"""CLI: send CSV file contents as context to Ollama."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common.compound.finance_pipeline.csv_ollama import CsvOllamaClient, CsvOllamaConfig


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Use CSV file contents as context for an Ollama prompt (stdlib only).",
    )
    ap.add_argument(
        "csv_files",
        nargs="+",
        type=Path,
        metavar="FILE.csv",
        help="One or more CSV files",
    )
    ap.add_argument(
        "-p",
        "--prompt",
        required=True,
        help="Instruction or question; CSV data is prepended as context",
    )
    ap.add_argument(
        "--model",
        default="gemma4:e2b",
        help="Ollama model tag (default: gemma4:e2b)",
    )
    ap.add_argument(
        "--ollama",
        default="http://127.0.0.1:11434",
        help="Ollama base URL",
    )
    ap.add_argument(
        "--max-context-chars",
        type=int,
        default=200_000,
        metavar="N",
        help="Max characters of CSV text per file (default: 200000)",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="HTTP timeout in seconds (default: 600)",
    )
    return ap


def run_csv_ollama_prompt(args: argparse.Namespace) -> int:
    for p in args.csv_files:
        if not p.is_file():
            print(f"Error: not a file: {p}", file=sys.stderr)
            return 1

    cfg = CsvOllamaConfig(
        base_url=args.ollama,
        model=args.model,
        max_context_chars=args.max_context_chars,
        timeout_sec=args.timeout,
    )
    client = CsvOllamaClient(cfg)

    try:
        out = client.generate(args.csv_files, args.prompt)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run_csv_ollama_prompt(args)
