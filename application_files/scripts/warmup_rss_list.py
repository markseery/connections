#!/usr/bin/env python3
"""
Warmup script for an RSS feed list: repeatedly fetches new items from all feeds
in the list and saves their IDs to storage (rss_notified) until no new items remain.
No summarization or other processing; use to prefill storage so the first notify
run doesn't process a huge backlog.

Usage:
  python scripts/warmup_rss_list.py LIST_NAME [--worker-url URL] [--registry-url URL]
  python scripts/warmup_rss_list.py cloud-news
  python scripts/warmup_rss_list.py cloud-news --worker-url http://127.0.0.1:7001

Requires: registry (unless --worker-url), storage server, worker with rss_new_skill
and rss_new_and_save_skill; feed list at data/lists/{LIST_NAME}.json.
"""

from __future__ import annotations

import argparse
import sys
import time

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


SKILL_NAME = "rss_new_and_save_skill"
SKILL_TIMEOUT = 600.0


def get_worker_url(registry_url: str) -> str:
    try:
        return _find_worker(registry_url.rstrip("/"))
    except RuntimeError:
        raise SystemExit("No live worker found in registry")


def run_fetch_and_save(worker_url: str, list_name: str, debug: bool = True) -> dict:
    payload = {
        "list_name": list_name.strip(),
        "dry_run": False,
        "worker_url": worker_url,
        "debug": debug,
        "warmup": True,  # save link IDs only; no content fetch
    }
    with httpx.Client(timeout=SKILL_TIMEOUT) as client:
        r = client.post(f"{worker_url}/skills/{SKILL_NAME}/run", json=payload)
        r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Warmup RSS list: fetch all items from feeds and save to storage until no new items."
    )
    ap.add_argument(
        "list_name",
        metavar="LIST_NAME",
        help="Feed list name (e.g. cloud-news); uses data/lists/<LIST_NAME>.json",
    )
    ap.add_argument(
        "--worker-url",
        metavar="URL",
        default="",
        help="Worker base URL; if omitted, discovered from registry",
    )
    ap.add_argument(
        "--registry-url",
        metavar="URL",
        default="http://127.0.0.1:7002",
        help="Registry URL for worker discovery (default: http://127.0.0.1:7002)",
    )
    ap.add_argument(
        "--no-debug",
        action="store_true",
        help="Do not request fetch_debug from skill (less output)",
    )
    args = ap.parse_args()

    list_name = (args.list_name or "").strip()
    if not list_name:
        print("list_name is required", file=sys.stderr)
        return 1

    worker_url = (args.worker_url or "").strip().rstrip("/")
    if not worker_url:
        try:
            worker_url = get_worker_url(args.registry_url)
        except SystemExit as e:
            print(e, file=sys.stderr)
            return 1
        print(f"Using worker: {worker_url}", file=sys.stderr)

    print(f"[warmup] List: {list_name}", file=sys.stderr, flush=True)

    round_num = 0
    total_saved = 0
    debug_skill = not getattr(args, "no_debug", False)
    if debug_skill:
        print("[warmup] Requesting fetch_debug from skill (use --no-debug to disable)", file=sys.stderr, flush=True)

    while True:
        round_num += 1
        print(f"[warmup] Round {round_num}: calling {SKILL_NAME} (timeout={SKILL_TIMEOUT}s)...", file=sys.stderr, flush=True)
        t0 = time.perf_counter()
        try:
            data = run_fetch_and_save(worker_url, list_name, debug=debug_skill)
        except Exception as e:
            print(f"[warmup] Error: {e}", file=sys.stderr)
            return 1
        elapsed = time.perf_counter() - t0
        print(f"[warmup] Round {round_num} completed in {elapsed:.2f}s", file=sys.stderr, flush=True)

        new_count = data.get("new_items_count") or 0
        persisted = data.get("persisted_count") or 0
        total_saved += persisted
        if data.get("persist_errors"):
            for err in data["persist_errors"]:
                print(f"[warmup]   Persist warning: {err}", file=sys.stderr)

        if data.get("fetch_debug") and debug_skill:
            print("[warmup] --- skill fetch_debug ---", file=sys.stderr, flush=True)
            for line in data["fetch_debug"]:
                print(f"[warmup]   {line}", file=sys.stderr, flush=True)
            print("[warmup] --- end fetch_debug ---", file=sys.stderr, flush=True)

        if new_count == 0:
            print(f"[warmup] No new items. Done.", file=sys.stderr, flush=True)
            break
        print(f"[warmup] Round {round_num} summary: new_items={new_count}, persisted={persisted}, total_saved_so_far={total_saved}", file=sys.stderr, flush=True)

    print(f"[warmup] Complete. Total persisted: {total_saved}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
