#!/usr/bin/env python3
"""
Run AI on pages from a webscrape markdown export and collect JSON facts.

Input markdown is read from data/webscrape/sites/ (see webscrape_save.py). Output JSON
is written under data/webscrape/facts/. Use --ai-strength to set the aiserver profile
(fast / chat / reason / agent).

Usage:
  python scripts/webscrape_site_facts.py nebius.md "Extract structured facts as JSON."
  python scripts/webscrape_site_facts.py nebius.md "Extract facts." --ai-strength reason
  python scripts/webscrape_site_facts.py nebius.md "Extract facts." -b 10
  python scripts/webscrape_site_facts.py data/webscrape/sites/export.md "Summarize." --ai-strength fast

Each AI request includes up to `--batch-size` pages (one combined context); batches also
split early if `--max-context-chars` would be exceeded.

Requires: aiserver (registry for discovery). No worker needed if input file exists.
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

_APP_ROOT = Path(__file__).resolve().parents[1]
SCRAPES_SITES_DIR = _APP_ROOT / "data" / "webscrape" / "sites"
FACTS_OUTPUT_DIR = _APP_ROOT / "data" / "webscrape" / "facts"
TAXONOMY_DIR = _APP_ROOT / "data" / "taxonomy"
DEFAULT_REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
AI_TIMEOUT = 300.0
DEFAULT_AI_STRENGTH = "agent"
AI_STRENGTH_CHOICES = ("fast", "chat", "reason", "agent")
# Pages combined into a single /generate call until batch_size or max_context_chars is hit.
DEFAULT_BATCH_SIZE = 5
DEFAULT_MAX_CONTEXT_CHARS = 80_000


def extract_brace_block(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "\"":
                in_str = False
            continue
        if ch == "\"":
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _absorb_orphans(gap: str, objects: list[dict[str, Any]]) -> None:
    if not objects:
        return
    for m in re.finditer(
        r'"(\w+)"\s*:\s*(\[[^\]]*\]|"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null)',
        gap,
    ):
        key = m.group(1)
        val_str = m.group(2)
        try:
            val = json.loads(val_str)
        except Exception as exc:
            print(f"[json_repair] orphan value parse failed for key={key}: {exc}", flush=True)
            continue
        objects[-1][key] = val


def repair_json(text: str) -> str:
    top_fields: dict[str, Any] = {}
    for m in re.finditer(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"', text):
        key = m.group(1)
        if key in {"objective", "use_skill"}:
            top_fields[key] = m.group(2)

    array_match = re.search(r'"(\w+)"\s*:\s*\[', text)
    if not array_match:
        return text

    array_key = array_match.group(1)
    remainder = text[array_match.end():]
    objects: list[dict[str, Any]] = []

    pos = 0
    while pos < len(remainder):
        brace_start = remainder.find("{", pos)
        if brace_start == -1:
            _absorb_orphans(remainder[pos:], objects)
            break

        _absorb_orphans(remainder[pos:brace_start], objects)

        block = extract_brace_block(remainder[brace_start:])
        if block is None:
            break
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                objects.append(obj)
        except json.JSONDecodeError as exc:
            print(f"[json_repair] skipping malformed block in repair: {exc}", flush=True)
        pos = brace_start + len(block)

    result: dict[str, Any] = {**top_fields, array_key: objects}
    return json.dumps(result, ensure_ascii=False)


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


def parse_scrape_export_markdown(md: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Parse markdown written by webscrape_save.py: header # site, then --- / ## url / body blocks.
    """
    parts = re.split(r"\n---\n", md)
    site = ""
    pages: list[tuple[str, str]] = []
    if parts:
        first = parts[0].strip()
        first_line = first.split("\n", 1)[0] if first else ""
        if first_line.startswith("# "):
            site = first_line[2:].strip()
    for seg in parts[1:]:
        seg = seg.strip()
        if not seg.startswith("## "):
            continue
        nl = seg.find("\n")
        if nl == -1:
            url = seg[3:].strip()
            content = ""
        else:
            url = seg[3:nl].strip()
            content = seg[nl:].strip()
        pages.append((url, content))
    return site, pages


def resolve_input_path(arg: str) -> Path:
    """Resolve input file: absolute path, cwd-relative, or under data/webscrape/sites/."""
    raw = (arg or "").strip()
    if not raw:
        raise FileNotFoundError("input path is empty")

    p = Path(raw)
    if p.is_absolute():
        if p.is_file():
            return p
        raise FileNotFoundError(f"Input file not found: {p}")

    if p.is_file():
        return p.resolve()

    cand = SCRAPES_SITES_DIR / raw
    if cand.is_file():
        return cand

    if not raw.endswith(".md"):
        cand_md = SCRAPES_SITES_DIR / f"{raw}.md"
        if cand_md.is_file():
            return cand_md

    rel = _APP_ROOT / raw
    if rel.is_file():
        return rel.resolve()

    raise FileNotFoundError(
        f"Input file not found: {raw!r}. Looked under {SCRAPES_SITES_DIR} and {_APP_ROOT}."
    )


def get_aiserver_url(registry_url: str) -> str:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{registry_url.rstrip('/')}/servers/aiserver")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError("Registry missing url for aiserver")
        return str(url).rstrip("/")


def call_ai(aiserver_url: str, prompt: str, *, profile: str) -> dict:
    """POST /generate with aiserver profile (maps from --ai-strength)."""
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
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\s*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t).strip()
    if t.startswith("'''"):
        t = re.sub(r"^'''\s*\n?", "", t)
        t = re.sub(r"\n?'''\s*$", "", t).strip()
    return t


def _extract_array_block(text: str) -> str | None:
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
    t = _strip_json_like(text)
    t_trim = t.lstrip()
    if t_trim.startswith("["):
        block = _extract_array_block(t)
        if block is not None:
            return block
    block = extract_brace_block(t)
    if block is not None:
        return block
    return _extract_array_block(t)


def parse_facts_json(raw: str) -> list[dict[str, Any]]:
    block = _extract_json_block(raw)
    if not block:
        return []
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
    if isinstance(data, list):
        facts = [f for f in data if isinstance(f, dict)]
    elif isinstance(data, dict):
        if "facts" in data and isinstance(data["facts"], list):
            facts = [f for f in data["facts"] if isinstance(f, dict)]
        else:
            facts = [data] if data else []
    else:
        facts = []
    return facts


def _build_batches(
    pages: list[tuple[str, str]],
    batch_size: int,
    max_context_chars: int,
) -> list[list[tuple[str, str]]]:
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
    return json.dumps(fact, sort_keys=True, ensure_ascii=False)


def _path_display(path: Path) -> str:
    try:
        return str(path.relative_to(_APP_ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Run AI on a webscrape markdown export (data/webscrape/sites) and save facts JSON. "
            "Use --batch-size to send multiple pages in one AI request (same prompt, shared context)."
        )
    )
    ap.add_argument(
        "input",
        help="Markdown file: name under data/webscrape/sites/, or path",
    )
    ap.add_argument("prompt", help="Prompt (page content from file is appended as context)")
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
        "--taxonomy",
        default=None,
        metavar="FILE",
        help="Taxonomy YAML (e.g. fact_taxonomy.yaml or data/taxonomy/...)",
    )
    ap.add_argument(
        "--out",
        default=None,
        metavar="FILE",
        help="Facts JSON path. Relative paths go under data/webscrape/facts/. "
        "Default: data/webscrape/facts/<input_stem>_facts_<timestamp>.json",
    )
    ap.add_argument(
        "-b",
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help=(
            f"Max pages to include in one AI request (default: {DEFAULT_BATCH_SIZE}). "
            "Pages are concatenated with separators; use with --max-context-chars if batches get too large."
        ),
    )
    ap.add_argument(
        "--max-context-chars",
        type=int,
        default=DEFAULT_MAX_CONTEXT_CHARS,
        metavar="CHARS",
        help=f"Max context chars per batch (default: {DEFAULT_MAX_CONTEXT_CHARS})",
    )
    ap.add_argument(
        "--ai-strength",
        choices=list(AI_STRENGTH_CHOICES),
        default=DEFAULT_AI_STRENGTH,
        metavar="LEVEL",
        help=(
            "Model capability tier sent to aiserver as `profile` "
            "(fast < chat < reason < agent; default: agent)."
        ),
    )
    args = ap.parse_args()

    ai_profile = args.ai_strength

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

    try:
        input_path = resolve_input_path(args.input)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        if SCRAPES_SITES_DIR.is_dir():
            names = sorted(p.name for p in SCRAPES_SITES_DIR.iterdir() if p.suffix == ".md")
            if names:
                print(f"Markdown files in {SCRAPES_SITES_DIR.relative_to(_APP_ROOT)}: {', '.join(names[:20])}", file=sys.stderr)
        return 1

    md = input_path.read_text(encoding="utf-8")
    site, pages = parse_scrape_export_markdown(md)
    if not site:
        site = input_path.stem

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

    total = len(pages)
    if total == 0:
        print("No pages found in markdown (expected webscrape_save.py export format).", file=sys.stderr)
        return 1

    max_pages = args.max_pages
    if max_pages is not None and max_pages < 1:
        max_pages = None
    limit = max_pages if max_pages is not None else total
    batch_size = max(1, args.batch_size)
    max_context = max(1000, args.max_context_chars)

    pages = pages[:limit]
    batches = _build_batches(pages, batch_size, max_context)
    print(
        f"[webscrape_site_facts] {_path_display(input_path)}: "
        f"{len(pages)} pages → {len(batches)} batch(es) "
        f"(batch_size={batch_size}, max_context={max_context}, ai_strength={ai_profile})",
        flush=True,
    )

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
            response = call_ai(aiserver_url, full_prompt, profile=ai_profile)
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

    payload = {
        "facts": all_facts,
        "count": len(all_facts),
        "source_file": _path_display(input_path),
        "site": site,
        "ai_strength": ai_profile,
        "batch_size": batch_size,
        "batch_count": len(batches),
        "max_context_chars": max_context,
    }
    if all_facts:
        print("--- All facts (deduplicated) ---")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        if args.out is None:
            FACTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            try:
                host = urlparse(site).netloc or input_path.stem
                host = re.sub(r"[^\w.-]", "_", host).strip("_") or input_path.stem
            except Exception:
                host = input_path.stem
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            out_path = FACTS_OUTPUT_DIR / f"{host}_facts_{ts}.json"
        else:
            out_path = Path(args.out)
            if not out_path.is_absolute():
                out_path = FACTS_OUTPUT_DIR / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved to {_path_display(out_path)}", file=sys.stderr)
    else:
        print("--- No facts collected ---", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
