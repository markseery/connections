#!/usr/bin/env python3
"""
Exercise webscraper_skill: list stored URLs, run a scrape, list again, print diff.

Requires: registry, worker (webscraper_skill auto-loads on first request).

Usage:
  python scripts/webscrape_fetch.py https://example.com --namespace webscrape --max-pages 20 --max-depth 2
  python scripts/webscrape_fetch.py https://example.com --registry-url http://127.0.0.1:7002
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_env_path = ROOT / ".env"
if _env_path.is_file():
    from dotenv import load_dotenv

    load_dotenv(_env_path)

from common.skill_lifecycle import find_live_worker

SKILL = "webscraper_skill"
DEFAULT_NAMESPACE = "webscrape"
REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
POLL_INTERVAL_SEC = 1.5
SCRAPE_TIMEOUT = 600.0


def _urls_from_pages_response(payload: dict) -> list[str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw = data.get("urls")
    if not isinstance(raw, list):
        return []
    return sorted({str(u).strip() for u in raw if str(u).strip()})


def _print_url_block(title: str, urls: list[str]) -> None:
    print(title, flush=True)
    if not urls:
        print("  (none)", flush=True)
        return
    for u in urls:
        print(f"  {u}", flush=True)
    print(f"  Total: {len(urls)}", flush=True)


def fetch_stored_urls(client: httpx.Client, worker_url: str, namespace: str, sitename: str) -> list[str]:
    q = urlencode({"sitename": sitename, "namespace": namespace})
    r = client.get(f"{worker_url}/skills/{SKILL}/pages/urls?{q}")
    r.raise_for_status()
    return _urls_from_pages_response(r.json())


def start_scrape(
    client: httpx.Client,
    worker_url: str,
    *,
    url: str,
    namespace: str,
    max_pages: int,
    max_depth: int,
    summarize: bool,
) -> str:
    body = {
        "url": url.strip(),
        "namespace": namespace,
        "max_pages": max_pages,
        "max_depth": max_depth,
        "summarize": summarize,
    }
    r = client.post(f"{worker_url}/skills/{SKILL}/scrape", json=body, timeout=30.0)
    r.raise_for_status()
    data = r.json().get("data") if isinstance(r.json().get("data"), dict) else {}
    job_id = data.get("job_id") or r.json().get("job_id")
    if not job_id:
        raise RuntimeError(f"No job_id in scrape response: {r.text[:500]}")
    return str(job_id)


def wait_for_job(client: httpx.Client, worker_url: str, job_id: str, timeout_sec: float) -> dict:
    deadline = time.monotonic() + timeout_sec
    last: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"{worker_url}/skills/{SKILL}/scrape/{quote(job_id, safe='')}")
        r.raise_for_status()
        last = r.json()
        data = last.get("data") if isinstance(last.get("data"), dict) else {}
        status = (data.get("status") or "").lower()
        if status in ("completed", "failed"):
            return data
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout_sec}s; last={last!r}")


def main() -> int:
    p = argparse.ArgumentParser(description="List stored URLs, scrape via webscraper_skill, diff before/after.")
    p.add_argument(
        "sitename",
        help="Root URL to crawl and storage sitename (e.g. https://example.com)",
    )
    p.add_argument("--namespace", default=DEFAULT_NAMESPACE, help="Storage namespace (default: webscrape)")
    p.add_argument("--max-pages", type=int, default=30, dest="max_pages", help="max_pages for scrape job")
    p.add_argument("--max-depth", type=int, default=2, dest="max_depth", help="max_depth for scrape job")
    p.add_argument("--registry-url", default=REGISTRY_URL, help="Registry base URL")
    p.add_argument("--worker-url", default="", help="Worker base URL (default: discover via registry)")
    p.add_argument("--summarize", action="store_true", help="Request AI summary after crawl (slower)")
    p.add_argument(
        "--timeout",
        type=float,
        default=SCRAPE_TIMEOUT,
        help="Max seconds to wait for scrape job (default: 600)",
    )
    args = p.parse_args()

    sitename = args.sitename.strip()
    namespace = (args.namespace or "").strip() or DEFAULT_NAMESPACE
    if not sitename.startswith(("http://", "https://")):
        print("Error: sitename must be an http(s) URL", file=sys.stderr)
        return 1

    worker_url = (args.worker_url or "").strip().rstrip("/")
    if not worker_url:
        worker_url = find_live_worker(args.registry_url.rstrip("/"))
        if not worker_url:
            print("Error: No live worker in registry", file=sys.stderr)
            return 1
        worker_url = worker_url.rstrip("/")

    try:
        with httpx.Client(timeout=120.0) as client:
            before = fetch_stored_urls(client, worker_url, namespace, sitename)
            _print_url_block(f"=== Stored URLs before scrape (namespace={namespace!r}, sitename={sitename!r}) ===", before)

            print("\nStarting scrape...", flush=True)
            job_id = start_scrape(
                client,
                worker_url,
                url=sitename,
                namespace=namespace,
                max_pages=args.max_pages,
                max_depth=args.max_depth,
                summarize=args.summarize,
            )
            print(f"  job_id: {job_id}", flush=True)

            job = wait_for_job(client, worker_url, job_id, args.timeout)
            status = (job.get("status") or "").lower()
            if status == "failed":
                err = job.get("error") or "unknown error"
                print(f"Error: scrape job failed: {err}", file=sys.stderr)
                return 1

            after = fetch_stored_urls(client, worker_url, namespace, sitename)
            _print_url_block(f"\n=== Stored URLs after scrape (namespace={namespace!r}, sitename={sitename!r}) ===", after)

            bset, aset = set(before), set(after)
            added = sorted(aset - bset)
            removed = sorted(bset - aset)

            print("\n=== Diff (after vs before) ===", flush=True)
            _print_url_block("New URLs (stored now, not in initial list):", added)
            print("", flush=True)
            _print_url_block("Removed URLs (were stored before, missing now):", removed)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
