#!/usr/bin/env python3
"""
Batch AI analysis of stored webscrape pages using MLX locally.

Loads an MLX model in-process and runs inference directly — no aiserver,
no HTTP overhead, no rate-limits. Processes batches sequentially since
MLX uses the GPU exclusively.

Performance tuning (M4 / 24 GB baseline):
  - Default model is Qwen3-4B-4bit (~2.3 GB, ~2x faster than Mistral-7B)
  - --max-kv-size caps the rotating KV cache for very long contexts
  - Smaller defaults for batch-size, max-context-chars, max-content, and
    max-tokens keep prefill and decode times short

Usage:
  python scripts/webscrape_ai_batch_mlx.py \\
    --sites 'https://lambda.ai' \\
    --prompt 'List the products and services mentioned' \\
    --namespace webscrape

  python scripts/webscrape_ai_batch_mlx.py \\
    --sites 'https://coreweave.com' \\
    --prompt 'Summarize key differentiators' \\
    --model 'mlx-community/Mistral-7B-Instruct-v0.3-4bit' \\
    --max-pages 30 --out coreweave_products.md
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.skill_lifecycle import find_live_worker

DEFAULT_REGISTRY_URL = os.environ.get(
    "REGISTRY_SERVER_URL", "http://127.0.0.1:7002"
).rstrip("/")
DEFAULT_MODEL = "mlx-community/Qwen3-4B-4bit"
DEFAULT_BATCH_SIZE = 5
DEFAULT_MAX_CONTEXT_CHARS = 40_000
DEFAULT_MAX_CONTENT_PER_PAGE = 1000
DEFAULT_MAX_TOKENS = 1024
OUTPUT_DIR = ROOT / "data" / "webscrape" / "analysis"


def _try_set_wired_memory(model_mb: int) -> None:
    """Hint macOS to wire enough GPU memory for the model weights + KV cache."""
    target = max(model_mb * 2, 8000)
    try:
        subprocess.run(
            ["sudo", "-n", "sysctl", f"iogpu.wired_limit_mb={target}"],
            capture_output=True,
            timeout=5,
        )
        print(f"  iogpu.wired_limit_mb set to {target}", flush=True)
    except Exception:
        print(
            f"  Tip: run  sudo sysctl iogpu.wired_limit_mb={target}  "
            "to avoid GPU memory paging",
            flush=True,
        )


def _estimate_model_mb(model_name: str) -> int:
    """Rough size estimate so we can suggest a wired-memory value."""
    name = model_name.lower()
    if "3b" in name:
        return 2000
    if "4b" in name:
        return 2500
    if "7b" in name or "8b" in name:
        return 4500
    if "13b" in name or "14b" in name:
        return 8000
    return 4000


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


def _format_batch_context(site: str, pages: list[dict]) -> str:
    blocks = []
    for p in pages:
        blocks.append(f"URL: {p.get('url', '?')}\n\n{p.get('content', '')}")
    return (
        f"=== SITE: {site} ({len(pages)} pages) ===\n\n"
        + "\n\n--- Next Page ---\n\n".join(blocks)
    )


def _call_mlx(
    model: object,
    tokenizer: object,
    prompt: str,
    max_tokens: int,
    *,
    max_kv_size: int | None = None,
) -> str:
    """Run MLX inference. No prompt cache — used for one-off calls like synthesis."""
    from mlx_lm import generate as mlx_generate

    messages = [{"role": "user", "content": prompt}]
    formatted = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
    kwargs: dict = dict(
        prompt=formatted, max_tokens=max_tokens, verbose=False,
    )
    if max_kv_size is not None:
        kwargs["max_kv_size"] = max_kv_size
    return mlx_generate(model, tokenizer, **kwargs).strip()


def _hierarchical_synthesis(
    texts: list[str],
    prompt: str,
    model: object,
    tokenizer: object,
    max_tokens: int,
    max_kv_size: int | None,
    max_context_chars: int,
) -> str | None:
    """Merge batch results in rounds that fit within max_context_chars."""
    round_num = 0
    current = list(texts)

    while len(current) > 1:
        round_num += 1
        groups: list[list[str]] = []
        group: list[str] = []
        group_chars = 0
        for t in current:
            t_chars = len(t) + 20
            if group and group_chars + t_chars > max_context_chars:
                groups.append(group)
                group = []
                group_chars = 0
            group.append(t)
            group_chars += t_chars
        if group:
            groups.append(group)

        if len(groups) == 1 and len(groups[0]) == len(current):
            # Everything fits in one group — do the final merge
            pass

        label = f"synthesis round {round_num}"
        print(
            f"  [{label}] {len(current)} inputs → {len(groups)} group(s) …",
            flush=True,
        )

        next_round: list[str] = []
        for gi, grp in enumerate(groups):
            numbered = "\n\n---\n\n".join(
                f"[Result {i + 1}]\n{t}" for i, t in enumerate(grp)
            )
            synth_prompt = (
                f"{prompt}\n\n"
                "Below are partial results from processing a site in batches. "
                "Synthesize them into a single, comprehensive, deduplicated answer.\n\n"
                f"{numbered}"
            )
            glabel = f"{label} group {gi + 1}/{len(groups)}"
            t0 = time.monotonic()
            try:
                synth = _call_mlx(
                    model, tokenizer, synth_prompt, max_tokens,
                    max_kv_size=max_kv_size,
                )
            except Exception as e:
                print(
                    f"  [{glabel}] ERROR (falling back to concat): {e}",
                    file=sys.stderr, flush=True,
                )
                return None

            elapsed = time.monotonic() - t0
            print(f"  [{glabel}] OK ({len(synth)} chars, {elapsed:.1f}s)", flush=True)
            next_round.append(synth)

        current = next_round

    return current[0]


def _run_site(
    *,
    site: str,
    pages: list[dict],
    prompt: str,
    model: object,
    tokenizer: object,
    batch_size: int,
    max_context_chars: int,
    max_tokens: int,
    max_kv_size: int | None,
    synthesize: bool,
) -> str:
    batches = _build_batches(pages, batch_size, max_context_chars)
    total_pages = sum(len(b) for b in batches)
    print(
        f"[{site}] {total_pages} pages → {len(batches)} batch(es) "
        f"(batch_size={batch_size}, max_context={max_context_chars})",
        flush=True,
    )

    batch_texts: list[str] = []
    page_offset = 0
    for bi, batch in enumerate(batches):
        page_start = page_offset + 1
        page_end = page_offset + len(batch)
        page_offset = page_end
        label = f"Batch {bi + 1}/{len(batches)} (pages {page_start}-{page_end})"

        context = _format_batch_context(site, batch)
        full_prompt = (
            f"{prompt}\n\n"
            f"Below is content from {len(batch)} page(s) scraped from {site}.\n"
            "Use this content to answer the prompt above.\n\n"
            f"{context}"
        )
        t0 = time.monotonic()
        try:
            text = _call_mlx(
                model, tokenizer, full_prompt, max_tokens,
                max_kv_size=max_kv_size,
            )
        except Exception as e:
            print(f"  [{label}] ERROR: {e}", file=sys.stderr, flush=True)
            continue

        elapsed = time.monotonic() - t0
        print(
            f"  [{label}] OK ({len(text)} chars, {elapsed:.1f}s)",
            flush=True,
        )
        batch_texts.append(text)

    if not batch_texts:
        return "_All batches failed._"

    if synthesize and len(batch_texts) > 1:
        result = _hierarchical_synthesis(
            batch_texts, prompt, model, tokenizer,
            max_tokens, max_kv_size, max_context_chars,
        )
        if result is not None:
            return result

    if len(batch_texts) == 1:
        return batch_texts[0]

    return "\n\n---\n\n".join(batch_texts)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Batch AI analysis of stored webscrape pages using MLX locally.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              python scripts/webscrape_ai_batch_mlx.py \\
                --sites 'https://lambda.ai' \\
                --prompt 'List products and services'

              python scripts/webscrape_ai_batch_mlx.py \\
                --sites 'https://coreweave.com' \\
                --prompt 'Summarize differentiators' \\
                --model 'mlx-community/Mistral-7B-Instruct-v0.3-4bit' \\
                --max-pages 20 --no-synthesis

              # Fastest settings for quick iteration
              python scripts/webscrape_ai_batch_mlx.py \\
                --sites 'https://lambda.ai' \\
                --prompt 'List products' \\
                --model 'mlx-community/Llama-3.2-3B-Instruct-4bit' \\
                -b 3 --max-context-chars 20000 --max-tokens 512
        """),
    )
    ap.add_argument(
        "--sites",
        required=True,
        help="Comma-separated site URLs (e.g. 'https://a.com,https://b.com')",
    )
    ap.add_argument("--prompt", required=True, help="Prompt applied to each batch")
    ap.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"MLX model name from HuggingFace (default: {DEFAULT_MODEL})",
    )
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
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        metavar="N",
        help=f"Max tokens to generate per call (default: {DEFAULT_MAX_TOKENS})",
    )
    ap.add_argument(
        "--max-kv-size",
        type=int,
        default=None,
        metavar="N",
        help="Rotating KV cache size (tokens). Caps memory on very long contexts "
        "at the cost of quality. Try 4096 or 8192. (default: unlimited)",
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
        help="Registry URL for fetching pages (default: $REGISTRY_SERVER_URL or 127.0.0.1:7002)",
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
    worker_url = find_live_worker(registry)
    if not worker_url:
        print("Error: no live worker found in registry", file=sys.stderr)
        return 1
    worker_url = worker_url.rstrip("/")

    model_name = args.model
    batch_size = max(1, args.batch_size)
    max_context = max(1000, args.max_context_chars)
    max_content = max(100, args.max_content)
    max_tokens = max(64, args.max_tokens)
    max_kv_size: int | None = args.max_kv_size
    if max_kv_size is not None:
        max_kv_size = max(512, max_kv_size)
    synthesize = not args.no_synthesis

    est_mb = _estimate_model_mb(model_name)
    _try_set_wired_memory(est_mb)

    print(f"Loading model: {model_name} …", flush=True)
    t0 = time.monotonic()
    from mlx_lm import load
    model, tokenizer = load(model_name)
    load_time = time.monotonic() - t0
    print(f"Model loaded in {load_time:.1f}s.", flush=True)

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
            model=model,
            tokenizer=tokenizer,
            batch_size=batch_size,
            max_context_chars=max_context,
            max_tokens=max_tokens,
            max_kv_size=max_kv_size,
            synthesize=synthesize,
        )
        site_results.append((site, result))

    successful = [(s, r) for s, r in site_results if not r.startswith("_")]

    if synthesize and len(successful) > 1:
        print(f"\n{'='*60}", flush=True)
        print("Final synthesis across all sites …", flush=True)
        site_texts = [f"## {site}\n\n{text}" for site, text in successful]
        final_text = _hierarchical_synthesis(
            site_texts, prompt, model, tokenizer,
            max_tokens, max_kv_size, max_context,
        )
        if final_text is not None:
            full_output = f"# Batch AI Analysis\n\n**Prompt:** {prompt}\n\n{final_text}\n"
        else:
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
            rel = out_path.relative_to(ROOT)
        except ValueError:
            rel = out_path
        print(f"\nSaved to {rel}", file=sys.stderr, flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
