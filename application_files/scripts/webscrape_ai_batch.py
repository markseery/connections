#!/usr/bin/env python3
"""
Fetch stored pages for one or more sites from the webscraper skill,
split them into batches that fit in the model's context window,
run an AI prompt against each batch, and print/save the merged result.

Unlike webscrape_ai_analysis.yaml (which sends ALL pages in a single
AI call and blows up on large sites), this script caps each batch at
--max-context-chars and sends them separately.

Usage:
  python scripts/webscrape_ai_batch.py \
    --sites 'https://nebius.com,https://coreweave.com' \
    --prompt 'List the products, services, and solutions mentioned by each company' \
    --namespace webscrape

  # Limit pages per site, pick a lighter model
  python scripts/webscrape_ai_batch.py \
    --sites 'https://coreweave.com' \
    --prompt 'Summarize key differentiators' \
    --max-pages 30 --ai-strength fast

  # Save merged output to a file
  python scripts/webscrape_ai_batch.py \
    --sites 'https://nebius.com' \
    --prompt 'Extract product names as JSON' \
    --out nebius_products.md
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import httpx

_WORKER_NAMES = ["worker-1", "worker-2", "worker"]


def _find_worker(registry_url: str) -> str:
    for name in _WORKER_NAMES:
        try:
            r = httpx.get(f"{registry_url}/servers/{name}", timeout=5.0)
            if r.status_code == 200:
                url = (r.json() or {}).get("url", "").rstrip("/")
                if url and httpx.get(f"{url}/health", timeout=3.0).status_code == 200:
                    return url
        except Exception:
            continue
    raise RuntimeError("No live worker found in registry")


DEFAULT_REGISTRY_URL = os.environ.get(
    "REGISTRY_SERVER_URL", "http://127.0.0.1:7002"
).rstrip("/")
AI_TIMEOUT = 300.0
DEFAULT_BATCH_SIZE = 10
DEFAULT_MAX_CONTEXT_CHARS = 80_000
DEFAULT_MAX_CONTENT_PER_PAGE = 2000
AI_PROFILE_CHOICES = ("fast", "chat", "reason", "agent")
DEFAULT_AI_PROFILE = "agent"
DEFAULT_DELAY = 1.0
MAX_RETRIES = 4
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "webscrape" / "analysis"

_rate_lock = Lock()
_last_call_time = 0.0


def _discover_url(registry_url: str, name: str) -> str:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{registry_url}/servers/{name}")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError(f"Registry has no url for {name}")
        return str(url).rstrip("/")


def _fetch_pages(
    worker_url: str,
    site: str,
    namespace: str,
    max_content: int,
) -> list[dict]:
    url = f"{worker_url}/skills/webscraper_skill/pages/list"
    params = {"sitename": site, "namespace": namespace, "max_content": max_content}
    r = httpx.get(url, params=params, timeout=120.0)
    r.raise_for_status()
    return r.json().get("items", [])


def _build_batches(
    pages: list[dict],
    batch_size: int,
    max_context_chars: int,
) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for page in pages:
        page_chars = len(page.get("url", "")) + len(page.get("content", "")) + 40
        if current and (
            len(current) >= batch_size or current_chars + page_chars > max_context_chars
        ):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(page)
        current_chars += page_chars
    if current:
        batches.append(current)
    return batches


def _call_ai(aiserver_url: str, prompt: str, profile: str, delay: float) -> str:
    """POST /generate with rate-limiting and retry on 429."""
    global _last_call_time

    for attempt in range(MAX_RETRIES + 1):
        with _rate_lock:
            now = time.monotonic()
            wait = delay - (now - _last_call_time)
            if wait > 0:
                time.sleep(wait)
            _last_call_time = time.monotonic()

        with httpx.Client(timeout=AI_TIMEOUT) as client:
            r = client.post(
                f"{aiserver_url}/generate",
                json={"prompt": prompt, "profile": profile},
            )

        if r.status_code in (429, 500, 502, 503, 529):
            backoff = delay * (2 ** (attempt + 1))
            reasons = {429: "rate-limited", 529: "API overloaded"}
            reason = reasons.get(r.status_code, f"server error {r.status_code}")
            print(
                f"    [{r.status_code}] {reason}, retrying in {backoff:.0f}s "
                f"(attempt {attempt + 1}/{MAX_RETRIES}) …",
                file=sys.stderr, flush=True,
            )
            time.sleep(backoff)
            continue

        r.raise_for_status()
        out = r.json().get("output")
        if isinstance(out, dict) and "text" in out:
            return str(out["text"]).strip()
        if isinstance(out, str):
            return out.strip()
        return str(out).strip()

    raise httpx.HTTPStatusError(
        f"{r.status_code} after {MAX_RETRIES} retries",
        request=r.request,
        response=r,
    )


def _format_batch_context(site: str, pages: list[dict]) -> str:
    blocks = []
    for p in pages:
        blocks.append(f"URL: {p.get('url', '?')}\n\n{p.get('content', '')}")
    return (
        f"=== SITE: {site} ({len(pages)} pages) ===\n\n"
        + "\n\n--- Next Page ---\n\n".join(blocks)
    )


def _process_one_batch(
    *,
    site: str,
    batch: list[dict],
    batch_index: int,
    total_batches: int,
    page_start: int,
    page_end: int,
    prompt: str,
    aiserver_url: str,
    profile: str,
    delay: float,
) -> tuple[int, str | None]:
    """Process a single batch. Returns (batch_index, raw_ai_text_or_None)."""
    label = f"Batch {batch_index + 1}/{total_batches} (pages {page_start}-{page_end})"
    context = _format_batch_context(site, batch)
    full_prompt = (
        f"{prompt}\n\n"
        f"Below is content from {len(batch)} page(s) scraped from {site}.\n"
        "Use this content to answer the prompt above.\n\n"
        f"{context}"
    )
    t0 = time.monotonic()
    try:
        text = _call_ai(aiserver_url, full_prompt, profile, delay)
    except Exception as e:
        print(f"  [{label}] ERROR: {e}", file=sys.stderr, flush=True)
        return batch_index, None

    elapsed = time.monotonic() - t0
    print(f"  [{label}] OK ({len(text)} chars, {elapsed:.1f}s)", flush=True)
    return batch_index, text


def _run_site(
    *,
    site: str,
    pages: list[dict],
    prompt: str,
    aiserver_url: str,
    profile: str,
    batch_size: int,
    max_context_chars: int,
    synthesize: bool,
    workers: int,
    delay: float,
) -> str:
    """Returns a single string: the synthesis (or concatenated batch results as fallback)."""
    batches = _build_batches(pages, batch_size, max_context_chars)
    total_pages = sum(len(b) for b in batches)
    effective_workers = min(workers, len(batches))
    print(
        f"[{site}] {total_pages} pages → {len(batches)} batch(es) "
        f"(batch_size={batch_size}, max_context={max_context_chars}, "
        f"profile={profile}, workers={effective_workers})",
        flush=True,
    )

    page_offsets: list[tuple[int, int]] = []
    offset = 0
    for batch in batches:
        page_offsets.append((offset + 1, offset + len(batch)))
        offset += len(batch)

    indexed_outputs: list[tuple[int, str | None]] = []

    with ThreadPoolExecutor(max_workers=effective_workers) as pool:
        futures = {
            pool.submit(
                _process_one_batch,
                site=site,
                batch=batch,
                batch_index=bi,
                total_batches=len(batches),
                page_start=page_offsets[bi][0],
                page_end=page_offsets[bi][1],
                prompt=prompt,
                aiserver_url=aiserver_url,
                profile=profile,
                delay=delay,
            ): bi
            for bi, batch in enumerate(batches)
        }
        for future in as_completed(futures):
            indexed_outputs.append(future.result())

    indexed_outputs.sort(key=lambda x: x[0])
    batch_texts = [text for _, text in indexed_outputs if text is not None]

    if not batch_texts:
        return "_All batches failed._"

    if synthesize and len(batch_texts) > 1:
        print(f"  [synthesis] merging {len(batch_texts)} batch results …", flush=True)
        numbered = "\n\n---\n\n".join(
            f"[Batch {i + 1}]\n{t}" for i, t in enumerate(batch_texts)
        )
        synth_prompt = (
            f"{prompt}\n\n"
            "Below are partial results from processing the site in batches. "
            "Synthesize them into a single, comprehensive, deduplicated answer.\n\n"
            f"{numbered}"
        )
        try:
            synth = _call_ai(aiserver_url, synth_prompt, profile, delay)
            print(f"  [synthesis] OK ({len(synth)} chars)", flush=True)
            return synth
        except Exception as e:
            print(f"  [synthesis] ERROR (falling back to concat): {e}", file=sys.stderr, flush=True)

    if len(batch_texts) == 1:
        return batch_texts[0]

    return "\n\n---\n\n".join(batch_texts)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Batch AI analysis of stored webscrape pages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              python scripts/webscrape_ai_batch.py \\
                --sites 'https://nebius.com,https://coreweave.com' \\
                --prompt 'List products and services for each company'

              python scripts/webscrape_ai_batch.py \\
                --sites 'https://coreweave.com' \\
                --prompt 'Summarize differentiators' \\
                --max-pages 20 --ai-strength fast --no-synthesis
        """),
    )
    ap.add_argument(
        "--sites",
        required=True,
        help="Comma-separated site URLs (e.g. 'https://a.com,https://b.com')",
    )
    ap.add_argument("--prompt", required=True, help="Prompt applied to each batch")
    ap.add_argument(
        "--namespace",
        default="webscrape",
        help="Storage namespace (default: webscrape)",
    )
    ap.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="Limit to first N pages per site (default: all)",
    )
    ap.add_argument(
        "--max-content",
        type=int,
        default=DEFAULT_MAX_CONTENT_PER_PAGE,
        metavar="CHARS",
        help=f"Max chars to fetch per page (default: {DEFAULT_MAX_CONTENT_PER_PAGE})",
    )
    ap.add_argument(
        "-b",
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help=f"Max pages per AI call (default: {DEFAULT_BATCH_SIZE})",
    )
    ap.add_argument(
        "--max-context-chars",
        type=int,
        default=DEFAULT_MAX_CONTEXT_CHARS,
        metavar="CHARS",
        help=f"Max total chars per AI call (default: {DEFAULT_MAX_CONTEXT_CHARS})",
    )
    ap.add_argument(
        "--ai-strength",
        choices=list(AI_PROFILE_CHOICES),
        default=DEFAULT_AI_PROFILE,
        metavar="LEVEL",
        help=f"AI profile: {', '.join(AI_PROFILE_CHOICES)} (default: {DEFAULT_AI_PROFILE})",
    )
    ap.add_argument(
        "-w",
        "--workers",
        type=int,
        default=4,
        metavar="N",
        help="Max concurrent AI calls per site (default: 4)",
    )
    ap.add_argument(
        "-d",
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        metavar="SECS",
        help=f"Min seconds between AI calls to avoid rate-limits (default: {DEFAULT_DELAY})",
    )
    ap.add_argument(
        "--no-synthesis",
        action="store_true",
        help="Skip the final synthesis pass that merges batch results",
    )
    ap.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help="Save output. Relative paths go under data/webscrape/analysis/. "
        "Default: auto-named with timestamp.",
    )
    ap.add_argument(
        "--registry-url",
        default=DEFAULT_REGISTRY_URL,
        help="Registry URL (default: $REGISTRY_SERVER_URL or 127.0.0.1:7002)",
    )
    args = ap.parse_args()

    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    if not sites:
        print("Error: --sites is empty", file=sys.stderr)
        return 1

    prompt = (args.prompt or "").strip()
    if not prompt:
        print("Error: --prompt is empty", file=sys.stderr)
        return 1

    registry = args.registry_url.rstrip("/")
    try:
        aiserver_url = _discover_url(registry, "aiserver")
    except Exception as e:
        print(f"Error discovering aiserver: {e}", file=sys.stderr)
        return 1

    try:
        worker_url = _find_worker(registry)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    worker_url = worker_url.rstrip("/")

    profile = args.ai_strength
    batch_size = max(1, args.batch_size)
    max_context = max(1000, args.max_context_chars)
    max_content = max(100, args.max_content)
    workers = max(1, args.workers)
    delay = max(0.0, args.delay)
    synthesize = not args.no_synthesis

    site_results: list[tuple[str, str]] = []

    for site in sites:
        print(f"\n{'='*60}", flush=True)
        print(f"Fetching pages for {site} …", flush=True)
        try:
            pages = _fetch_pages(worker_url, site, args.namespace, max_content)
        except Exception as e:
            print(f"Error fetching {site}: {e}", file=sys.stderr)
            site_results.append((site, f"_Error fetching pages: {e}_"))
            continue

        if not pages:
            print(f"  No pages found for {site}", flush=True)
            site_results.append((site, "_No pages found._"))
            continue

        if args.max_pages is not None and args.max_pages > 0:
            pages = pages[: args.max_pages]

        result = _run_site(
            site=site,
            pages=pages,
            prompt=prompt,
            aiserver_url=aiserver_url,
            profile=profile,
            batch_size=batch_size,
            max_context_chars=max_context,
            synthesize=synthesize,
            workers=workers,
            delay=delay,
        )
        site_results.append((site, result))

    successful = [(s, r) for s, r in site_results if not r.startswith("_")]

    if synthesize and len(successful) > 1:
        print(f"\n{'='*60}", flush=True)
        print("Final synthesis across all sites …", flush=True)
        combined = "\n\n---\n\n".join(
            f"## {site}\n\n{text}" for site, text in successful
        )
        final_prompt = (
            f"{prompt}\n\n"
            "Below are the per-site results. Synthesize them into a single, "
            "comprehensive, deduplicated answer that covers all sites.\n\n"
            f"{combined}"
        )
        try:
            final_text = _call_ai(aiserver_url, final_prompt, profile, delay)
            print(f"  [final synthesis] OK ({len(final_text)} chars)", flush=True)
            full_output = f"# Batch AI Analysis\n\n**Prompt:** {prompt}\n\n{final_text}\n"
        except Exception as e:
            print(f"  [final synthesis] ERROR: {e}", file=sys.stderr, flush=True)
            full_output = f"# Batch AI Analysis\n\n**Prompt:** {prompt}\n\n" + "\n---\n\n".join(
                f"## {site}\n\n{text}" for site, text in site_results
            )
    else:
        full_output = f"# Batch AI Analysis\n\n**Prompt:** {prompt}\n\n" + "\n---\n\n".join(
            f"## {site}\n\n{text}" for site, text in site_results
        )

    print(f"\n{'='*60}")
    print(full_output)

    out_path: Path | None = None
    if args.out is not None:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = OUTPUT_DIR / out_path
    else:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        slug = "_".join(
            s.replace("https://", "").replace("http://", "").split("/")[0].replace(".", "_")
            for s in sites
        )[:60]
        out_path = OUTPUT_DIR / f"{slug}_{ts}.md"

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(full_output, encoding="utf-8")
        try:
            rel = out_path.relative_to(Path(__file__).resolve().parents[1])
        except ValueError:
            rel = out_path
        print(f"\nSaved to {rel}", file=sys.stderr, flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
