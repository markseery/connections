"""Load all ETF holdings cache files, aggregate overlap statistics, and send to aiserver for analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common.simple import script_env

_REPO_ROOT = script_env.repo_root()

from common.compound.aiserver_discovery import get_aiserver_base_url
from common.compound.aiserver_generate_client import AiserverGenerateClient
from common.compound.finance_pipeline.etf_holdings_cache import default_cache_dir
from common.compound.finance_pipeline.etf_holdings_overlap_aggregate import (
    build_overlap_aggregation,
    load_etf_cache_directory,
)


def _build_prompt(aggregate: dict[str, Any], per_etf: list[dict[str, Any]], *, max_holdings: int) -> str:
    """Prompt asks for most vs least overlap; data is included as JSON for grounding."""
    slim_etfs: list[dict[str, Any]] = []
    for e in per_etf:
        h = (e.get("holdings") or [])[: max(0, int(max_holdings))]
        slim_etfs.append(
            {
                "cache_file_symbol": e.get("cache_file_symbol"),
                "etf_ticker": e.get("etf_ticker"),
                "source": e.get("source"),
                "underlying_ticker": e.get("underlying_ticker"),
                "cached_at": e.get("cached_at"),
                "n_holdings_in_cache": e.get("n_holdings_rows"),
                "holdings_top_slice": h,
            }
        )
    payload: dict[str, Any] = {
        "per_etf_holdings_sliced": slim_etfs,
        "overlap_aggregation": aggregate,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    return (
        "You are given JSON built from a local on-disk cache of multiple ETF / fund holdings. "
        "Each entry under per_etf_holdings_sliced is one cached symbol. "
        "holdings_top_slice is the first N rows from that file (or fewer if the list is short). "
        "overlap_aggregation has pairwise Jaccard similarity, shared tickers, and which "
        "constituent tickers appear in multiple cached funds.\n\n"
        "Important: distinguish **literal holdings overlap** vs **economic exposure overlap**.\n"
        "- Literal overlap: treat tickers as-is (this matches the Jaccard stats in overlap_aggregation).\n"
        "- Economic exposure overlap (\"look-through\"): when a fund holds another ETF/fund as a position, "
        "treat that position as exposure to the held fund's constituents IF (and only if) those "
        "constituents are available in this JSON.\n"
        "  - Example pattern (generic): if Fund A holds ETF X at ~100%, and ETF X appears as its own "
        "entry in per_etf_holdings_sliced (or its constituents appear elsewhere in the JSON), then "
        "Fund A and any fund holding X's constituents should be described as having high economic "
        "overlap even if their literal ticker overlap is low.\n"
        "  - If the held ETF/fund (e.g. X) is NOT present as a cached entry here, you may NOT assume its "
        "constituents; instead, flag the missing data and keep the conclusion tentative.\n"
        "- Non-equivalence / caveats: do NOT claim two funds have the same exposure if the holdings "
        "suggest overlays or structural differences. Watch for signals like: options strategies "
        "(covered calls/collars), leveraged/inverse exposure, futures/swaps, concentrated vs broad "
        "sampling, large cash/treasury positions, or the fund mostly holding \"CASH\"-like instruments.\n\n"
        "Answer clearly (use the JSON only; do not invent tickers).\n"
        "Instead of only the single most/least, enumerate **all notable examples** under BOTH lenses.\n"
        "Use the overlap_aggregation pairwise Jaccard where available, and apply look-through where supported.\n\n"
        "a) Examples of **significant overlap** (include all pairs/groups that qualify):\n"
        "   - a1) Significant literal holdings overlap: list every ETF pair/group with clearly high Jaccard "
        "(or large shared_ticker_count), and cite key shared tickers.\n"
        "   - a2) Significant economic exposure overlap via look-through: list every wrapper/feeder pattern "
        "(fund holds another ETF) where constituents/themes make exposure similar, even if literal overlap is low.\n"
        "b) Examples of **no or insignificant overlap** (include all pairs/groups that qualify):\n"
        "   - b1) No/low literal overlap: list every ETF pair with near-zero / very low Jaccard and explain why, "
        "grounded in the holdings shown.\n"
        "   - b2) No/low economic overlap after look-through: list pairs that remain dissimilar even after look-through, "
        "or where look-through is blocked by missing data; explain what is missing and what structural differences "
        "(options overlays, leverage/inverse, futures/swaps, cash-heavy) prevent “same exposure” claims.\n\n"
        "If a metric is empty (e.g. only one fund), say so. If look-through cannot be applied due to missing "
        "constituent data in the JSON, say exactly what is missing.\n\n"
        f"Data:\n```json\n{text}\n```\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate ETF cache holdings and ask aiserver where overlaps are largest vs smallest."
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="ETF cache directory (default: application_files/data/etf_holdings_cache).",
    )
    parser.add_argument(
        "--max-holdings-per-etf",
        type=int,
        default=80,
        metavar="N",
        help="Max holdings rows to include per fund in the prompt (default: 80).",
    )
    parser.add_argument(
        "--top-pairwise",
        type=int,
        default=30,
        help="How many highest/lowest Jaccard pairs to list in the aggregation (default: 30).",
    )
    parser.add_argument(
        "--aiserver-url",
        default=None,
        help="Aiserver base URL (optional; else registry / dev fallback).",
    )
    parser.add_argument(
        "--registry-url",
        default=None,
        help="Registry URL for aiserver discovery.",
    )
    parser.add_argument(
        "--profile",
        default="reason",
        help="Aiserver /generate profile (default: reason).",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Optional provider override for /generate.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="HTTP timeout for /generate in seconds (default: 300).",
    )
    parser.add_argument(
        "--print-aggregate-only",
        action="store_true",
        help="Only print the aggregation JSON to stdout; do not call aiserver.",
    )
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="After building the prompt, print it and do not call aiserver (implies large output).",
    )
    args = parser.parse_args()

    cache_dir = (
        Path(args.cache_dir).expanduser()
        if args.cache_dir
        else default_cache_dir(_REPO_ROOT)
    )
    if not cache_dir.is_absolute():
        cache_dir = (_REPO_ROOT / cache_dir).resolve()
    if not cache_dir.is_dir():
        print(f"error: cache directory not found: {cache_dir}", file=sys.stderr)
        return 2

    per_etf = load_etf_cache_directory(cache_dir)
    if not per_etf:
        print(f"error: no valid ETF cache JSON files in {cache_dir}", file=sys.stderr)
        return 1

    agg = build_overlap_aggregation(per_etf, top_pairwise=int(args.top_pairwise))
    if args.print_aggregate_only:
        print(json.dumps({"per_etf_count": len(per_etf), "aggregation": agg}, indent=2, ensure_ascii=False))
        return 0

    prompt = _build_prompt(agg, per_etf, max_holdings=max(0, int(args.max_holdings_per_etf)))
    if args.print_prompt:
        print(prompt, flush=True)
        return 0

    reg = str(args.registry_url).strip() if args.registry_url else None
    explicit = str(args.aiserver_url).strip() if args.aiserver_url else None
    try:
        base = get_aiserver_base_url(explicit=explicit or None, registry_override=reg)
    except Exception as exc:  # noqa: BLE001
        print(f"error: aiserver URL resolution failed: {exc}", file=sys.stderr)
        return 2

    prov = str(args.provider).strip() if args.provider else None
    client = AiserverGenerateClient(base_url=base, timeout_sec=float(args.timeout))
    try:
        payload = client.generate(
            prompt=prompt, profile=str(args.profile).strip() or "reason", provider=prov or None
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: aiserver /generate failed: {exc}", file=sys.stderr)
        return 1

    out = AiserverGenerateClient.output_text(payload)
    print(out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
