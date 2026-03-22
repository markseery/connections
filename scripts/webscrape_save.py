#!/usr/bin/env python3
"""
Save all stored webscrape content for a site to a markdown file, page by page.

Usage:
  python scripts/webscrape_save.py https://iren.com
  python scripts/webscrape_save.py https://iren.com --out iren_content.md
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.stored_site_content import StoredSiteContent

DEFAULT_REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
OUTPUT_DIR = ROOT / "data" / "exports"


def _list_available_sites(registry_url: str) -> list[str]:
    try:
        import httpx
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Save stored webscrape content to a markdown file.")
    ap.add_argument("site", help="Site URL (must already be scraped and stored)")
    ap.add_argument(
        "--out", default=None, metavar="FILE",
        help="Output file path (default: data/exports/<host>_<timestamp>.md)",
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
    worker_url = (args.worker_url or "").strip().rstrip("/") if args.worker_url else None

    content = StoredSiteContent(site, worker_url=worker_url, registry_url=registry_url)
    try:
        content.load()
    except Exception as e:
        available = _list_available_sites(registry_url)
        print(f"Error: {e}", file=sys.stderr)
        if available:
            print(f"Available sites: {', '.join(available)}", file=sys.stderr)
        return 1

    total = len(content)
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

    for i, (url, page_content) in enumerate(content):
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
            out_path = ROOT / out_path
    else:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = OUTPUT_DIR / f"{host}_{ts}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"Saved {limit} pages ({len(md):,} chars) → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
