#!/usr/bin/env python3
"""
Process pages of a stored site through the AI server with a given prompt.

Gets pages via StoredSiteContent (worker + webscraper_skill), batches them,
sends each batch to the aiserver /generate with the provided prompt, prints each response.

Usage:
  python scripts/site_pages_ai.py https://example.com "Summarize this page in one paragraph."
  python scripts/site_pages_ai.py https://example.com "List key facts." --max-pages 5
  python scripts/site_pages_ai.py https://example.com "Extract facts." --taxonomy fact_taxonomy.yaml
  python scripts/site_pages_ai.py https://example.com "Extract facts." --batch-size 10
  python scripts/site_pages_ai.py https://example.com "Extract facts." --batch-size 20 --max-context-chars 60000

Requires: registry, worker (webscraper_skill), aiserver. Site must already be scraped and stored.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.json_repair import extract_brace_block, repair_json
from common.stored_site_content import StoredSiteContent

DEFAULT_REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
TAXONOMY_DIR = ROOT / "data" / "taxonomy"
FACTS_OUTPUT_DIR = ROOT / "data" / "facts"
AI_TIMEOUT = 300.0
PROFILE = "agent"
DEFAULT_BATCH_SIZE = 1
DEFAULT_MAX_CONTEXT_CHARS = 80_000


def load_taxonomy(path: Path) -> dict[str, Any]:
    """Load taxonomy YAML; return dict with 'dimensions' (name -> {values: [...]})."""
    if not path.is_file():
        raise FileNotFoundError(f"Taxonomy file not found: {path}")
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or "dimensions" not in data:
        raise ValueError("Taxonomy file must contain a top-level 'dimensions' key")
    return data


def taxonomy_to_prompt_instruction(taxonomy: dict[str, Any]) -> str:
    """Build instruction text: respond with JSON adhering to the taxonomy."""
    dimensions = taxonomy.get("dimensions") or {}
    if not dimensions:
        return ""
    lines = [
        "",
        "Respond with a JSON object only. Your response must adhere to the following taxonomy (use only these allowed values for each field):",
        "",
    ]
    for name, spec in dimensions.items():
        if not isinstance(spec, dict):
            continue
        values = spec.get("values")
        if not isinstance(values, list) or not values:
            continue
        vals = []
        for v in values:
            if isinstance(v, dict) and "id" in v:
                vals.append(str(v["id"]))
            else:
                vals.append(str(v))
        lines.append(f"  - {name}: {', '.join(repr(v) for v in vals)}")
    if len(lines) <= 3:
        return ""
    lines.append("")
    return "\n".join(lines)


def _list_available_sites(worker_or_registry_url: str, registry_url: str) -> list[str]:
    """Best-effort fetch of stored site names from webscraper_skill."""
    try:
        from common.skill_lifecycle import find_live_worker
        wurl = find_live_worker(registry_url)
        if not wurl:
            return []
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{wurl.rstrip('/')}/skills/webscraper_skill/sites")
            if r.status_code != 200:
                return []
            items = r.json().get("items") or []
            return [it.get("title") or it.get("link") or "" for it in items if isinstance(it, dict)]
    except Exception:
        return []


def get_aiserver_url(registry_url: str) -> str:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{registry_url.rstrip('/')}/servers/aiserver")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError("Registry missing url for aiserver")
        return str(url).rstrip("/")


def call_ai(aiserver_url: str, prompt: str, profile: str) -> dict:
    with httpx.Client(timeout=AI_TIMEOUT) as client:
        r = client.post(
            f"{aiserver_url}/generate",
            json={"prompt": prompt, "profile": profile},
        )
        r.raise_for_status()
    return r.json()


def extract_text(response: dict) -> str:
    out = response.get("output")
    if isinstance(out, dict) and "text" in out:
        return str(out["text"]).strip()
    if isinstance(out, str):
        return out.strip()
    return str(response.get("output", response)).strip()


def _strip_json_like(text: str) -> str:
    """Remove markdown/code fences (``` or ''') and trim."""
    t = text.strip()
    # ```json ... ``` or ``` ... ```
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\s*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t).strip()
    # ''' ... '''
    if t.startswith("'''"):
        t = re.sub(r"^'''\s*\n?", "", t)
        t = re.sub(r"\n?'''\s*$", "", t).strip()
    return t


def _extract_array_block(text: str) -> str | None:
    """Return first top-level [...] block (brace-depth and string-aware)."""
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    in_str = False
    quote = None
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_json_block(text: str) -> str | None:
    """Return first top-level {...} or [...] block. Prefer array when text starts with '['."""
    t = _strip_json_like(text)
    t_trim = t.lstrip()
    # Prefer array when response is a top-level array (e.g. list of facts)
    if t_trim.startswith("["):
        block = _extract_array_block(t)
        if block is not None:
            return block
    # Otherwise try object first, then array
    block = extract_brace_block(t)
    if block is not None:
        return block
    return _extract_array_block(t)


def parse_facts_json(raw: str) -> list[dict[str, Any]]:
    """
    Parse raw AI response into a list of fact dicts. Handles malformed JSON:
    strips ```/''', extracts {...} or [...], repairs and normalizes.
    Returns empty list on parse failure.
    """
    block = _extract_json_block(raw)
    if not block:
        return []
    # Try parse
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        if block.strip().startswith("{"):
            try:
                data = json.loads(repair_json(block))
            except json.JSONDecodeError:
                return []
        else:
            return []
    # Normalize to list of fact objects
    if isinstance(data, list):
        facts = [f for f in data if isinstance(f, dict)]
    elif isinstance(data, dict):
        if "facts" in data and isinstance(data["facts"], list):
            facts = [f for f in data["facts"] if isinstance(f, dict)]
        else:
            # Single fact object
            facts = [data] if data else []
    else:
        facts = []
    return facts


def _build_batches(
    pages: list[tuple[str, str]],
    batch_size: int,
    max_context_chars: int,
) -> list[list[tuple[str, str]]]:
    """Group pages into batches respecting both batch_size and max_context_chars."""
    batches: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_chars = 0
    for url, content in pages:
        page_chars = len(url) + len(content) + 30
        if current and (len(current) >= batch_size or current_chars + page_chars > max_context_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append((url, content))
        current_chars += page_chars
    if current:
        batches.append(current)
    return batches


def _fact_canonical(fact: dict[str, Any]) -> str:
    """Canonical string for deduplication (sorted keys, stable repr)."""
    return json.dumps(fact, sort_keys=True, ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run AI (agent profile) on each page of a stored site with a given prompt."
    )
    ap.add_argument("site", help="Base URL of the site (must already be scraped and stored)")
    ap.add_argument("prompt", help="Prompt to send for each page (page content is appended as context)")
    ap.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="Limit to first N pages (default: all)",
    )
    ap.add_argument(
        "--registry-url",
        default=DEFAULT_REGISTRY_URL,
        help="Registry URL (default: REGISTRY_SERVER_URL or 127.0.0.1:7002)",
    )
    ap.add_argument(
        "--worker-url",
        default=None,
        help="Worker base URL (default: from registry)",
    )
    ap.add_argument(
        "--taxonomy",
        default=None,
        metavar="FILE",
        help="Taxonomy YAML file (e.g. fact_taxonomy.yaml or data/taxonomy/fact_taxonomy.yaml). Responses must be JSON adhering to its dimensions.",
    )
    ap.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help="Write deduplicated facts to this JSON file (default: data/facts/<site>_facts_<timestamp>.json)",
    )
    ap.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help=f"Pages per AI call (default: {DEFAULT_BATCH_SIZE}). Higher = fewer calls, but larger context.",
    )
    ap.add_argument(
        "--max-context-chars",
        type=int,
        default=DEFAULT_MAX_CONTEXT_CHARS,
        metavar="CHARS",
        help=f"Max context chars per batch (default: {DEFAULT_MAX_CONTEXT_CHARS}). Batch is sent early if exceeded.",
    )
    args = ap.parse_args()

    taxonomy_instruction = ""
    if args.taxonomy:
        tax_path = Path(args.taxonomy.strip())
        if not tax_path.is_absolute():
            if not tax_path.suffix:
                tax_path = tax_path.with_suffix(".yaml")
            if not tax_path.is_file():
                tax_path = TAXONOMY_DIR / tax_path.name
        try:
            taxonomy = load_taxonomy(tax_path)
            taxonomy_instruction = taxonomy_to_prompt_instruction(taxonomy)
        except Exception as e:
            print(f"Taxonomy: {e}", file=sys.stderr)
            return 1

    site = (args.site or "").strip().rstrip("/")
    if not site:
        print("site is required", file=sys.stderr)
        return 1
    prompt_text = (args.prompt or "").strip()
    if not prompt_text:
        print("prompt is required", file=sys.stderr)
        return 1

    registry_url = (args.registry_url or DEFAULT_REGISTRY_URL).rstrip("/")
    try:
        aiserver_url = get_aiserver_url(registry_url)
    except Exception as e:
        print(f"Aiserver discovery: {e}", file=sys.stderr)
        return 1

    worker_url = (args.worker_url or "").strip().rstrip("/") if args.worker_url else None
    content = StoredSiteContent(site, worker_url=worker_url, registry_url=registry_url)
    try:
        content.load()
    except Exception as e:
        available = _list_available_sites(worker_url or registry_url, registry_url)
        print(f"Error: {e}", file=sys.stderr)
        if available:
            print(f"Available sites: {', '.join(available)}", file=sys.stderr)
        else:
            print("No stored sites found. Scrape a site first (webscraper_skill POST /scrape).", file=sys.stderr)
        return 1

    total = len(content)
    if total == 0:
        print("No pages in stored site.", file=sys.stderr)
        return 0

    max_pages = args.max_pages
    if max_pages is not None and max_pages < 1:
        max_pages = None
    limit = max_pages if max_pages is not None else total
    batch_size = max(1, args.batch_size)
    max_context = max(1000, args.max_context_chars)

    pages: list[tuple[str, str]] = []
    for i, (url, page_content) in enumerate(content):
        if i >= limit:
            break
        pages.append((url, page_content or ""))

    batches = _build_batches(pages, batch_size, max_context)
    print(f"[site_pages_ai] {len(pages)} pages → {len(batches)} batch(es) (batch_size={batch_size}, max_context={max_context})", flush=True)

    all_facts: list[dict[str, Any]] = []
    seen_canonical: set[str] = set()

    page_offset = 0
    for bi, batch in enumerate(batches):
        context_blocks: list[str] = []
        for url, page_content in batch:
            context_blocks.append(f"URL: {url}\n\n{page_content}")
        context_text = "\n\n--- Next Page ---\n\n".join(context_blocks)
        page_start = page_offset + 1
        page_end = page_offset + len(batch)
        page_offset = page_end

        full_prompt = (
            "Use the following context when answering. "
            f"The context contains {len(batch)} page(s) from {site}.\n\n"
            "--- Context ---\n"
            f"{context_text}\n\n"
            "--- End context ---\n\n"
            f"{prompt_text}"
            f"{taxonomy_instruction}"
        )
        label = f"Batch {bi + 1}/{len(batches)} (pages {page_start}-{page_end})"
        try:
            response = call_ai(aiserver_url, full_prompt, PROFILE)
            text = extract_text(response)
        except Exception as e:
            urls = [u for u, _ in batch]
            print(f"[{label}] Error: {e} — urls: {urls}", file=sys.stderr)
            print()
            continue
        print(f"--- {label} ---")
        print(text)
        print()

        facts = parse_facts_json(text)
        for fact in facts:
            canonical = _fact_canonical(fact)
            if canonical not in seen_canonical:
                seen_canonical.add(canonical)
                all_facts.append(fact)

    # Print and save deduplicated facts
    payload = {"facts": all_facts, "count": len(all_facts)}
    if all_facts:
        print("--- All facts (deduplicated) ---")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        # Save to JSON file
        out_path = args.out
        if out_path is None:
            FACTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            try:
                host = urlparse(site).netloc or "site"
                host = re.sub(r"[^\w.-]", "_", host).strip("_") or "site"
            except Exception:
                host = "site"
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            out_path = FACTS_OUTPUT_DIR / f"{host}_facts_{ts}.json"
        else:
            out_path = Path(out_path)
            if not out_path.is_absolute():
                out_path = ROOT / out_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved to {out_path}", file=sys.stderr)
    else:
        print("--- No facts collected ---", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
