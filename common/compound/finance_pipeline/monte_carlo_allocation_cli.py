"""
Monte Carlo allocation search for income + ending value (0% price growth).

Given a list of symbols (e.g. application_files/config/rh.symbols.yaml), sample many
random allocations of a fixed budget across those symbols and estimate:
  - ending portfolio value (with optional DRIP reinvestment, constant price)
  - monthly distribution (gross and cash after DRIP)

This is a heuristic search (Dirichlet-random portfolios), not an optimizer.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any

import yaml

from common.simple import script_env

from common.compound.finance_pipeline.distribution_pattern_engine import analyze_symbol_distribution
from common.compound.finance_pipeline.drip_forecast import get_current_price, infer_monthly_price_growth_rate
from common.compound.finance_pipeline.stockanalysis_dividends import StockAnalysisDividendExtractor


_AVG_DAYS_PER_MONTH = 365.25 / 12.0


def _load_symbols_from_positions_yaml(path: Path) -> list[str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("positions") or []
    if not isinstance(items, list):
        raise ValueError("positions must be a list")
    out: list[str] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").strip().upper()
        if sym:
            out.append(sym)
    # preserve order but dedupe
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _dirichlet_weights(rng: random.Random, n: int, alpha: float = 1.0) -> list[float]:
    if n <= 0:
        return []
    a = float(alpha)
    if a <= 0:
        raise ValueError("alpha must be > 0")
    xs = [rng.gammavariate(a, 1.0) for _ in range(n)]
    s = sum(xs)
    if s <= 0:
        return [1.0 / n] * n
    return [x / s for x in xs]


def _apply_anti_max_distribution_bias(
    weights: list[float],
    *,
    monthly_dist_per_share: list[float],
    strength: float,
) -> list[float]:
    """
    Reduce the probability that high-distribution symbols become the max-weight position.

    We do this by scaling sampled weights by a factor inversely related to each symbol's
    expected monthly distribution per share. Higher distribution => smaller factor.
    """
    if not weights or not monthly_dist_per_share or len(weights) != len(monthly_dist_per_share):
        return weights
    s = float(strength)
    if s <= 0:
        return weights

    vals = [max(0.0, float(v)) for v in monthly_dist_per_share]
    positives = sorted([v for v in vals if v > 0])
    if not positives:
        return weights
    median = positives[len(positives) // 2]
    if median <= 0:
        return weights
    eps = median * 1e-6

    scaled: list[float] = []
    for w, v in zip(weights, vals):
        # factor < 1 when v > median, > 1 when v < median
        factor = (median / (v + eps)) ** s
        scaled.append(float(w) * float(factor))
    total = sum(scaled)
    if total <= 0:
        return weights
    return [x / total for x in scaled]


def _cap_weights(weights: list[float], *, max_weight: float) -> list[float]:
    """
    Enforce per-symbol max allocation by clipping and redistributing.

    This keeps all weights >= 0, sum to 1, and each weight <= max_weight (within float tolerance).
    """
    if not weights:
        return weights
    cap = float(max_weight)
    if cap <= 0 or cap > 1:
        raise ValueError("max_weight must be in (0, 1]")
    n = len(weights)
    if cap * n < 1.0 - 1e-12:
        raise ValueError(f"max_weight={cap} is infeasible for n={n} (need cap >= 1/n)")

    w = [max(0.0, float(x)) for x in weights]
    s = sum(w)
    if s <= 0:
        w = [1.0 / n] * n
    else:
        w = [x / s for x in w]

    # Iteratively clip and redistribute to non-clipped weights.
    for _ in range(50):
        over = [i for i, x in enumerate(w) if x > cap]
        if not over:
            break
        excess = sum(w[i] - cap for i in over)
        for i in over:
            w[i] = cap
        under = [i for i, x in enumerate(w) if x < cap - 1e-15]
        if not under:
            # Everyone is at cap; distribute tiny numeric remainder uniformly.
            rem = 1.0 - sum(w)
            if abs(rem) < 1e-12:
                break
            add = rem / n
            w = [min(cap, max(0.0, x + add)) for x in w]
            break
        under_sum = sum(w[i] for i in under)
        if under_sum <= 0:
            add = excess / len(under)
            for i in under:
                w[i] = min(cap, w[i] + add)
        else:
            for i in under:
                w[i] = min(cap, w[i] + excess * (w[i] / under_sum))
        # Renormalize to correct drift.
        tot = sum(w)
        if tot > 0:
            w = [x / tot for x in w]
    return w


def _events_per_month_expected(*, payout_frequency: str | None, cadence_days: float | None) -> float:
    # Use cadence_days when available; it already encodes weekly/monthly/quarterly-ish patterns.
    if cadence_days is not None and cadence_days > 0:
        return max(0.0, _AVG_DAYS_PER_MONTH / float(cadence_days))
    freq = str(payout_frequency or "").strip().lower()
    if "week" in freq:
        return _AVG_DAYS_PER_MONTH / 7.0
    if "biweek" in freq:
        return _AVG_DAYS_PER_MONTH / 14.0
    if "quarter" in freq:
        return 1.0 / 3.0
    if "annual" in freq or "year" in freq:
        return 1.0 / 12.0
    return 1.0


@dataclass(frozen=True)
class SymbolParams:
    symbol: str
    price: float
    per_share_distribution: float
    events_per_month: float
    cadence_days: float | None
    payout_frequency: str | None
    source: str | None
    monthly_price_growth_rate: float


def _load_symbol_params(
    symbols: list[str],
    *,
    as_of: date,
    price_growth_rate: float | None,
    infer_monthly_growth_rate: bool,
    lookback_months: int,
    price_series: str,
) -> list[SymbolParams]:
    extractor = StockAnalysisDividendExtractor()
    out: list[SymbolParams] = []
    for sym in symbols:
        price = get_current_price(sym)
        if price is None or price <= 0:
            raise RuntimeError(f"[{sym}] unable to resolve current price")

        growth = price_growth_rate
        if infer_monthly_growth_rate:
            try:
                inferred = infer_monthly_price_growth_rate(
                    symbol=sym,
                    lookback_months=int(lookback_months),
                    price_series=str(price_series),
                )
                if growth is None:
                    growth = float(inferred.get("monthly_cagr") or 0.0)
            except Exception as exc:
                # Do not fail the whole run due to one symbol's missing/short history.
                if growth is None:
                    growth = 0.0
                print(f"[{sym}] growth inference failed: {exc} (falling back to {float(growth):.4%})", flush=True)
        if growth is None:
            growth = 0.0

        dist = analyze_symbol_distribution(
            symbol=sym,
            shares=10,  # just to populate fields; we use per-share outputs
            as_of_date=as_of,
            horizon_days=3650,
            extractor=extractor,
        )
        per_share = dist.get("median_distribution_per_share")
        if per_share is None:
            per_share = 0.0
        per_share = float(per_share or 0.0)
        cadence = dist.get("cadence_days")
        cadence_f = float(cadence) if isinstance(cadence, (int, float)) and float(cadence) > 0 else None
        freq = dist.get("payout_frequency")
        events = _events_per_month_expected(payout_frequency=str(freq) if freq else None, cadence_days=cadence_f)
        out.append(
            SymbolParams(
                symbol=sym,
                price=float(price),
                per_share_distribution=per_share,
                events_per_month=float(events),
                cadence_days=cadence_f,
                payout_frequency=str(freq) if freq else None,
                source=str(dist.get("next_distribution_source") or "") or None,
                monthly_price_growth_rate=float(growth),
            )
        )
    return out


def _simulate_portfolio(
    params: list[SymbolParams],
    weights: list[float],
    *,
    budget: float,
    months: int,
    drip_rate: float,
) -> dict[str, Any]:
    months = max(1, int(months))
    drip = float(drip_rate)
    if drip < 0 or drip > 1:
        raise ValueError("drip_rate must be in [0, 1]")
    if len(weights) != len(params):
        raise ValueError("weights length mismatch")

    end_value = 0.0
    end_monthly_gross = 0.0
    avg_monthly_gross = 0.0

    alloc: dict[str, float] = {}
    for w, p in zip(weights, params):
        dollars = float(budget) * float(w)
        alloc[p.symbol] = dollars
        shares0 = dollars / p.price if p.price > 0 else 0.0

        # Monthly gross distribution per share (expected), using per_share_distribution * events_per_month.
        dist_per_share_month = p.per_share_distribution * p.events_per_month
        price_end = p.price * ((1.0 + float(p.monthly_price_growth_rate)) ** float(months))

        if dist_per_share_month <= 0 or shares0 <= 0:
            end_value += shares0 * price_end
            continue

        # With constant price and constant per-share distribution, DRIP is multiplicative.
        g = 1.0 + (drip * dist_per_share_month / p.price)
        if g <= 0:
            g = 1.0
        shares_end = shares0 * (g**months)

        # Gross distribution in last month:
        gross_last = shares_end * dist_per_share_month

        # Average gross distribution across months (geometric series):
        if abs(g - 1.0) < 1e-12:
            gross_avg = shares0 * dist_per_share_month
        else:
            gross_sum = shares0 * dist_per_share_month * (g * (g**months - 1.0) / (g - 1.0)) / g
            gross_avg = gross_sum / float(months)

        end_value += shares_end * price_end
        end_monthly_gross += gross_last
        avg_monthly_gross += gross_avg

    # "Cash" distributions are what is NOT reinvested.
    end_monthly_cash = end_monthly_gross * (1.0 - drip)
    avg_monthly_cash = avg_monthly_gross * (1.0 - drip)

    return {
        "ending_value": end_value,
        "end_monthly_distribution_gross": end_monthly_gross,
        "avg_monthly_distribution_gross": avg_monthly_gross,
        "end_monthly_distribution_cash": end_monthly_cash,
        "avg_monthly_distribution_cash": avg_monthly_cash,
        "weights": {p.symbol: w for p, w in zip(params, weights)},
        "starting_dollars": alloc,
    }


def _pareto_frontier(rows: list[dict[str, Any]], *, x: str, y: str) -> list[dict[str, Any]]:
    # Maximize both x and y.
    pts = sorted(rows, key=lambda r: (float(r.get(x) or 0.0), float(r.get(y) or 0.0)), reverse=True)
    frontier: list[dict[str, Any]] = []
    best_y = -1e99
    for r in pts:
        yy = float(r.get(y) or 0.0)
        if yy > best_y:
            best_y = yy
            frontier.append(r)
    return frontier


def _topk_push(
    heap: list[tuple[float, int, dict[str, Any]]], item: dict[str, Any], *, score: float, k: int, seq: int
) -> None:
    """
    Keep a min-heap of at most k items, keyed by score (higher is better).
    Heap entries are (score, seq, item) so ties never compare dicts.
    """
    if k <= 0:
        return
    if len(heap) < k:
        heapq.heappush(heap, (score, seq, item))
        return
    if score > heap[0][0]:
        heapq.heapreplace(heap, (score, seq, item))


def _topk_items(heap: list[tuple[float, int, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [it for _, __, it in sorted(heap, key=lambda t: t[0], reverse=True)]


def _pareto_frontier_update(frontier_ws: list[dict[str, Any]], row: dict[str, Any], *, x: str, y: str, cap: int) -> None:
    """
    Maintain an approximate Pareto frontier working set incrementally.
    We periodically compress to the true frontier to keep memory bounded.
    """
    frontier_ws.append(row)
    if cap <= 0:
        return
    if len(frontier_ws) >= max(2000, cap * 8):
        f = _pareto_frontier(frontier_ws, x=x, y=y)
        del frontier_ws[:]
        # Keep a little slack beyond cap to reduce churn.
        frontier_ws.extend(f[: max(cap, min(len(f), cap * 2))])


def main() -> int:
    ap = argparse.ArgumentParser(description="Monte Carlo allocation search across a symbol list.")
    ap.add_argument(
        "--config",
        default="application_files/config/rh.symbols.yaml",
        help="Positions YAML providing the symbol list (default: application_files/config/rh.symbols.yaml).",
    )
    ap.add_argument("--budget", type=float, default=350000.0, help="Initial dollars to allocate (default 350000).")
    ap.add_argument("--months", type=int, default=24, help="Number of months to simulate (default 24).")
    ap.add_argument(
        "--price-growth-rate",
        type=float,
        default=None,
        help="Monthly price growth rate applied to ending value (e.g. 0.01 == +1%% per month). Default 0.",
    )
    ap.add_argument(
        "--infer-monthly-growth-rate",
        action="store_true",
        help="Infer a per-symbol monthly growth rate from history (uses yfinance monthly bars).",
    )
    ap.add_argument(
        "--lookback-months",
        type=int,
        default=36,
        help="Lookback window for --infer-monthly-growth-rate (default: 36).",
    )
    ap.add_argument(
        "--price-series",
        choices=["close", "adjclose"],
        default="adjclose",
        help="Which monthly price series to use for growth inference (default: adjclose).",
    )
    ap.add_argument(
        "--drip-rate",
        type=float,
        default=1.0,
        help="Fraction of distributions reinvested (0..1). Default 1.0.",
    )
    ap.add_argument("--samples", type=int, default=20000, help="Monte Carlo samples (default 20000).")
    ap.add_argument("--alpha", type=float, default=1.0, help="Dirichlet alpha (default 1.0).")
    ap.add_argument("--seed", type=int, default=7, help="RNG seed (default 7).")
    ap.add_argument(
        "--progress-every",
        type=int,
        default=1000,
        help="Print progress every N samples (default 1000). Set 0 to disable.",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress prints (final output only).",
    )
    ap.add_argument(
        "--anti-max-by-distribution-strength",
        type=float,
        default=0.0,
        help="Bias sampling so higher-distribution symbols are less likely to be the max-weight "
        "position in each sample. 0 disables (default). Typical range: 0.5 to 2.0.",
    )
    ap.add_argument(
        "--max-weight",
        type=float,
        default=0.10,
        help="Max allocation to any single symbol as a fraction (default 0.10 == 10%%).",
    )
    ap.add_argument(
        "--objective",
        choices=["pareto", "score"],
        default="pareto",
        help="pareto prints the frontier; score ranks by a combined score.",
    )
    ap.add_argument(
        "--distribution-metric",
        choices=["avg_cash", "end_cash", "avg_gross", "end_gross"],
        default="avg_cash",
        help="Which distribution metric to optimize/report (default avg_cash).",
    )
    ap.add_argument(
        "--lambda",
        dest="lambda_",
        type=float,
        default=1.0,
        help="If --objective=score: score = normalized_end_value + lambda * normalized_distribution (default 1.0).",
    )
    ap.add_argument("--top", type=int, default=15, help="How many allocations to print (default 15).")
    ap.add_argument("--json", action="store_true", help="Output JSON instead of text.")
    ap.add_argument(
        "--keep-all",
        action="store_true",
        help="Store all samples in memory (dangerous for large --samples). Default keeps bounded memory.",
    )
    ap.add_argument(
        "--spill-jsonl",
        type=str,
        default="",
        help="If set, stream each sample as JSONL to this path (enables --objective=score without huge RAM).",
    )
    ap.add_argument(
        "--frontier-cap",
        type=int,
        default=5000,
        help="Approximate Pareto frontier working-set cap (default 5000).",
    )
    args = ap.parse_args()

    cfg = Path(str(args.config)).expanduser()
    if not cfg.is_absolute():
        cfg = (script_env.repo_root() / cfg).resolve()
    symbols = _load_symbols_from_positions_yaml(cfg)
    if not symbols:
        raise SystemExit(f"no symbols found in {cfg}")

    as_of = date.today()
    params = _load_symbol_params(
        symbols,
        as_of=as_of,
        price_growth_rate=args.price_growth_rate,
        infer_monthly_growth_rate=bool(args.infer_monthly_growth_rate),
        lookback_months=int(args.lookback_months),
        price_series=str(args.price_series),
    )

    rng = random.Random(int(args.seed))
    n = len(params)
    samples = max(1, int(args.samples))
    alpha = float(args.alpha)
    progress_every = max(0, int(args.progress_every))
    anti_max_strength = float(args.anti_max_by_distribution_strength or 0.0)
    max_weight = float(args.max_weight)
    monthly_dist_per_share = [
        float(p.per_share_distribution) * float(p.events_per_month) for p in params
    ]

    dist_key = {
        "avg_cash": "avg_monthly_distribution_cash",
        "end_cash": "end_monthly_distribution_cash",
        "avg_gross": "avg_monthly_distribution_gross",
        "end_gross": "end_monthly_distribution_gross",
    }[str(args.distribution_metric)]

    top_n = max(1, int(args.top))
    keep_all = bool(args.keep_all)
    spill_path = str(args.spill_jsonl or "").strip()
    spill_fh = None
    if spill_path:
        p = Path(spill_path).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        spill_fh = p.open("w", encoding="utf-8")

    results: list[dict[str, Any]] = []  # only populated when --keep-all
    top_by_value_h: list[tuple[float, int, dict[str, Any]]] = []
    top_by_dist_h: list[tuple[float, int, dict[str, Any]]] = []
    frontier_ws: list[dict[str, Any]] = []

    # For --objective=score normalization.
    v_min = float("inf")
    v_max = float("-inf")
    d_min = float("inf")
    d_max = float("-inf")
    started = perf_counter()
    best_by_value: dict[str, Any] | None = None
    best_by_dist: dict[str, Any] | None = None
    for i in range(1, samples + 1):
        w = _dirichlet_weights(rng, n, alpha=alpha)
        if anti_max_strength > 0:
            w = _apply_anti_max_distribution_bias(
                w,
                monthly_dist_per_share=monthly_dist_per_share,
                strength=anti_max_strength,
            )
        if max_weight < 1.0:
            w = _cap_weights(w, max_weight=max_weight)
        r = _simulate_portfolio(
            params,
            w,
            budget=float(args.budget),
            months=int(args.months),
            drip_rate=float(args.drip_rate),
        )
        ev = float(r.get("ending_value") or 0.0)
        dm = float(r.get(dist_key) or 0.0)

        v_min = min(v_min, ev)
        v_max = max(v_max, ev)
        d_min = min(d_min, dm)
        d_max = max(d_max, dm)

        if keep_all:
            results.append(r)
        if spill_fh is not None:
            spill_fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

        _topk_push(top_by_value_h, r, score=ev, k=top_n, seq=i)
        _topk_push(top_by_dist_h, r, score=dm, k=top_n, seq=i)
        if str(args.objective) != "score":
            _pareto_frontier_update(
                frontier_ws,
                r,
                x="ending_value",
                y=dist_key,
                cap=max(0, int(args.frontier_cap)),
            )

        if best_by_value is None or ev > float(best_by_value.get("ending_value") or 0.0):
            best_by_value = r
        if best_by_dist is None or dm > float(best_by_dist.get(dist_key) or 0.0):
            best_by_dist = r

        if not args.quiet and progress_every and (i % progress_every == 0 or i == samples):
            elapsed = perf_counter() - started
            rate = (i / elapsed) if elapsed > 0 else 0.0
            eta = ((samples - i) / rate) if rate > 0 else 0.0
            bv = float((best_by_value or {}).get("ending_value") or 0.0)
            bd = float((best_by_dist or {}).get(dist_key) or 0.0)
            print(
                f"[progress] {i:,}/{samples:,} samples  "
                f"elapsed={elapsed:,.1f}s  eta={eta:,.1f}s  "
                f"best_value=${bv:,.0f}  best_{args.distribution_metric}=${bd:,.0f}/mo",
                flush=True,
            )

    if spill_fh is not None:
        spill_fh.flush()
        spill_fh.close()

    # Always compute these helpful ranks (bounded memory).
    by_value = _topk_items(top_by_value_h)[:top_n]
    by_dist = _topk_items(top_by_dist_h)[:top_n]

    lam = float(args.lambda_)

    def _norm(x: float, lo: float, hi: float) -> float:
        if hi <= lo:
            return 0.0
        return (x - lo) / (hi - lo)

    def _score_for(row: dict[str, Any]) -> float:
        ev = float(row.get("ending_value") or 0.0)
        dm = float(row.get(dist_key) or 0.0)
        nv = _norm(ev, v_min, v_max)
        nd = _norm(dm, d_min, d_max)
        return nv + lam * nd

    def _finalize_row(row: dict[str, Any]) -> dict[str, Any]:
        """
        Output shape:
        - score first
        - weights as percent only (weights_pct)
        - dollars as starting dollars only (starting_dollars)
        """
        w = row.get("weights") or {}
        weights_pct: dict[str, float] = {}
        if isinstance(w, dict):
            weights_pct = {str(k): float(v) * 100.0 for k, v in w.items()}

        out: dict[str, Any] = {
            "score": float(row.get("score") if "score" in row else _score_for(row)),
            "ending_value": float(row.get("ending_value") or 0.0),
            "end_monthly_distribution_gross": float(row.get("end_monthly_distribution_gross") or 0.0),
            "avg_monthly_distribution_gross": float(row.get("avg_monthly_distribution_gross") or 0.0),
            "end_monthly_distribution_cash": float(row.get("end_monthly_distribution_cash") or 0.0),
            "avg_monthly_distribution_cash": float(row.get("avg_monthly_distribution_cash") or 0.0),
            "weights_pct": weights_pct,
            "starting_dollars": row.get("starting_dollars") or {},
        }
        return out

    payload: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "config": str(cfg),
        "symbols": [p.symbol for p in params],
        "budget": float(args.budget),
        "months": int(args.months),
        "price_growth_rate": float(args.price_growth_rate) if args.price_growth_rate is not None else 0.0,
        "infer_monthly_growth_rate": bool(args.infer_monthly_growth_rate),
        "lookback_months": int(args.lookback_months),
        "price_series": str(args.price_series),
        "drip_rate": float(args.drip_rate),
        "samples": samples,
        "alpha": alpha,
        "anti_max_by_distribution_strength": anti_max_strength,
        "max_weight": max_weight,
        "distribution_metric": str(args.distribution_metric),
        "symbol_params": [
            {
                "symbol": p.symbol,
                "price": p.price,
                "monthly_price_growth_rate": float(p.monthly_price_growth_rate),
                "per_share_distribution": p.per_share_distribution,
                "events_per_month_expected": p.events_per_month,
                "expected_monthly_distribution_per_share": round(
                    float(p.per_share_distribution) * float(p.events_per_month), 8
                ),
                "cadence_days": p.cadence_days,
                "payout_frequency": p.payout_frequency,
                "source": p.source,
            }
            for p in params
        ],
        "top_by_ending_value": [_finalize_row(r) for r in by_value],
        "top_by_distribution_metric": [_finalize_row(r) for r in by_dist],
    }

    if str(args.objective) == "score":
        top_by_score_h: list[tuple[float, int, dict[str, Any]]] = []
        if keep_all:
            for j, r in enumerate(results, start=1):
                s = _score_for(r)
                rr = dict(r)
                rr["score"] = s
                _topk_push(top_by_score_h, rr, score=float(s), k=top_n, seq=j)
        elif spill_path:
            p = Path(spill_path).expanduser()
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            with p.open("r", encoding="utf-8") as fh:
                for j, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    s = _score_for(r)
                    r["score"] = s
                    _topk_push(top_by_score_h, r, score=float(s), k=top_n, seq=j)
        else:
            raise SystemExit(
                "objective=score without --keep-all needs --spill-jsonl to avoid storing all samples in RAM"
            )

        payload["top_by_score"] = [_finalize_row(r) for r in _topk_items(top_by_score_h)[:top_n]]
    else:
        frontier = _pareto_frontier(frontier_ws, x="ending_value", y=dist_key)
        payload["pareto_frontier"] = [_finalize_row(r) for r in frontier[: max(1, int(args.top))]]

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0

    def _fmt_alloc(weights_pct: dict[str, float], *, k: int = 6) -> str:
        items = sorted(weights_pct.items(), key=lambda kv: -kv[1])[:k]
        return ", ".join([f"{s}={w:.1f}%" for s, w in items])

    print(f"symbols={len(params)}  budget=${float(args.budget):,.0f}  months={int(args.months)}  drip={float(args.drip_rate):.2f}")
    print(f"distribution_metric={args.distribution_metric}  samples={samples}  alpha={alpha}  objective={args.objective}")
    print()

    def _print_block(title: str, rows: list[dict[str, Any]]) -> None:
        print(title)
        for i, r in enumerate(rows, start=1):
            ev = float(r.get("ending_value") or 0.0)
            dm = float(r.get(dist_key) or 0.0)
            sc = float(r.get("score") or 0.0)
            print(
                f"{i:>2}. score={sc:.4f}  ending_value=${ev:,.0f}  {args.distribution_metric}=${dm:,.0f}/mo  "
                f"top={_fmt_alloc(r.get('weights_pct') or {})}"
            )
        print()

    _print_block("Top by ending value:", list(payload.get("top_by_ending_value") or []))
    _print_block(f"Top by {args.distribution_metric}:", list(payload.get("top_by_distribution_metric") or []))
    if str(args.objective) == "score":
        _print_block("Top by score:", list(payload.get("top_by_score") or []))
    else:
        _print_block("Pareto frontier (value vs distribution):", list(payload.get("pareto_frontier") or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

