#!/usr/bin/env python3
"""
Save all stored webscrape content for a site to a markdown file, page by page.

Usage:
  python scripts/webscrape_save.py https://iren.com
  python scripts/webscrape_save.py https://iren.com --out iren_content.md
    # writes data/webscrape/sites/iren_content.md when --out is relative
  python scripts/webscrape_save.py https://iren.com --max-pages 20

Requires: registry, worker (webscraper_skill). Site must already be scraped.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

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


DEFAULT_REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "webscrape" / "sites"

_PAGE_SEP = "\n\n---\n\n"
_URL_PREFIX = "URL: "


def _strip_url_query(url: str) -> str:
    u = (url or "").strip()
    q = u.find("?")
    if q >= 0:
        u = u[:q]
    return u


def _parse_combined_text(combined_text: str) -> list[tuple[str, str]]:
    if not (combined_text or "").strip():
        return []
    segments = combined_text.strip().split(_PAGE_SEP)
    pages: list[tuple[str, str]] = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if seg.startswith(_URL_PREFIX):
            first_newline = seg.find("\n")
            if first_newline >= 0:
                url = seg[len(_URL_PREFIX) : first_newline].strip()
                content = seg[first_newline :].strip()
            else:
                url = seg[len(_URL_PREFIX) :].strip()
                content = ""
            pages.append((url, content))
        elif pages:
            last_url, last_content = pages[-1]
            pages[-1] = (last_url, (last_content + "\n\n" + seg).strip())
    return pages


def _list_available_sites(registry_url: str) -> list[str]:
    try:
        wurl = _find_worker(registry_url)
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{wurl.rstrip('/')}/skills/webscraper_skill/sites")
            if r.status_code != 200:
                return []
            items = r.json().get("items") or []
            return [it.get("title") or it.get("link") or "" for it in items if isinstance(it, dict)]
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Save stored webscrape content to a markdown file.")
    ap.add_argument("site", help="Site URL (must already be scraped and stored)")
    ap.add_argument(
        "--namespace", default="webscrape",
        help="Storage namespace (default: webscrape)",
    )
    ap.add_argument(
        "--out", default=None, metavar="FILE",
        help="Output file path. Relative paths go under data/webscrape/sites/. "
        "Default: data/webscrape/sites/<host>_<timestamp>.md",
    )
    ap.add_argument(
        "--max-pages", type=int, default=None, metavar="N",
        help="Limit to first N pages (default: all)",
    )
    ap.add_argument(
        "--registry-url", default=DEFAULT_REGISTRY_URL,
        help="Registry URL (default: REGISTRY_SERVER_URL or 127.0.0.1:7002)",
    )
    ap.add_argument(
        "--worker-url", default=None,
        help="Worker base URL (default: from registry)",
    )
    args = ap.parse_args()

    site = (args.site or "").strip().rstrip("/")
    if not site:
        print("site is required", file=sys.stderr)
        return 1

    registry_url = (args.registry_url or DEFAULT_REGISTRY_URL).rstrip("/")
    worker_url_opt = (args.worker_url or "").strip().rstrip("/") if args.worker_url else None

    namespace = (args.namespace or "").strip() or "webscrape"
    base = _strip_url_query(site).rstrip("/")

    try:
        wurl = worker_url_opt or _find_worker(registry_url).rstrip("/")
        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                f"{wurl}/skills/webscraper_skill/pages/content",
                params={"sitename": base, "namespace": namespace},
            )
            if r.status_code == 404:
                raise ValueError(
                    f"No stored scrape found for {base!r}. "
                    "Scrape the site first (e.g. webscraper_skill POST /scrape)."
                )
            r.raise_for_status()
            data = r.json()
        combined = data.get("data", {}).get("combined_text") or data.get("combined_text") or ""
        pages = _parse_combined_text(combined)
        if not pages:
            raise ValueError(
                f"No stored pages found for {base!r}. "
                "Scrape the site first (e.g. webscraper_skill POST /scrape)."
            )
    except Exception as e:
        available = _list_available_sites(registry_url)
        print(f"Error: {e}", file=sys.stderr)
        if available:
            print(f"Available sites: {', '.join(available)}", file=sys.stderr)
        return 1

    total = len(pages)
    if total == 0:
        print("No pages found.", file=sys.stderr)
        return 0

    limit = total
    if args.max_pages is not None and args.max_pages >= 1:
        limit = min(args.max_pages, total)

    try:
        host = urlparse(site).netloc or "site"
        host = re.sub(r"[^\w.-]", "_", host).strip("_") or "site"
    except Exception:
        host = "site"

    lines: list[str] = []
    lines.append(f"# {site}")
    lines.append("")
    lines.append(f"*{limit} of {total} pages — exported {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append("")

    for i, (url, page_content) in enumerate(pages):
        if i >= limit:
            break
        lines.append("---")
        lines.append("")
        lines.append(f"## {url}")
        lines.append("")
        lines.append(page_content.strip() if page_content else "*(empty page)*")
        lines.append("")

    md = "\n".join(lines)

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = OUTPUT_DIR / out_path
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = OUTPUT_DIR / f"{host}_{ts}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    _app = Path(__file__).resolve().parents[1]
    try:
        display_path = str(out_path.relative_to(_app))
    except ValueError:
        display_path = str(out_path)
    print(f"Saved {limit} pages ({len(md):,} chars) → {display_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
