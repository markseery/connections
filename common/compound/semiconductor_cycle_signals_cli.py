#!/usr/bin/env python3
"""
Early-warning scan for semiconductor cycle stress (web research + aiserver synthesis).

Uses the same building blocks as other repo CLIs:
  - Worker ``websearch_skill`` (Google CSE / DDG + page extract + optional aiserver summary)
  - Registry discovery for worker + ``common.compound.aiserver_discovery`` for aiserver
  - ``AiserverGenerateClient`` POST /generate with configurable profile (strength), e.g.
    ``fast``, ``chat``, ``reason``, ``agent`` — same idea as ``--ai-strength`` in
    ``application_files/scripts/webscrape_site_facts.py``.

The script pulls **quantitative hooks** first (see ``finance_pipeline/semiconductor_cycle_metrics.py``):
optional **FRED** series (``FRED_API_KEY``), **Yahoo Finance** inventory / DIO-style proxies and
named peer **equity** momentum (ARM, AVGO, MU, SNDK, INTC, AMD, ASML, TSM, NVDA, LRCX — not ETFs), and a **DRAMeXchange** DRAM spot table scrape.
GPU / cloud **pricing** snapshots are intentionally omitted (incomplete vs paywalled sources and distorting).
It then maps five watch areas to websearch queries and
asks the AI server to merge **numbers + news** into one JSON object. Not investment advice.

Examples:
  python3 scripts/run_semiconductor_cycle_signals.py --segment memory --profile reason
  # With FRED_API_KEY in ``.env`` (repo root or application_files/.env); see load_connections_dotenv.
  python3 scripts/run_semiconductor_cycle_signals.py --profile agent --out sem_cycle_scan.json
  python3 scripts/run_semiconductor_cycle_signals.py --worker-url http://127.0.0.1:7010 --verbose
  python3 scripts/run_semiconductor_cycle_signals.py --skip-metrics
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from common.simple import script_env

from common.compound.aiserver_discovery import get_aiserver_base_url
from common.compound.aiserver_generate_client import AiserverGenerateClient
from common.simple.user_dir import load_connections_dotenv
from common.compound.finance_pipeline.semiconductor_cycle_metrics import (
    DEFAULT_INVENTORY_TICKERS,
    collect_cycle_metrics,
)

_WORKER_NAMES = ("worker-1", "worker-2", "worker")
DEFAULT_REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
AI_PROFILE_CHOICES = ("fast", "chat", "reason", "agent")
SEARCH_TIMEOUT = 120.0
AI_TIMEOUT = 300.0

# Stripped from ``collect_cycle_metrics`` output so HARD_DATA does not bias synthesis with partial GPU pricing.
_GPU_PRICING_HARD_DATA_KEYS = frozenset(
    {
        "gpu_pricing_industry_sources",
        "gpu_pricing_data_strategy",
        "semi_analysis_page_snapshot",
        "semi_analysis_public_web_context",
        "gpu_cloud_spot",
    }
)


def _strip_gpu_pricing_from_hard_data(data: dict[str, Any]) -> None:
    for k in _GPU_PRICING_HARD_DATA_KEYS:
        data.pop(k, None)


@dataclass(frozen=True)
class SignalQuery:
    signal_id: str
    title: str
    query: str


def _find_worker(registry_url: str) -> str:
    for name in _WORKER_NAMES:
        try:
            r = httpx.get(f"{registry_url}/servers/{name}", timeout=5.0)
            if r.status_code != 200:
                continue
            url = (r.json() or {}).get("url", "").strip().rstrip("/")
            if not url:
                continue
            h = httpx.get(f"{url}/health", timeout=3.0)
            if h.status_code == 200:
                return url
        except Exception:
            continue
    raise RuntimeError(
        f"No live worker in registry {registry_url!r} (tried {_WORKER_NAMES}). "
        "Start a worker or pass --worker-url."
    )


def _segment_suffix(segment: str) -> str:
    if segment == "ai_accelerators":
        return "GPU AI accelerator HBM data center hyperscale capex"
    if segment == "memory":
        return "DRAM NAND HBM flash memory spot price inventory"
    if segment == "equipment":
        return "semiconductor wafer fab equipment WFE lithography bookings backlog"
    if segment == "foundry_broad":
        return "foundry logic mature node automotive industrial MCU"
    return "semiconductor industry broad market"


def _signal_queries(segment: str) -> list[SignalQuery]:
    tail = _segment_suffix(segment)
    return [
        SignalQuery(
            "lead_times_panic_buying",
            "Lead times & panic buying / double ordering",
            f"semiconductor component lead times 2026 extended backlog double order "
            f"inventory front-load phantom demand {tail}",
        ),
        SignalQuery(
            "book_to_bill",
            "Equipment billings & orders vs billings",
            f"SEMI North America semiconductor equipment billings three month moving average YoY 2026 "
            f"SEMI WWSEMS equipment bookings vs billings WSTS silicon cycle book-to-bill {tail}",
        ),
        SignalQuery(
            "training_vs_inference",
            "AI training vs inference mix & margins",
            f"AI accelerator training vs inference workload mix margin pressure "
            f"2026 data center capex efficiency hyperscale {tail}",
        ),
        SignalQuery(
            "inventory_asp",
            "Days of inventory & memory / chip ASPs",
            f"semiconductor days inventory rising tech OEM balance sheet 2026 "
            f"memory DRAM NAND ASP decline oversupply {tail}",
        ),
        SignalQuery(
            "wafer_fab_equipment",
            "Wafer fab equipment orders (3–6 month lead)",
            f"semiconductor equipment orders bookings 2026 AMAT LRCX KLAC Tokyo Electron "
            f"outlook slowdown capex push-out {tail}",
        ),
    ]


def _websearch(worker_url: str, query: str, limit: int) -> dict[str, Any]:
    url = f"{worker_url.rstrip('/')}/skills/websearch_skill/search"
    with httpx.Client(timeout=SEARCH_TIMEOUT) as client:
        r = client.post(url, json={"query": query, "limit": limit})
        r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {"summary": str(data), "items": []}


def _strip_json_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = _strip_json_fences(text)
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                chunk = cleaned[start : i + 1]
                try:
                    obj = json.loads(chunk)
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _build_synthesis_prompt(
    *,
    segment: str,
    research_blocks: list[tuple[str, str, str]],
    hard_data: dict[str, Any],
) -> str:
    framework = """Framework (early-cycle warning lenses):
1) Extreme lead times can coincide with panic buying / double ordering → later glut risk.
2) Book-to-bill sustained < 1.0 → production outpacing new orders.
3) Shift from training-class accelerators toward more commoditized inference can pressure margins.
4) Rising customer / OEM days-of-inventory plus falling ASPs (especially memory) → supply/demand imbalance.
5) Wafer fab equipment orders often lead the broader chip market by roughly 3–6 months."""

    blocks_txt = ""
    for signal_id, title, summary in research_blocks:
        blocks_txt += f"\n### [{signal_id}] {title}\n{summary.strip()}\n"

    hard_json = json.dumps(hard_data, ensure_ascii=False, indent=2)

    return f"""You are a research assistant (not a financial adviser). Today (authoritative clock): {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}.

User segment focus: {segment!r}

{framework}

--- HARD_DATA (quantitative; JSON) ---
The next block is machine-gathered from FRED (if keyed), Yahoo Finance, DRAMeXchange HTML,
and book-to-bill context pointers. **GPU / cloud rental pricing fields are intentionally omitted** from this run
(incomplete or paywalled sources); do not infer dollar GPU rents from HARD_DATA.
Peer momentum lives under ``yahoo_peer_momentum`` (same default equity list as inventory, not SOXX/SMH).
Treat numeric fields as facts **as-of scrape/download time**; they may lag filings or be incomplete.
Respect in-json notes (e.g. SEMI book-to-bill discontinuation, DIO proxy definitions).

{hard_json}
--- End HARD_DATA ---

Below are web-search summaries (qualitative / news). Cross-check against HARD_DATA where possible.

--- Web research ---
{blocks_txt}
--- End web research ---

Return ONLY valid JSON (no markdown fences) with this exact shape:
{{
  "as_of_utc": "{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}",
  "segment_focus": "{segment}",
  "data_snapshot": {{
    "fred_latest": [{{"series_id": "…", "label": "…", "latest_date": "…", "latest_value": "…"}}],
    "memory_spot_highlights": [{{"product": "…", "session_average_usd": null, "session_change_pct": null}}],
    "inventory_trend_highlights": [{{"symbol": "…", "latest_inventory_usd": null, "latest_inventory_days_proxy": null, "period_end": "…"}}],
    "peer_momentum_pct": [{{"symbol": "…", "1 month": null, "3 month": null, "6 month": null}}]
  }},
  "data_points_used": [
    {{
      "ref": "hard_data.path.or.web",
      "fact": "short text with numbers copied from HARD_DATA or web research only",
      "signal_ids": ["inventory_asp", "…"]
    }}
  ],
  "overall_cycle_read": "expansion|late_cycle|turning|uncertain",
  "overall_rationale": "2-4 sentences; cite at least one quantitative fact where possible",
  "signals": [
    {{
      "id": "lead_times_panic_buying|book_to_bill|training_vs_inference|inventory_asp|wafer_fab_equipment",
      "title": "short label",
      "status": "neutral|watch|warning|insufficient_data",
      "confidence": "low|medium|high",
      "summary": "2-4 sentences",
      "evidence_bullets": ["…"],
      "contrary_evidence_bullets": ["optional"]
    }}
  ],
  "macro_geopolitical_watch": ["…"],
  "blind_spots": ["…"],
  "next_monitoring_steps": ["…"]
}}

Rules:
- Emit exactly five signal objects, one per id above (same ids as the research sections).
- **Numbers**: You may quote values **verbatim** from HARD_DATA JSON or from web research summaries. Do not fabricate statistics.
- Fill ``data_snapshot`` by summarizing HARD_DATA (use null where a slice is missing). Keep arrays short (top 3–6 rows each).
- Copy ``peer_momentum_pct`` in ``data_snapshot`` from HARD_DATA.yahoo_peer_momentum.peer_momentum_pct (named semiconductor peers, not ETFs).
- Do **not** invent GPU cloud $/hr, SemiAnalysis index numbers, or third-party rental tables; HARD_DATA for this run excludes GPU pricing snapshots by design.
- For ``training_vs_inference``: rely on **web research** plus non-pricing HARD_DATA (inventory, DRAM spot, peer momentum, FRED) — not dollar GPU rent quotes.
- ``data_points_used`` must list every quantitative fact you rely on in ``overall_rationale`` or ``signals`` (minimum 3 entries when HARD_DATA has any numbers).
- If HARD_DATA and web research conflict, say so in ``blind_spots`` and lower confidence.
"""


def main() -> int:
    load_connections_dotenv()
    ap = argparse.ArgumentParser(
        description="Semiconductor cycle early-warning scan (websearch skill + aiserver JSON synthesis).",
    )
    ap.add_argument(
        "--segment",
        choices=("all", "ai_accelerators", "memory", "equipment", "foundry_broad"),
        default="all",
        help="Tail search queries toward a slice of the market (default: all).",
    )
    ap.add_argument(
        "--profile",
        choices=AI_PROFILE_CHOICES,
        default="reason",
        metavar="STRENGTH",
        help=f"Aiserver profile / strength (default: reason). Choices: {', '.join(AI_PROFILE_CHOICES)}.",
    )
    ap.add_argument(
        "--search-limit",
        type=int,
        default=5,
        metavar="N",
        help="Max results per websearch call (default: 5).",
    )
    ap.add_argument(
        "--registry-url",
        default=DEFAULT_REGISTRY_URL,
        help="Registry URL for worker discovery (default: REGISTRY_SERVER_URL or 127.0.0.1:7002).",
    )
    ap.add_argument(
        "--worker-url",
        default=None,
        help="Skip registry; call websearch on this worker base URL.",
    )
    ap.add_argument(
        "--aiserver-url",
        default=None,
        help="Override aiserver base URL (default: registry via get_aiserver_base_url).",
    )
    ap.add_argument(
        "--provider",
        default=None,
        help="Optional aiserver provider override (same as ask_ai --provider).",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory for the full run record JSON when using a relative ``--out`` name, "
            "or for the default filename (default: application_files/data/semiconductor_cycle)."
        ),
    )
    ap.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help=(
            "Full run record (research + raw AI + parsed JSON if any). "
            "Relative paths are under --out-dir. Default: semiconductor_cycle_<segment>.json there."
        ),
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Extra progress: print full websearch query strings.",
    )
    ap.add_argument(
        "--skip-metrics",
        action="store_true",
        help="Skip FRED/Yahoo/DRAM quantitative collection (websearch + AI only).",
    )
    ap.add_argument(
        "--no-dram-scrape",
        action="store_true",
        help="Skip DRAMeXchange spot HTML fetch.",
    )
    ap.add_argument(
        "--fred-api-key",
        default=None,
        metavar="KEY",
        help="Override FRED_API_KEY env for St. Louis Fed observations.",
    )
    ap.add_argument(
        "--inventory-tickers",
        default=None,
        metavar="LIST",
        help=f"Comma-separated symbols for Yahoo inventory/DIO proxy (default: {','.join(DEFAULT_INVENTORY_TICKERS)}).",
    )
    args = ap.parse_args()

    segment = args.segment
    if segment == "all":
        segment = "all (broad semiconductor)"

    print(
        f"[sem-cycle] segment={args.segment!r} profile={args.profile!r} "
        f"search_limit={args.search_limit}",
        flush=True,
    )

    hard_data: dict[str, Any]
    if args.skip_metrics:
        hard_data = {
            "skipped": True,
            "reason": "--skip-metrics: no quantitative collection.",
        }
        print("[sem-cycle] quantitative: skipped (--skip-metrics)", flush=True)
    else:
        print(
            "[sem-cycle] quantitative: fetching FRED / Yahoo / DRAM spot …",
            flush=True,
        )
        inv_tick: tuple[str, ...] | None = None
        if args.inventory_tickers:
            inv_tick = tuple(
                s.strip().upper()
                for s in args.inventory_tickers.split(",")
                if s.strip()
            )
            if not inv_tick:
                inv_tick = None
        hard_data = collect_cycle_metrics(
            fred_api_key=args.fred_api_key,
            inventory_tickers=inv_tick,
            include_dram_spot=not args.no_dram_scrape,
            include_vast=False,
            include_semianalysis_html=False,
        )
        fred = hard_data.get("fred") or {}
        if fred.get("available"):
            print("[sem-cycle] quantitative: FRED series fetched.", flush=True)
        else:
            print(
                f"[sem-cycle] quantitative: FRED skipped ({fred.get('reason', 'n/a')[:100]})",
                flush=True,
            )
        yinv = hard_data.get("yahoo_inventory_quarters") or {}
        nrows = len(yinv.get("rows") or [])
        print(f"[sem-cycle] quantitative: Yahoo inventory rows={nrows}", flush=True)
        if not args.no_dram_scrape:
            dsp = hard_data.get("dram_spot") or {}
            nsp = len(dsp.get("samples") or [])
            if dsp.get("error"):
                print(f"[sem-cycle] quantitative: DRAM spot failed ({dsp.get('error')})", flush=True)
            else:
                print(f"[sem-cycle] quantitative: DRAM spot samples={nsp}", flush=True)

    _strip_gpu_pricing_from_hard_data(hard_data)
    print("[sem-cycle] HARD_DATA: GPU pricing / SemiAnalysis snapshot fields stripped.", flush=True)

    if args.worker_url:
        worker_url = args.worker_url.rstrip("/")
        print(f"[sem-cycle] using worker URL from --worker-url: {worker_url}", flush=True)
    else:
        print(
            f"[sem-cycle] discovering worker via registry {args.registry_url!r} …",
            flush=True,
        )
        try:
            worker_url = _find_worker(args.registry_url).rstrip("/")
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"[sem-cycle] worker: {worker_url}", flush=True)

    queries = _signal_queries("all" if args.segment == "all" else args.segment)
    nq = len(queries)
    print(f"[sem-cycle] running {nq} websearch calls on worker …", flush=True)
    research: list[dict[str, Any]] = []
    blocks: list[tuple[str, str, str]] = []

    for i, sq in enumerate(queries, start=1):
        print(
            f"[sem-cycle] [{i}/{nq}] websearch: {sq.title} ({sq.signal_id}) …",
            flush=True,
        )
        if args.verbose:
            print(f"[sem-cycle]   query: {sq.query}", flush=True)
        try:
            payload = _websearch(worker_url, sq.query, max(1, min(10, args.search_limit)))
        except Exception as exc:
            err = f"(websearch failed: {exc})"
            research.append(
                {"signal_id": sq.signal_id, "title": sq.title, "error": str(exc), "summary": err}
            )
            blocks.append((sq.signal_id, sq.title, err))
            print(f"Warning: websearch failed for {sq.signal_id}: {exc}", file=sys.stderr)
            print(f"[sem-cycle] [{i}/{nq}] websearch: failed ({exc})", flush=True)
            continue

        summary = str(payload.get("summary") or payload.get("text") or "").strip()
        research.append(
            {
                "signal_id": sq.signal_id,
                "title": sq.title,
                "query": sq.query,
                "summary": summary,
                "source": payload.get("source"),
                "count": payload.get("count"),
            }
        )
        blocks.append((sq.signal_id, sq.title, summary or "(empty summary)"))
        src = payload.get("source") or "?"
        cnt = payload.get("count")
        cnt_s = f"{cnt}" if cnt is not None else "?"
        print(
            f"[sem-cycle] [{i}/{nq}] websearch: done (source={src}, pages={cnt_s}, "
            f"summary_chars={len(summary)})",
            flush=True,
        )

    print("[sem-cycle] resolving aiserver URL …", flush=True)
    aiserver = (args.aiserver_url or get_aiserver_base_url(registry_override=args.registry_url)).rstrip(
        "/"
    )
    print(f"[sem-cycle] aiserver: {aiserver}", flush=True)
    prov = args.provider or "(server default)"
    print(
        f"[sem-cycle] calling POST /generate (profile={args.profile!r}, provider={prov}) …",
        flush=True,
    )
    prompt = _build_synthesis_prompt(segment=segment, research_blocks=blocks, hard_data=hard_data)
    client = AiserverGenerateClient(aiserver, timeout_sec=AI_TIMEOUT)
    try:
        raw = client.generate(prompt=prompt, profile=args.profile, provider=args.provider)
    except Exception as exc:
        print(f"Error: aiserver /generate failed: {exc}", file=sys.stderr)
        return 1

    text = AiserverGenerateClient.output_text(raw)
    print(
        f"[sem-cycle] aiserver: response received (output_chars={len(text)})",
        flush=True,
    )
    parsed = _extract_json_object(text)
    if parsed is not None:
        print("[sem-cycle] parsed structured JSON from model output.", flush=True)
    else:
        print(
            "[sem-cycle] could not parse JSON; printing raw model output below.",
            flush=True,
        )

    record: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "segment": args.segment,
        "profile": args.profile,
        "worker_url": worker_url,
        "aiserver_url": aiserver,
        "hard_data": hard_data,
        "research": research,
        "synthesis_raw_text": text,
        "synthesis_json": parsed,
    }

    out_dir = script_env.resolve_output_dir(args.out_dir, segment="semiconductor_cycle")
    if args.out:
        op = Path(args.out).expanduser()
        full_record_path = op if op.is_absolute() else (out_dir / op)
    else:
        safe_seg = re.sub(r"[^0-9A-Za-z_-]+", "_", str(args.segment)).strip("_") or "run"
        full_record_path = out_dir / f"semiconductor_cycle_{safe_seg}.json"
    full_record_path.parent.mkdir(parents=True, exist_ok=True)

    print("[sem-cycle] emitting result to stdout …", flush=True)
    if parsed is not None:
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    else:
        print(text)
        print(
            "\nWarning: could not parse JSON from model output; see synthesis_raw_text in the run record file.",
            file=sys.stderr,
        )

    print(f"[sem-cycle] writing full run record to {full_record_path!r} …", flush=True)
    try:
        with open(full_record_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        print(
            f"[sem-cycle] wrote {full_record_path!r} ({os.path.getsize(full_record_path):,} bytes)",
            flush=True,
        )
    except Exception as exc:
        print(f"Error writing run record file: {exc}", file=sys.stderr)
        return 1

    print("[sem-cycle] done.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
