#!/usr/bin/env python3
"""
Batched website analysis using adaptive batching and multi-topic prompts.

Reads previously-scraped page content via the batcher skill and extracts
findings across a configurable set of topics defined in a YAML analysis
profile.  All tunable parameters — storage namespace, key separator,
AI profile, context %, chars-per-token ratio, AI timeout, and topic
definitions with extraction guidance — live in the profile YAML.

Profiles live in application_files/config/analysis_profiles/<name>.yaml.
Use --profile to select one (default: marketing).

Output is written to application_files/data/<profile_name>/<site>_<timestamp>.md
by default.  Use --out to override.

Usage:
  python3 batch_analysis.py https://nebius.com
  python3 batch_analysis.py https://nebius.com --profile technical
  python3 batch_analysis.py https://nebius.com --context-pct 0.85
  python3 batch_analysis.py https://nebius.com --namespace custom_ns
  python3 batch_analysis.py --list-profiles

Requires: registry, worker (batcher_skill), aiserver, storage with existing data.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import random

import httpx
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = APP_ROOT / "config" / "analysis_profiles"
DATA_DIR = APP_ROOT / "data"
DEFAULT_PROFILE = "marketing"

DEFAULT_REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")


# ── analysis profile ────────────────────────────────────────────────────

class AnalysisProfile:
    """Loaded from a YAML file in config/analysis_profiles/."""

    def __init__(self, path: Path) -> None:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        self.name: str = raw.get("name", path.stem)
        self.slug: str = path.stem
        self.description: str = raw.get("description", "")

        self.namespace: str = raw.get("namespace", "webscrape")
        self.key_separator: str = raw.get("key_separator", "\x00")

        self.ai_profile: str = raw.get("ai_profile", "agent")
        self.context_pct: float = float(raw.get("context_pct", 0.80))
        self.chars_per_token: float = float(raw.get("chars_per_token", 3.5))
        self.ai_timeout: float = float(raw.get("ai_timeout", 660))

        raw_topics = raw.get("topics") or []
        if not raw_topics:
            raise ValueError(f"Profile {path} has no topics defined")
        self.topic_names: list[str] = []
        self.topic_guidance: dict[str, str] = {}
        for entry in raw_topics:
            name = entry.get("name", "").strip()
            if not name:
                continue
            self.topic_names.append(name)
            guidance = (entry.get("guidance") or "").strip()
            if guidance:
                self.topic_guidance[name] = guidance

    def topics_block(self) -> str:
        lines = []
        for i, name in enumerate(self.topic_names, 1):
            guidance = self.topic_guidance.get(name, "")
            if guidance:
                lines.append(f"  {i}. **{name}**: {guidance}")
            else:
                lines.append(f"  {i}. {name}")
        return "\n".join(lines)

    def reports_dir(self) -> Path:
        return DATA_DIR / self.slug


def _load_profile(name: str) -> AnalysisProfile:
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in PROFILES_DIR.glob("*.yaml")) if PROFILES_DIR.is_dir() else []
        raise FileNotFoundError(
            f"Profile '{name}' not found at {path}\n"
            f"Available profiles: {', '.join(available) or 'none'}"
        )
    return AnalysisProfile(path)


def _list_profiles() -> list[str]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


# ── prompt templates ─────────────────────────────────────────────────────

_EXTRACT_PROMPT_TEMPLATE = """\
You are analysing web pages from a single website ({batch_label}).

{profile_context}

For EACH of the following {n_topics} topics, extract any relevant information
from the content below.  Be specific — include names, numbers, locations, and
quotes where present.  If there is no relevant information for a topic, write
exactly "NO_RELEVANT_INFO" for that topic.

Return your answer as numbered sections matching the topic numbers below.
Use the format:

## 1. Topic name
<findings or NO_RELEVANT_INFO>

## 2. Topic name
...

Topics:
{topics_block}

--- Website content ---
{content}
"""

_SYNTH_PROMPT_TEMPLATE = """\
Below are findings about **{topic}** extracted from {n_batches} batches of
web pages.  Consolidate them into a single, comprehensive summary.
Remove duplicates, reconcile contradictions, and present the strongest
evidence.  Use bullet points where helpful.  If no batch had relevant
information, say so.

{numbered_findings}
"""


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s" if m else f"{s}s"


# ── service discovery ────────────────────────────────────────────────────

def _find_worker(registry_url: str) -> str:
    """Discover all worker servers from the registry, health-check them,
    and pick an idle one. Falls back to the least busy worker."""
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
    if idle:
        chosen = random.choice(idle)
    else:
        candidates.sort(key=lambda x: x[1])
        chosen = candidates[0][0]

    if len(candidates) > 1:
        active_for_chosen = next(a for u, a in candidates if u == chosen)
        print(f"  Workers: {len(candidates)} healthy, "
              f"{len(idle)} idle, selected: {chosen} "
              f"(active={active_for_chosen})", flush=True)
    return chosen


def _get_aiserver_url(registry_url: str) -> str:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{registry_url}/servers/aiserver")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError("Registry missing url for aiserver")
        return str(url).rstrip("/")


def _get_batch_plan(
    worker_url: str,
    namespace: str,
    prefix: str,
    ai_profile: str,
    context_pct: float,
    chars_per_token: float,
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


def _canonical_sitename(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = (parsed.hostname or url).lower().rstrip("/")
    return f"{scheme}://{host}"


def _default_report_path(profile: AnalysisProfile, sitename: str) -> Path:
    safe_name = re.sub(r"[^a-z0-9]+", "_", sitename.lower().split("://", 1)[-1]).strip("_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = profile.reports_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{safe_name}_{ts}.md"


# ── batcher client ───────────────────────────────────────────────────────

def _batcher_post(worker_url: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=60.0) as client:
        r = client.post(f"{worker_url}/skills/batcher_skill{path}", json=body)
        r.raise_for_status()
    return r.json()


def batcher_batch(
    worker_url: str, namespace: str, prefix: str, offset: int, limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    resp = _batcher_post(worker_url, "/batch", {
        "namespace": namespace, "prefix": prefix,
        "offset": offset, "limit": limit,
    })
    items = resp.get("items", [])
    has_more = resp.get("data", {}).get("has_more", False)
    return items, has_more


# ── AI calls ─────────────────────────────────────────────────────────────

def _ai_generate(aiserver_url: str, prompt: str, ai_profile: str, timeout: float = 660.0) -> tuple[str, dict[str, Any]]:
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            f"{aiserver_url}/generate",
            json={"prompt": prompt, "profile": ai_profile},
        )
        r.raise_for_status()
    out = r.json()
    meta = {
        "provider": out.get("provider"),
        "profile": out.get("profile"),
        "model": out.get("model"),
    }
    output = out.get("output") if isinstance(out.get("output"), dict) else out
    if isinstance(output, dict) and "text" in output:
        text = str(output["text"]).strip()
    else:
        text = str(output).strip() if output else ""
    return text, meta


def _parse_multi_topic_response(response: str, topic_names: list[str]) -> dict[str, str]:
    results: dict[str, str] = {}
    current_topic: str | None = None
    current_lines: list[str] = []

    for line in response.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_topic is not None:
                results[current_topic] = "\n".join(current_lines).strip()
            header = stripped[3:].strip()
            for i, ch in enumerate(header):
                if ch == '.' and i > 0:
                    header = header[i+1:].strip()
                    break
                if not ch.isdigit():
                    break
            current_topic = _match_topic(header, topic_names)
            current_lines = []
        elif current_topic is not None:
            current_lines.append(line)

    if current_topic is not None:
        results[current_topic] = "\n".join(current_lines).strip()

    return results


def _match_topic(header: str, topic_names: list[str]) -> str:
    h_lower = header.lower().strip()
    for topic in topic_names:
        if topic.lower() == h_lower:
            return topic
    for topic in topic_names:
        if topic.lower() in h_lower or h_lower in topic.lower():
            return topic
    return header


def analyse_batch_multi_topic(
    aiserver_url: str,
    content: str,
    batch_label: str,
    profile: AnalysisProfile,
) -> tuple[dict[str, str], dict[str, Any]]:
    profile_context = f"Analysis: **{profile.name}**"
    if profile.description:
        profile_context += f"\n{profile.description}"

    prompt = _EXTRACT_PROMPT_TEMPLATE.format(
        batch_label=batch_label,
        profile_context=profile_context,
        n_topics=len(profile.topic_names),
        topics_block=profile.topics_block(),
        content=content,
    )
    text, meta = _ai_generate(aiserver_url, prompt, profile.ai_profile, timeout=profile.ai_timeout)
    findings = _parse_multi_topic_response(text, profile.topic_names)
    return findings, meta


def synthesise_topic(
    aiserver_url: str, topic: str, batch_findings: list[str],
    ai_profile: str, timeout: float = 660.0,
) -> str:
    numbered = "\n\n".join(
        f"--- Batch {i+1} ---\n{f}" for i, f in enumerate(batch_findings)
    )
    prompt = _SYNTH_PROMPT_TEMPLATE.format(
        topic=topic,
        n_batches=len(batch_findings),
        numbered_findings=numbered,
    )
    text, _ = _ai_generate(aiserver_url, prompt, ai_profile, timeout=timeout)
    return text


# ── main flow ────────────────────────────────────────────────────────────

def main() -> int:
    available = _list_profiles()
    profiles_help = f"Available: {', '.join(available)}" if available else "No profiles found"

    parser = argparse.ArgumentParser(
        description=(
            "Batched website analysis with adaptive batching and multi-topic prompts. "
            "Topics and extraction guidance are defined in YAML analysis profiles."
        ),
    )
    parser.add_argument("url", help="Website URL to analyse (must already be scraped and stored)")
    parser.add_argument(
        "--profile", default=DEFAULT_PROFILE,
        help=f"Analysis profile name (default: {DEFAULT_PROFILE}). {profiles_help}",
    )
    parser.add_argument(
        "--out", default="",
        help="Write report to this path. Default: application_files/data/<profile>/<site>_<timestamp>.md",
    )
    parser.add_argument("--namespace", default=None, help="Override profile's storage namespace")
    parser.add_argument("--context-pct", type=float, default=None, help="Override profile's context_pct")
    parser.add_argument("--registry-url", default=DEFAULT_REGISTRY_URL, help="Registry server URL")
    parser.add_argument("--worker-url", default=None, help="Worker base URL (overrides registry lookup)")
    parser.add_argument("--list-profiles", action="store_true", help="List available profiles and exit")
    args = parser.parse_args()

    if args.list_profiles:
        if not available:
            print(f"No profiles found in {PROFILES_DIR}", file=sys.stderr)
            return 1
        for name in available:
            try:
                p = _load_profile(name)
                print(f"  {name:20s}  {p.name} ({len(p.topic_names)} topics, "
                      f"ns={p.namespace}, ctx={p.context_pct:.0%})")
            except Exception as e:
                print(f"  {name:20s}  [error: {e}]")
        return 0

    # ── load profile ──
    try:
        profile = _load_profile(args.profile)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1

    namespace = args.namespace or profile.namespace
    topic_names = profile.topic_names
    n_topics = len(topic_names)
    context_pct = max(0.1, min(0.95, args.context_pct if args.context_pct is not None else profile.context_pct))

    print(f"Profile: {profile.name} ({n_topics} topics)", flush=True)
    print(f"  Storage: namespace={namespace}, key_separator={repr(profile.key_separator)}", flush=True)
    if profile.description:
        print(f"  {profile.description.strip()}", flush=True)

    url = args.url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    sitename = _canonical_sitename(url)

    registry_url = (args.registry_url or DEFAULT_REGISTRY_URL).rstrip("/")
    try:
        worker_url = args.worker_url.rstrip("/") if args.worker_url else _find_worker(registry_url)
        aiserver_url = _get_aiserver_url(registry_url)
    except Exception as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    # ── batch plan via recommender skill ──
    prefix = sitename + profile.key_separator
    topics_block = profile.topics_block()
    prompt_overhead = len(_EXTRACT_PROMPT_TEMPLATE) + len(topics_block) + len(profile.description or "") + 500

    print(f"\nRequesting batch plan from recommender skill ...", flush=True)
    try:
        plan = _get_batch_plan(
            worker_url, namespace, prefix, profile.ai_profile,
            context_pct, profile.chars_per_token, prompt_overhead,
        )
    except Exception as e:
        print(f"Batch recommender error: {e}", file=sys.stderr)
        return 1

    plan_data = plan.get("data", {})
    total_records = plan_data.get("total_records", 0)

    if total_records == 0:
        print(f"No stored content found for {sitename} in namespace '{namespace}'.\n"
              f"Run a scrape first, e.g.:\n"
              f"  python3 webscrape_fetch.py {url} --namespace {namespace}",
              file=sys.stderr)
        return 1

    model_name = plan_data.get("model", "unknown")
    context_tokens = plan_data.get("context_window", 128_000)
    context_source = plan_data.get("context_window_source", "unknown")
    content_budget = plan_data.get("content_budget", 100_000)
    non_empty = plan_data.get("non_empty_records", total_records)
    total_chars = plan_data.get("total_chars", 0)
    batch_plan = plan_data.get("batches", [])
    n_batches = len(batch_plan)
    batches = [(b["start"], b["end"]) for b in batch_plan]

    print(f"Model: {model_name} ({context_tokens:,} token context window, source: {context_source})", flush=True)
    print(f"Content budget per batch: {content_budget:,} chars "
          f"({context_pct:.0%} of {context_tokens:,} tokens × {profile.chars_per_token} chars/token "
          f"– {prompt_overhead:,} prompt overhead)", flush=True)

    # ── load page content ──
    print(f"\nFetching page content ({total_records} pages) ...", flush=True)
    fetch_chunk = 200
    all_pages: list[dict[str, Any]] = []
    offset = 0
    while offset < total_records:
        items, has_more = batcher_batch(worker_url, namespace, prefix, offset, fetch_chunk)
        if not items:
            break
        all_pages.extend(items)
        offset += fetch_chunk
        if not has_more:
            break
    print(f"  Retrieved {len(all_pages)} pages.", flush=True)

    if not all_pages:
        print("No page content retrieved.", file=sys.stderr)
        return 1

    page_sizes = [len(str(p.get("content", ""))) for p in all_pages]

    print(f"\n{'='*60}", flush=True)
    print(f"  Pages: {non_empty} with content ({total_chars:,} total chars)", flush=True)
    print(f"  Batches: {n_batches} (adaptively sized to ~{content_budget:,} chars each)", flush=True)
    print(f"  Phase 1: {n_batches} AI calls (1 multi-topic call per batch)", flush=True)
    print(f"  Phase 2: up to {n_topics} synthesis calls", flush=True)
    print(f"  Total AI calls: ~{n_batches + n_topics} "
          f"(vs ~{n_batches * n_topics + n_topics} with single-topic approach)", flush=True)
    print(f"  AI profile: {profile.ai_profile}", flush=True)
    print(f"{'='*60}", flush=True)

    # ── Phase 1: analyse each batch ──
    run_start = time.monotonic()
    all_batch_findings: dict[str, list[str]] = {t: [] for t in topic_names}
    total_pages_analysed = 0
    ai_meta: dict[str, str] = {}
    batch_times: list[float] = []

    for batch_idx, (start, end) in enumerate(batches, 1):
        batch_start = time.monotonic()
        elapsed_total = batch_start - run_start
        batch_pages = all_pages[start:end]
        batch_chars = sum(page_sizes[start:end])
        page_count = sum(1 for s in page_sizes[start:end] if s > 0)

        print(f"\n── Batch {batch_idx}/{n_batches} "
              f"(pages {start+1}–{end}/{len(all_pages)}, {batch_chars:,} chars) "
              f"[elapsed {_fmt_elapsed(elapsed_total)}] ──", flush=True)

        if ai_meta:
            print(f"  AI: provider={ai_meta.get('provider','?')}, "
                  f"model={ai_meta.get('model','?')}", flush=True)

        content_parts = []
        for p in batch_pages:
            c = str(p.get("content", "")).strip()
            if c:
                content_parts.append(c)
        content = "\n\n---\n\n".join(content_parts)

        if not content.strip():
            print("  (empty batch, skipping)", flush=True)
            continue

        total_pages_analysed += page_count
        batch_label = f"batch {batch_idx}/{n_batches}, pages {start+1}–{end} of {len(all_pages)}"

        print(f"  Sending {len(content):,} chars with {n_topics} topics in one call ...", flush=True)
        t0 = time.monotonic()
        try:
            findings, meta = analyse_batch_multi_topic(aiserver_url, content, batch_label, profile)
            call_dur = time.monotonic() - t0
            if not ai_meta:
                ai_meta.update({k: v for k, v in meta.items() if v})
        except Exception as e:
            call_dur = time.monotonic() - t0
            print(f"  [!] AI call failed ({call_dur:.1f}s): {e}", flush=True)
            continue

        relevant_count = 0
        for topic in topic_names:
            text = findings.get(topic, "")
            if text and "NO_RELEVANT_INFO" not in text and not text.startswith("[Error"):
                all_batch_findings[topic].append(text)
                relevant_count += 1

        batch_dur = time.monotonic() - batch_start
        batch_times.append(batch_dur)
        avg_batch = sum(batch_times) / len(batch_times)
        remaining = n_batches - batch_idx
        eta = avg_batch * remaining

        print(f"  {relevant_count}/{n_topics} topics had findings | "
              f"AI call: {call_dur:.1f}s | batch: {_fmt_elapsed(batch_dur)} | "
              f"avg {_fmt_elapsed(avg_batch)}/batch | "
              f"ETA phase 1: ~{_fmt_elapsed(eta)}", flush=True)

    if total_pages_analysed == 0:
        print("No pages were successfully analysed.", file=sys.stderr)
        return 1

    phase1_elapsed = time.monotonic() - run_start
    topics_with_findings = sum(1 for v in all_batch_findings.values() if v)
    topics_needing_synthesis = sum(1 for v in all_batch_findings.values() if len(v) > 1)

    # ── Phase 2: synthesise ──
    print(f"\n{'='*60}", flush=True)
    print(f"Phase 1 complete: {n_batches} batches, {total_pages_analysed} pages "
          f"in {_fmt_elapsed(phase1_elapsed)}", flush=True)
    print(f"  {topics_with_findings}/{n_topics} topics have findings", flush=True)
    print(f"  {topics_needing_synthesis} topics need multi-batch synthesis", flush=True)
    print(f"{'='*60}", flush=True)

    synth_start = time.monotonic()
    final: dict[str, str] = {}

    for i, topic in enumerate(topic_names, 1):
        findings_list = all_batch_findings[topic]
        if not findings_list:
            final[topic] = "_No relevant information found across any batch._"
            print(f"  [{i}/{n_topics}] {topic} — no findings, skipped", flush=True)
            continue

        if len(findings_list) == 1:
            final[topic] = findings_list[0]
            print(f"  [{i}/{n_topics}] {topic} — single batch, no synthesis needed", flush=True)
            continue

        t0 = time.monotonic()
        print(f"  [{i}/{n_topics}] Synthesising: {topic} ({len(findings_list)} batches) ...",
              end="", flush=True)
        try:
            final[topic] = synthesise_topic(
                aiserver_url, topic, findings_list, profile.ai_profile,
                timeout=profile.ai_timeout,
            )
            dur = time.monotonic() - t0
            print(f" done ({dur:.1f}s)", flush=True)
        except Exception as e:
            dur = time.monotonic() - t0
            final[topic] = f"[Synthesis error: {e}]"
            print(f" error ({dur:.1f}s): {e}", flush=True)

    total_elapsed = time.monotonic() - run_start
    synth_elapsed = time.monotonic() - synth_start
    total_ai_calls = len(batch_times) + topics_needing_synthesis

    # ── Phase 3: build report ──
    lines = [
        f"# {profile.name}: {url}",
        "",
        f"*{profile.description.strip()}*" if profile.description else "",
        "",
        f"*Based on {total_pages_analysed} stored pages analysed in {len(batch_times)} "
        f"batch(es) with {total_ai_calls} AI calls (namespace: {namespace}, "
        f"model: {model_name}, profile: {args.profile}).*",
        "",
        "---",
        "",
    ]
    for topic in topic_names:
        lines.append(f"## {topic.title()}")
        lines.append("")
        lines.append(final.get(topic, ""))
        lines.append("")
        lines.append("---")
        lines.append("")

    report = "\n".join(lines)
    out_path = Path(args.out) if args.out else _default_report_path(profile, sitename)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nReport written to {out_path}", flush=True)

    print(f"\nDone. Phase 1: {_fmt_elapsed(phase1_elapsed)} ({len(batch_times)} AI calls) | "
          f"Phase 2: {_fmt_elapsed(synth_elapsed)} ({topics_needing_synthesis} AI calls) | "
          f"Total: {_fmt_elapsed(total_elapsed)} ({total_ai_calls} AI calls)", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
