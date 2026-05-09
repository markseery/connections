#!/usr/bin/env python3
"""
Test Google News RSS article URL decoding and content fetching via the worker.

Sends the URL to the rss_new_skill endpoint on the worker, which internally
decodes the Google News redirect and fetches article content.

Usage:
  python3 scripts/test_google_news_content.py
  python3 scripts/test_google_news_content.py --registry-url http://127.0.0.1:7002
  TEST_GOOGLE_NEWS_URL="https://news.google.com/..." python3 scripts/test_google_news_content.py
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import sys

import httpx

_WORKER_NAMES = ["worker-1", "worker-2", "worker"]

TEST_URL = (
    "https://news.google.com/rss/articles/CBMiygFBVV95cUxNemZPc2lvejFpUnBPOWUyOFEtRzlPOXZPNDcxaHpnZ3dwSUt5eEZuV0xLNlJVaWtnakpDLXVRWE8zNnNpXzYxTzI5TkZFUjN1NklLcWI5VEhRMURsQXZtS19obWhMeHRVcWYtOTJ4THVING5YLS1YY09xVXZJNVp3WWRjSXMtM0hqcmdLQUhtbmRZNjBjN1JlM1RINmhCM2FMUHREMjk4Wk05QWRfTkpVb0Y2ZGZXbHptMHE2cG1sTDNyU3NobktMNUVn?oc=5"
)


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


def _try_decode_google_news_url(url: str) -> str | None:
    """Attempt base64 decode of a Google News RSS article URL."""
    m = re.search(r"/articles/([A-Za-z0-9_-]+)", url)
    if not m:
        return None
    token = m.group(1)
    for prefix in ("", "CBMi"):
        candidate = token.removeprefix(prefix) if prefix else token
        padded = candidate + "=" * (-len(candidate) % 4)
        try:
            raw = base64.urlsafe_b64decode(padded)
            text = raw.decode("utf-8", errors="ignore")
            if text.startswith("http"):
                return text
            inner = re.search(r"(https?://[^\x00-\x1f]+)", text)
            if inner:
                return inner.group(1)
        except Exception:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Test Google News URL decode + content fetch")
    ap.add_argument("--registry-url", default=os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002"))
    ap.add_argument("--url", default=os.environ.get("TEST_GOOGLE_NEWS_URL", TEST_URL).strip())
    args = ap.parse_args()

    url = args.url
    print(f"Input URL: {url[:100]}...", flush=True)

    decoded = _try_decode_google_news_url(url)
    print(f"Decoded URL: {decoded or '(none)'}", flush=True)
    if not decoded:
        print("FAIL: could not decode Google News URL", flush=True)
        return 1

    try:
        worker = _find_worker(args.registry_url.rstrip("/"))
    except RuntimeError as e:
        print(f"FAIL: {e}", file=sys.stderr, flush=True)
        return 1

    print(f"Worker: {worker}", flush=True)
    print(f"Fetching content via rss_new_skill...", flush=True)

    try:
        r = httpx.post(
            f"{worker}/skills/rss_new_skill/fetch_content",
            json={"url": url},
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"FAIL: HTTP error — {e}", file=sys.stderr, flush=True)
        return 1

    content = data.get("data", {}).get("content", "") if isinstance(data.get("data"), dict) else str(data.get("data", ""))
    print(f"Content length: {len(content)}", flush=True)
    print(f"Preview (first 400 chars): {repr(content[:400])}", flush=True)

    if len(content) < 100:
        print("FAIL: content too short (expected article text)", flush=True)
        return 1
    if "Google News" in content and len(content) < 300:
        print("FAIL: still got stub 'Google News' page", flush=True)
        return 1

    print("OK: decoded URL and got article content", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
