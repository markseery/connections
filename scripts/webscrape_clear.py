#!/usr/bin/env python3
"""
Delete stored webscrape page(s) via webscraper_skill.

With --url '*', lists all URLs for the sitename and deletes each record.
Otherwise deletes the single given URL (query string is stripped server-side).

Usage:
  python scripts/webscrape_clear.py --namespace webscrape --sitename https://example.com --url '*'
  python scripts/webscrape_clear.py --namespace webscrape --sitename https://example.com \\
    --url 'https://example.com/about'

Requires: registry, worker (webscraper_skill).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

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
ALL_URLS_SENTINEL = "*"


def _strip_url_query(url: str) -> str:
    """Same rule as webscraper_skill: match legacy keys that still include ?…"""
    u = (url or "").strip()
    q = u.find("?")
    if q >= 0:
        u = u[:q]
    return u


def _urls_from_pages_response(payload: dict) -> list[str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw = data.get("urls")
    if not isinstance(raw, list):
        return []
    return sorted({str(u).strip() for u in raw if str(u).strip()})


def ensure_skill_loaded(client: httpx.Client, worker_url: str) -> None:
    r = client.post(f"{worker_url}/worker/skills/{SKILL}/load")
    if not r.is_success:
        raise RuntimeError(f"Failed to load {SKILL}: {r.status_code} {r.text}")


def fetch_stored_urls(client: httpx.Client, worker_url: str, namespace: str, sitename: str) -> list[str]:
    q = urlencode({"sitename": sitename, "namespace": namespace})
    r = client.get(f"{worker_url}/skills/{SKILL}/pages/urls?{q}")
    r.raise_for_status()
    return _urls_from_pages_response(r.json())


def delete_one(
    client: httpx.Client,
    worker_url: str,
    *,
    namespace: str,
    sitename: str,
    page_url: str,
) -> None:
    q = urlencode(
        {
            "namespace": namespace,
            "sitename": sitename,
            "url": page_url,
        }
    )
    r = client.delete(f"{worker_url}/skills/{SKILL}/pages?{q}")
    r.raise_for_status()


def main() -> int:
    p = argparse.ArgumentParser(description="Delete stored webscrape page(s) for a sitename.")
    p.add_argument("--namespace", default=DEFAULT_NAMESPACE, help="Storage namespace (default: webscrape)")
    p.add_argument(
        "--sitename",
        required=True,
        help="Sitename / base URL used when scraping (e.g. https://example.com)",
    )
    p.add_argument(
        "--url",
        required=True,
        help=f"Page URL to remove, or {ALL_URLS_SENTINEL!r} to remove all pages for this sitename",
    )
    p.add_argument("--registry-url", default=REGISTRY_URL, help="Registry base URL")
    p.add_argument("--worker-url", default="", help="Worker base URL (default: discover via registry)")
    args = p.parse_args()

    sitename = args.sitename.strip()
    namespace = (args.namespace or "").strip() or DEFAULT_NAMESPACE
    url_arg = (args.url or "").strip()

    if not sitename.startswith(("http://", "https://")):
        print("Error: --sitename must be an http(s) URL", file=sys.stderr)
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
            ensure_skill_loaded(client, worker_url)

            if url_arg == ALL_URLS_SENTINEL:
                urls = fetch_stored_urls(client, worker_url, namespace, sitename)
                if not urls:
                    print(
                        f"No stored URLs for namespace={namespace!r} sitename={sitename!r}.",
                        flush=True,
                    )
                    return 0
                # One DELETE per normalized path removes all legacy keys (e.g. ?utm… and ?tags=…).
                norms = sorted({_strip_url_query(u) for u in urls})
                ent = "entry" if len(urls) == 1 else "entries"
                print(
                    f"Deleting {len(norms)} unique path(s) ({len(urls)} listing {ent})...",
                    flush=True,
                )
                failed = 0
                for u in norms:
                    try:
                        delete_one(
                            client,
                            worker_url,
                            namespace=namespace,
                            sitename=sitename,
                            page_url=u,
                        )
                        print(f"  deleted {u}", flush=True)
                    except httpx.HTTPStatusError as exc:
                        failed += 1
                        print(
                            f"  failed {u}: HTTP {exc.response.status_code} {exc.response.text[:200]}",
                            file=sys.stderr,
                            flush=True,
                        )
                if failed:
                    print(f"Done with {failed} failure(s).", file=sys.stderr, flush=True)
                    return 1
                print("Done.", flush=True)
                return 0

            delete_one(
                client,
                worker_url,
                namespace=namespace,
                sitename=sitename,
                page_url=url_arg,
            )
            print(f"Deleted {url_arg!r}", flush=True)
            return 0

    except httpx.HTTPStatusError as exc:
        print(
            f"Error: HTTP {exc.response.status_code}: {exc.response.text[:500]}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
