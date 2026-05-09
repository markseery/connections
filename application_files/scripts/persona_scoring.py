#!/usr/bin/env python3
"""
Score website content against personas using adaptive batching.

Reads persona definitions from .docx files in
application_files/data/context/personas/, fetches batched website content
via the batch recommender and batcher skills, and asks an AI to score
each batch's relevance to each persona on a 0–100 scale.

Outputs a Markdown report with per-batch scores, per-persona summaries,
and an aggregate relevance matrix.

Usage:
  python3 persona_scoring.py https://coreweave.com
  python3 persona_scoring.py https://nebius.com --namespace webscrape
  python3 persona_scoring.py https://coreweave.com --ai-profile agent --context-pct 0.70
  python3 persona_scoring.py https://coreweave.com --out my_scores.md

Requires: registry, worker (batch_recommender_skill, batcher_skill), aiserver,
          storage with previously-scraped data, .docx persona files.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from docx import Document

APP_ROOT = Path(__file__).resolve().parents[1]
PERSONAS_DIR = APP_ROOT / "data" / "context" / "personas"
REPORTS_DIR = APP_ROOT / "data" / "reports" / "persona_scoring"

DEFAULT_NAMESPACE = "webscrape"
DEFAULT_KEY_SEPARATOR = "\x00"
DEFAULT_AI_PROFILE = "agent"
DEFAULT_CONTEXT_PCT = 0.70
DEFAULT_CHARS_PER_TOKEN = 3.5
DEFAULT_AI_TIMEOUT = 660.0
DEFAULT_REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")


# ── persona loading ─────────────────────────────────────────────────────

def _read_docx(path: Path) -> str:
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _clean_persona_name(stem: str) -> str:
    """Extract a human-readable name from filenames like
    'Persona Profile_ Carter, CTO_Technical Buyer' → 'Carter, CTO / Technical Buyer'."""
    name = re.sub(r"^Persona\s+Profile[_ ]*", "", stem, flags=re.IGNORECASE).strip()
    name = name.replace("_", " / ").strip(" /")
    return name or stem


def _load_personas(directory: Path) -> list[dict[str, str]]:
    personas: list[dict[str, str]] = []
    if not directory.is_dir():
        return personas
    for f in sorted(directory.glob("*.docx")):
        text = _read_docx(f)
        if text.strip():
            personas.append({
                "name": _clean_persona_name(f.stem),
                "file": f.name,
                "description": text,
            })
    return personas


# ── prompt template ──────────────────────────────────────────────────────

_SCORE_PROMPT = """\
You are evaluating website content for relevance to specific buyer/user personas.

For EACH persona below, score the following website content on a scale of
0 to 100, where:
  0  = completely irrelevant to this persona
  50 = moderately relevant, some useful information
  100 = extremely relevant, directly addresses this persona's needs

Also provide a 1-2 sentence justification for each score.

Return your answer as valid JSON — an array of objects, one per persona:
[
  {{"persona": "<name>", "score": <int 0-100>, "justification": "<1-2 sentences>"}},
  ...
]

Return ONLY the JSON array, no other text.

## Personas

{personas_block}

## Website Content ({batch_label})

{content}
"""


# ── service discovery ────────────────────────────────────────────────────

def _find_worker(registry_url: str) -> str:
    try:
        r = httpx.get(f"{registry_url}/servers", timeout=5.0)
        r.raise_for_status()
        servers = r.json().get("servers") or []
    except Exception:
        servers = []

    candidates: list[tuple[str, int]] = []
    for srv in servers:
        name = srv.get("name", "")
        if not name.startswith("worker"):
            continue
        url = srv.get("url", "").rstrip("/")
        if not url:
            continue
        try:
            resp = httpx.get(f"{url}/health", timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                active = int(data.get("active_requests", 0))
                candidates.append((url, active))
        except Exception:
            continue

    if not candidates:
        raise RuntimeError("No live worker found in registry")

    idle = [url for url, active in candidates if active == 0]
    chosen = random.choice(idle) if idle else min(candidates, key=lambda x: x[1])[0]
    return chosen


def _get_aiserver_url(registry_url: str) -> str:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{registry_url}/servers/aiserver")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError("Registry missing url for aiserver")
        return str(url).rstrip("/")


# ── skill clients ────────────────────────────────────────────────────────

def _get_batch_plan(
    worker_url: str, namespace: str, prefix: str,
    ai_profile: str, context_pct: float, chars_per_token: float,
    prompt_overhead: int,
) -> dict[str, Any]:
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"{worker_url}/skills/batch_recommender_skill/recommend",
            json={
                "namespace": namespace,
                "prefix": prefix,
                "ai_profile": ai_profile,
                "context_pct": context_pct,
                "chars_per_token": chars_per_token,
                "prompt_overhead": prompt_overhead,
            },
        )
        r.raise_for_status()
    return r.json()


def _batcher_batch(
    worker_url: str, namespace: str, prefix: str, offset: int, limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"{worker_url}/skills/batcher_skill/batch",
            json={"namespace": namespace, "prefix": prefix, "offset": offset, "limit": limit},
        )
        r.raise_for_status()
    resp = r.json()
    items = resp.get("items", [])
    has_more = resp.get("data", {}).get("has_more", False)
    return items, has_more


def _ai_generate(aiserver_url: str, prompt: str, ai_profile: str, timeout: float) -> str:
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            f"{aiserver_url}/generate",
            json={"prompt": prompt, "profile": ai_profile},
        )
        r.raise_for_status()
    out = r.json()
    output = out.get("output") if isinstance(out.get("output"), dict) else out
    if isinstance(output, dict) and "text" in output:
        return str(output["text"]).strip()
    return str(output).strip() if output else ""


# ── helpers ──────────────────────────────────────────────────────────────

def _canonical_sitename(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = (parsed.hostname or url).lower().rstrip("/")
    return f"{scheme}://{host}"


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s" if m else f"{s}s"


def _parse_scores(response: str, persona_names: list[str]) -> list[dict[str, Any]]:
    """Extract the JSON scores array from the AI response."""
    text = response.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    results: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("persona", "")).strip()
        score = item.get("score")
        justification = str(item.get("justification", "")).strip()
        if isinstance(score, (int, float)) and 0 <= score <= 100:
            results.append({"persona": name, "score": int(score), "justification": justification})
    return results


def _match_persona(returned_name: str, persona_names: list[str]) -> str | None:
    """Fuzzy-match an AI-returned persona name to the canonical list."""
    rn = returned_name.lower().strip()
    for pname in persona_names:
        if pname.lower() == rn:
            return pname
    for pname in persona_names:
        if pname.lower() in rn or rn in pname.lower():
            return pname
    for pname in persona_names:
        first_word = pname.split(",")[0].strip().split()[-1].lower()
        if first_word and first_word in rn:
            return pname
    return None


# ── main ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Score website content against personas")
    parser.add_argument("url", help="Website URL (must already be scraped)")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help=f"Storage namespace (default: {DEFAULT_NAMESPACE})")
    parser.add_argument("--ai-profile", default=DEFAULT_AI_PROFILE, help=f"AI profile (default: {DEFAULT_AI_PROFILE})")
    parser.add_argument("--ai-timeout", type=float, default=DEFAULT_AI_TIMEOUT, help="AI timeout in seconds")
    parser.add_argument("--context-pct", type=float, default=DEFAULT_CONTEXT_PCT, help="Context window usage fraction")
    parser.add_argument("--personas-dir", default=str(PERSONAS_DIR), help="Directory containing persona .docx files")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_URL, help="Registry URL")
    parser.add_argument("--runs", type=int, default=1, help="Number of scoring runs to average over (default: 1)")
    parser.add_argument("--out", default=None, help="Output report path")
    args = parser.parse_args()

    personas_dir = Path(args.personas_dir)
    personas = _load_personas(personas_dir)
    if not personas:
        print(f"Error: no .docx persona files found in {personas_dir}", file=sys.stderr)
        return 1

    persona_names = [p["name"] for p in personas]
    print(f"Loaded {len(personas)} persona(s): {', '.join(persona_names)}")

    url = args.url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    sitename = _canonical_sitename(url)
    namespace = args.namespace
    context_pct = max(0.1, min(0.95, args.context_pct))

    print(f"Site: {sitename}")
    print(f"Namespace: {namespace}")

    registry_url = args.registry.rstrip("/")
    try:
        worker_url = _find_worker(registry_url)
        aiserver_url = _get_aiserver_url(registry_url)
    except Exception as e:
        print(f"Service discovery error: {e}", file=sys.stderr)
        return 1

    # ── build personas block for prompt ──
    personas_block = "\n\n".join(
        f"### {p['name']}\n{p['description']}" for p in personas
    )
    prompt_overhead = len(_SCORE_PROMPT) + len(personas_block) + 500

    # ── get batch plan ──
    prefix = sitename + DEFAULT_KEY_SEPARATOR
    print(f"\nRequesting batch plan ...", flush=True)
    try:
        plan = _get_batch_plan(
            worker_url, namespace, prefix, args.ai_profile,
            context_pct, DEFAULT_CHARS_PER_TOKEN, prompt_overhead,
        )
    except Exception as e:
        print(f"Batch recommender error: {e}", file=sys.stderr)
        return 1

    plan_data = plan.get("data", {})
    total_records = plan_data.get("total_records", 0)
    if total_records == 0:
        print(f"No stored content for {sitename} in namespace '{namespace}'.", file=sys.stderr)
        return 1

    model_name = plan_data.get("model", "unknown")
    content_budget = plan_data.get("content_budget", 100_000)
    batch_plan = plan_data.get("batches", [])
    n_batches = len(batch_plan)
    batches = [(b["start"], b["end"]) for b in batch_plan]

    n_runs = max(1, args.runs)

    print(f"Model: {model_name}")
    print(f"Batches: {n_batches} (budget: {content_budget:,} chars/batch)")
    print(f"Runs: {n_runs}")
    print(f"AI calls: {n_runs} × {n_batches} = {n_runs * n_batches}")

    # ── fetch all pages ──
    print(f"\nFetching {total_records} pages ...", flush=True)
    all_pages: list[dict[str, Any]] = []
    offset = 0
    while offset < total_records:
        items, has_more = _batcher_batch(worker_url, namespace, prefix, offset, 200)
        if not items:
            break
        all_pages.extend(items)
        offset += 200
        if not has_more:
            break
    print(f"  Retrieved {len(all_pages)} pages.", flush=True)

    if not all_pages:
        print("No page content retrieved.", file=sys.stderr)
        return 1

    page_sizes = [len(str(p.get("content", ""))) for p in all_pages]

    # ── pre-build batch content (reused across runs) ──
    batch_contents: list[tuple[int, int, int, int, str]] = []
    for batch_idx, (start, end) in enumerate(batches, 1):
        batch_chars = sum(page_sizes[start:end])
        batch_pages = all_pages[start:end]
        content_parts = [str(p.get("content", "")).strip() for p in batch_pages if str(p.get("content", "")).strip()]
        content = "\n\n---\n\n".join(content_parts)
        if content.strip():
            batch_contents.append((batch_idx, start, end, batch_chars, content))

    if not batch_contents:
        print("All batches are empty.", file=sys.stderr)
        return 1

    # ── score across multiple runs ──
    # all_run_scores[run_idx][batch_idx] = {persona: {score, justification}}
    all_run_scores: list[list[dict[str, Any]]] = []
    run_start = time.monotonic()
    total_ai_calls = 0

    for run_num in range(1, n_runs + 1):
        if n_runs > 1:
            print(f"\n{'─'*60}", flush=True)
            print(f"  Run {run_num}/{n_runs}", flush=True)
            print(f"{'─'*60}", flush=True)

        run_batch_scores: list[dict[str, Any]] = []

        for batch_idx, start, end, batch_chars, content in batch_contents:
            batch_label = f"batch {batch_idx}/{n_batches}, pages {start+1}–{end}"
            prompt = _SCORE_PROMPT.format(
                personas_block=personas_block,
                batch_label=batch_label,
                content=content,
            )

            run_label = f"run {run_num}/{n_runs} " if n_runs > 1 else ""
            print(f"\n  {run_label}Batch {batch_idx}/{n_batches} ({batch_chars:,} chars, pages {start+1}–{end}) ...",
                  end="", flush=True)
            t0 = time.monotonic()
            try:
                response = _ai_generate(aiserver_url, prompt, args.ai_profile, args.ai_timeout)
                scores = _parse_scores(response, persona_names)
                dur = time.monotonic() - t0
                print(f" done ({dur:.1f}s)", flush=True)
                total_ai_calls += 1
            except Exception as e:
                dur = time.monotonic() - t0
                print(f" error ({dur:.1f}s): {e}", flush=True)
                scores = []

            returned_names = [s["persona"] for s in scores]
            matched_by_name: dict[str, dict[str, Any]] = {}
            for s in scores:
                canon = _match_persona(s["persona"], persona_names)
                if canon:
                    matched_by_name[canon] = s

            batch_result: dict[str, Any] = {
                "batch": batch_idx, "start": start, "end": end,
                "chars": batch_chars, "run": run_num, "scores": {},
            }
            for pname in persona_names:
                matched = matched_by_name.get(pname)
                if matched:
                    batch_result["scores"][pname] = {
                        "score": matched["score"],
                        "justification": matched["justification"],
                    }
                    print(f"    {pname}: {matched['score']}/100", flush=True)
                else:
                    batch_result["scores"][pname] = {"score": 0, "justification": "No score returned"}
                    print(f"    {pname}: no score returned", flush=True)
                    print(f"      (AI returned {len(scores)} scores: {returned_names})", flush=True)

            run_batch_scores.append(batch_result)

        all_run_scores.append(run_batch_scores)

    total_elapsed = time.monotonic() - run_start

    # ── aggregate: average across runs per (batch, persona) ──
    # avg_batch_scores[batch_content_idx][persona] = {avg_score, min, max, all_scores, last_justification}
    avg_batch_scores: list[dict[str, Any]] = []
    for b_idx in range(len(batch_contents)):
        batch_idx, start, end, batch_chars, _ = batch_contents[b_idx]
        entry: dict[str, Any] = {
            "batch": batch_idx, "start": start, "end": end, "chars": batch_chars,
            "scores": {},
        }
        for pname in persona_names:
            run_scores_for_persona = []
            last_just = ""
            for run_batch_list in all_run_scores:
                if b_idx < len(run_batch_list):
                    s = run_batch_list[b_idx]["scores"].get(pname, {})
                    run_scores_for_persona.append(s.get("score", 0))
                    j = s.get("justification", "")
                    if j and j != "No score returned":
                        last_just = j
            if run_scores_for_persona:
                avg = sum(run_scores_for_persona) / len(run_scores_for_persona)
                entry["scores"][pname] = {
                    "avg_score": round(avg, 1),
                    "min": min(run_scores_for_persona),
                    "max": max(run_scores_for_persona),
                    "spread": max(run_scores_for_persona) - min(run_scores_for_persona),
                    "all_scores": run_scores_for_persona,
                    "justification": last_just,
                }
            else:
                entry["scores"][pname] = {
                    "avg_score": 0, "min": 0, "max": 0, "spread": 0,
                    "all_scores": [], "justification": "No score returned",
                }
        avg_batch_scores.append(entry)

    # ── overall aggregate per persona (across all batches, averaged over runs) ──
    agg: dict[str, dict[str, Any]] = {}
    for pname in persona_names:
        all_avgs = [bs["scores"][pname]["avg_score"] for bs in avg_batch_scores]
        overall_avg = sum(all_avgs) / len(all_avgs) if all_avgs else 0
        all_individual = []
        for bs in avg_batch_scores:
            all_individual.extend(bs["scores"][pname]["all_scores"])
        agg[pname] = {
            "avg": round(overall_avg, 1),
            "min": min(all_individual) if all_individual else 0,
            "max": max(all_individual) if all_individual else 0,
            "spread": (max(all_individual) - min(all_individual)) if all_individual else 0,
            "n_scores": len(all_individual),
        }

    # ── build report ──
    safe_site = re.sub(r"[^a-z0-9]+", "_", sitename.lower().split("://", 1)[-1]).strip("_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    runs_note = f" | {n_runs} run(s) averaged" if n_runs > 1 else ""

    lines = [
        f"# Persona Relevance Scoring: {sitename}",
        "",
        f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} | "
        f"Model: {model_name} | {len(avg_batch_scores)} batches | "
        f"{total_ai_calls} AI calls{runs_note} | "
        f"{_fmt_elapsed(total_elapsed)} elapsed*",
        "",
        "---",
        "",
        "## Aggregate Scores",
        "",
    ]

    if n_runs > 1:
        lines.append(f"*Scores averaged over {n_runs} independent runs to smooth variance.*")
        lines.append("")
        lines.append("| Persona | Avg | Min | Max | Spread | Scores |")
        lines.append("|---|---|---|---|---|---|")
        for pname in persona_names:
            a = agg[pname]
            lines.append(f"| {pname} | **{a['avg']:.0f}** | {a['min']} | {a['max']} | {a['spread']} | {a['n_scores']} |")
    else:
        lines.append("| Persona | Avg Score | Min | Max | Batches |")
        lines.append("|---|---|---|---|---|")
        for pname in persona_names:
            a = agg[pname]
            lines.append(f"| {pname} | **{a['avg']:.0f}** | {a['min']} | {a['max']} | {a['n_scores']} |")

    lines.extend(["", "---", "", "## Per-Batch Detail", ""])

    for bs in avg_batch_scores:
        lines.append(f"### Batch {bs['batch']} (pages {bs['start']+1}–{bs['end']}, {bs['chars']:,} chars)")
        lines.append("")
        if n_runs > 1:
            lines.append("| Persona | Avg | Min | Max | Spread | Scores | Justification |")
            lines.append("|---|---|---|---|---|---|---|")
            for pname in persona_names:
                info = bs["scores"][pname]
                scores_str = ", ".join(str(s) for s in info["all_scores"])
                lines.append(
                    f"| {pname} | **{info['avg_score']:.0f}** | {info['min']} | {info['max']} "
                    f"| {info['spread']} | {scores_str} | {info['justification']} |"
                )
        else:
            lines.append("| Persona | Score | Justification |")
            lines.append("|---|---|---|")
            for pname in persona_names:
                info = bs["scores"][pname]
                lines.append(f"| {pname} | {info['avg_score']:.0f} | {info['justification']} |")
        lines.append("")

    lines.extend(["---", "", "## Personas Used", ""])
    for p in personas:
        lines.append(f"### {p['name']}")
        lines.append("")
        desc_preview = p["description"][:500]
        if len(p["description"]) > 500:
            desc_preview += " ..."
        lines.append(f"*Source: {p['file']}*")
        lines.append("")
        lines.append(desc_preview)
        lines.append("")

    report = "\n".join(lines)

    if args.out:
        out_path = Path(args.out)
    else:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = REPORTS_DIR / f"{safe_site}_{ts}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"Report written to {out_path}")
    print(f"\nAggregate scores ({n_runs} run(s) averaged):" if n_runs > 1 else "\nAggregate scores:")
    for pname in persona_names:
        a = agg[pname]
        spread_note = f"  spread={a['spread']}" if n_runs > 1 else ""
        print(f"  {pname:30s}  avg={a['avg']:.0f}  min={a['min']}  max={a['max']}{spread_note}")
    print(f"\nTotal: {_fmt_elapsed(total_elapsed)} ({total_ai_calls} AI calls)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
