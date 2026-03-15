"""
License: MIT
Description: Process a list of RSS/Atom feed URLs via the rss_skill (worker).
Fetches each feed, collects normalized JSON, and optionally writes a summary or
full results to a file.

Requires: registry, worker (with rss_skill loadable).

Usage:
  python process_rss_feeds.py
  python process_rss_feeds.py --out feeds_summary.json
  python process_rss_feeds.py --out results.json --full
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

from common.skill_lifecycle import find_live_worker


REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
FEED_TIMEOUT = 45.0

DEFAULT_FEEDS = [
    "https://research.google/blog/rss/",
    "https://deepmind.google/blog/rss.xml",
    "https://openai.com/news/rss.xml",
    "https://bair.berkeley.edu/blog/feed.xml",
    "https://news.mit.edu/rss/feed",
    "https://news.mit.edu/rss/research",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "https://www.kdnuggets.com/feed",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.ft.com/artificial-intelligence?format=rss",
    "https://arstechnica.com/ai/feed/",
    "https://news.microsoft.com/source/topics/ai/feed/",
    "https://aws.amazon.com/blogs/aws/category/artificial-intelligence/feed/",
    "https://www.infoworld.com/artificial-intelligence/feed/",
    "https://www.computerworld.com/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://news.crunchbase.com/sections/ai/feed/",
]


def _worker_url() -> str:
    url = find_live_worker(REGISTRY_URL)
    if not url:
        raise RuntimeError("No live worker found in registry")
    return url.rstrip("/")


def _ensure_skill_loaded(worker_url: str) -> None:
    with httpx.Client(timeout=10.0) as client:
        r = client.post(f"{worker_url}/worker/skills/rss_skill/load")
        r.raise_for_status()


def fetch_feed(worker_url: str, feed_url: str) -> dict[str, Any]:
    """Call rss_skill to fetch and parse one feed. Returns normalized JSON or error payload."""
    with httpx.Client(timeout=FEED_TIMEOUT) as client:
        r = client.post(
            f"{worker_url}/skills/rss_skill/feed",
            json={"url": feed_url},
        )
        if r.is_success:
            return {"ok": True, "url": feed_url, "data": r.json()}
        return {
            "ok": False,
            "url": feed_url,
            "status_code": r.status_code,
            "detail": r.text[:500] if r.text else None,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Process RSS feeds via rss_skill")
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Write results to this file (JSON). Default: print summary only.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Include full feed data (feed + items) in output; otherwise summary only.",
    )
    parser.add_argument(
        "feeds",
        nargs="*",
        default=None,
        help="Feed URLs (default: built-in AI/tech feed list).",
    )
    args = parser.parse_args()

    try:
        worker_url = _worker_url()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    _ensure_skill_loaded(worker_url)
    feeds = args.feeds if args.feeds else DEFAULT_FEEDS
    print(f"Processing {len(feeds)} feed(s) via {worker_url} ...", flush=True)

    results: list[dict[str, Any]] = []
    for i, feed_url in enumerate(feeds, 1):
        print(f"  [{i}/{len(feeds)}] {feed_url}", flush=True)
        try:
            out = fetch_feed(worker_url, feed_url)
            results.append(out)
            if out.get("ok"):
                data = out.get("data") or {}
                count = data.get("item_count", 0)
                title = (data.get("feed") or {}).get("title", "?")
                print(f"       -> {count} items: {title[:60]}{'...' if len(title) > 60 else ''}", flush=True)
            else:
                print(f"       -> FAILED {out.get('status_code')} {out.get('detail', '')[:80]}", flush=True)
        except Exception as e:
            results.append({"ok": False, "url": feed_url, "error": str(e)})
            print(f"       -> ERROR {e}", flush=True)

    # Build output
    ok_count = sum(1 for r in results if r.get("ok"))
    summary = {
        "total_feeds": len(feeds),
        "ok": ok_count,
        "failed": len(results) - ok_count,
        "results": [
            {
                "url": r["url"],
                "ok": r.get("ok", False),
                "item_count": (r.get("data") or {}).get("item_count") if r.get("ok") else None,
                "feed_title": ((r.get("data") or {}).get("feed") or {}).get("title") if r.get("ok") else None,
                "status_code": r.get("status_code"),
                "error": r.get("error") or (r.get("detail") if not r.get("ok") else None),
            }
            for r in results
        ],
    }
    if args.full:
        summary["full_results"] = results

    out_json = json.dumps(summary, indent=2)

    if args.out:
        with open(args.out, "w") as f:
            f.write(out_json)
        print(f"Wrote {args.out}", flush=True)
    else:
        print(out_json, flush=True)

    return 0 if ok_count == len(feeds) else 1


if __name__ == "__main__":
    sys.exit(main())
