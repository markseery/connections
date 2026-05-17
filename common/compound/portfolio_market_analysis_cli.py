#!/usr/bin/env python3
"""
Run technical indicators and SEC fundamental growth for each symbol in a YAML config.

Uses ``portfolio_market_analysis_skill`` on a live worker (default), or ``--local``
to run in-process without the worker.

Uses ``PositionLoader`` (``positions:`` or legacy ``symbols:``), e.g.::

    python scripts/run_symbol_market_analysis.py \\
      --config application_files/config/rh.symbols.yaml

Set ``SEC_EDGAR_USER_AGENT`` to a descriptive string with contact info (SEC policy).

Options ``--max`` and ``--sleep-sec`` limit batch size and pace SEC requests.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from common.simple import script_env

from application_files.portfolio_analyser.positions import PositionLoader
from common.complex.skill_lifecycle import find_live_worker
from common.compound.market_analysis_summaries import format_symbol_detail_lines
from common.compound.portfolio_market_run import run_portfolio_analysis, run_via_worker
from common.simple.user_dir import load_connections_dotenv

REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")


def _symbols_from_config(cfg: Path, max_symbols: int) -> List[str]:
    positions: List[Tuple[str, int]] = PositionLoader.load_from_yaml(cfg)
    if max_symbols and max_symbols > 0:
        positions = positions[:max_symbols]
    return [sym for sym, _ in positions]


def main() -> int:
    load_connections_dotenv()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--config",
        type=Path,
        default=None,
        help="YAML with positions (default: portfolio_analyser user config)",
    )
    ap.add_argument(
        "--period",
        default="5y",
        help="yfinance history period (default 5y for long SMAs)",
    )
    ap.add_argument(
        "--max",
        type=int,
        default=0,
        help="process at most this many symbols from the YAML, in file order (default: 0 = all)",
    )
    ap.add_argument("--sleep-sec", type=float, default=0.12, help="pause before each SEC request")
    ap.add_argument("--json-snapshot", action="store_true", help="append JSON lines per symbol after text")
    ap.add_argument(
        "--stability-period",
        default=None,
        help="yfinance period for stability/benchmark series (default: same as --period)",
    )
    ap.add_argument(
        "--worker-url",
        default=None,
        help="worker base URL (default: first live worker from registry)",
    )
    ap.add_argument(
        "--local",
        action="store_true",
        help="run in-process without worker (no HTTP)",
    )
    ap.add_argument(
        "--no-summaries",
        action="store_true",
        help="return snapshots only (skip portfolio summaries)",
    )
    ap.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="parallel symbol history fetch workers (default: min(8, symbol count))",
    )
    args = ap.parse_args()

    cfg = args.config
    if cfg is None:
        cfg = PositionLoader.default_config_path()
    cfg = cfg.resolve()
    symbols = _symbols_from_config(cfg, args.max)
    if not symbols:
        print("No symbols in config.", file=sys.stderr)
        return 1

    body: Dict[str, Any] = {
        "symbols": symbols,
        "period": args.period,
        "stability_period": args.stability_period,
        "sec_sleep_sec": args.sleep_sec,
        "include_summaries": not args.no_summaries,
        "summary_format": "text",
    }
    if args.max_workers is not None:
        body["max_workers"] = args.max_workers

    if args.local:
        result = run_portfolio_analysis(
            symbols,
            period=args.period,
            stability_period=args.stability_period,
            sec_sleep_sec=args.sleep_sec,
            include_summaries=not args.no_summaries,
            summary_format="text",
            max_workers=args.max_workers,
        )
    else:
        worker_url = (args.worker_url or "").strip() or find_live_worker(REGISTRY_URL)
        if not worker_url:
            print(
                "No live worker found. Start the worker or use --local.",
                file=sys.stderr,
            )
            return 1
        print(f"Worker: {worker_url}", flush=True)
        try:
            result = run_via_worker(worker_url, body)
        except Exception as exc:
            print(f"portfolio_market_analysis_skill failed: {exc}", file=sys.stderr)
            return 1

    snapshots = result.get("snapshots") or []
    for snap in snapshots:
        for line in format_symbol_detail_lines(snap):
            print(line)

    text = result.get("summaries_text") or ""
    if text:
        print(text, end="" if text.endswith("\n") else "\n")

    if args.json_snapshot:
        print("\n--- JSON ---")
        for obj in snapshots:
            print(json.dumps(obj, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
