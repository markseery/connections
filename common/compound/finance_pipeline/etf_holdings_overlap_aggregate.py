"""Load cached ETF JSON files, merge holdings, and build overlap / pairwise stats for reporting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_etf_cache_directory(cache_dir: Path) -> list[dict[str, Any]]:
    """
    Read every ``*.json`` in ``cache_dir``. Each item includes ``cache_file_symbol``,
    metadata, and a normalized ``holdings`` list.
    """
    d = Path(cache_dir).resolve()
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        data = raw.get("data")
        if not isinstance(data, dict):
            continue
        sym = str(path.stem or "").strip().upper()
        raw_holdings = data.get("holdings")
        rows: list[dict[str, Any]] = []
        if isinstance(raw_holdings, list):
            for h in raw_holdings:
                if not isinstance(h, dict):
                    continue
                t = str(h.get("ticker") or "").strip().upper()
                if not t:
                    continue
                w = h.get("weight_pct")
                name = str(h.get("name") or "")[:120]
                row: dict[str, Any] = {"ticker": t, "name": name}
                if isinstance(w, (int, float)):
                    row["weight_pct"] = w
                rows.append(row)
        out.append(
            {
                "cache_file_symbol": sym,
                "etf_ticker": str(raw.get("etf_ticker") or sym).strip().upper(),
                "cached_at": raw.get("cached_at"),
                "source": raw.get("source"),
                "underlying_ticker": (
                    str(raw.get("underlying_ticker")).strip().upper()
                    if isinstance(raw.get("underlying_ticker"), str) and str(raw.get("underlying_ticker")).strip()
                    else None
                ),
                "n_holdings_rows": len(rows),
                "holdings": rows,
            }
        )
    return out


def build_overlap_aggregation(etfs: list[dict[str, Any]], *, top_pairwise: int = 30) -> dict[str, Any]:
    """
    Cross-ETF stats: per-ticker frequency, pairwise intersection / Jaccard, sector-like signals for AI.
    """
    symbols = [e["cache_file_symbol"] for e in etfs]
    ticker_sets: dict[str, set[str]] = {}
    for e in etfs:
        s = e["cache_file_symbol"]
        ticker_sets[s] = {r["ticker"] for r in e.get("holdings") or [] if r.get("ticker")}

    ticker_to_etfs: dict[str, list[str]] = {}
    for s, tset in ticker_sets.items():
        for t in tset:
            ticker_to_etfs.setdefault(t, []).append(s)
    for t in list(ticker_to_etfs):
        ticker_to_etfs[t] = sorted(ticker_to_etfs[t])

    count_by_appearance = {t: len(v) for t, v in ticker_to_etfs.items()}
    most_held = sorted(
        count_by_appearance.items(), key=lambda kv: (-kv[1], kv[0])
    )[:200]

    pairs: list[dict[str, Any]] = []
    for i, a in enumerate(symbols):
        for b in symbols[i + 1 :]:
            A, B = ticker_sets.get(a, set()), ticker_sets.get(b, set())
            inter = A & B
            union = A | B
            j = (len(inter) / len(union)) if union else 0.0
            pairs.append(
                {
                    "etf_a": a,
                    "etf_b": b,
                    "shared_ticker_count": len(inter),
                    "union_ticker_count": len(union),
                    "jaccard": round(j, 4),
                    "shared_tickers": sorted(inter)[:150],
                }
            )
    by_j = sorted(pairs, key=lambda x: (-x["jaccard"], -x["shared_ticker_count"], x["etf_a"]))
    least = list(reversed(by_j))  # low jaccard first
    n = max(0, int(top_pairwise))
    return {
        "n_cached_etfs": len(etfs),
        "etf_symbols": sorted(symbols),
        "ticker_appearance_count": dict(most_held),
        "tickers_appearing_in_multiple_etfs": {
            t: c for t, c in most_held if c > 1
        },
        "pairwise_all_count": len(pairs),
        "pairwise_highest_jaccard": by_j[:n],
        "pairwise_lowest_jaccard": least[:n],
    }
