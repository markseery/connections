#!/usr/bin/env python3
"""
Load stored webscrape page content and run a prompt against it via the AI server.

If --url is * (asterisk), fetches combined content for all pages under the sitename.
Otherwise fetches content for that single URL.

Usage:
  python scripts/webscrape_analysis.py \\
    --sitename https://example.com --namespace webscrape --url '*' \\
    --prompt "What are the main product themes?"
  python scripts/webscrape_analysis.py \\
    --sitename https://example.com --url 'https://example.com/about' \\
    --prompt "Summarize this page in three bullets."

Requires: registry, worker (webscraper_skill), aiserver.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

import httpx

_env = Path(__file__).resolve().parents[1] / ".env"
if _env.is_file():
    from dotenv import load_dotenv
    load_dotenv(_env)

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


SKILL = "webscraper_skill"
DEFAULT_NAMESPACE = "webscrape"
REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
ALL_URLS_SENTINEL = "*"
AI_TIMEOUT = 300.0


def get_aiserver_url(registry_url: str) -> str:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{registry_url.rstrip('/')}/servers/aiserver")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError("Registry missing url for aiserver")
        return str(url).rstrip("/")


def combined_text_from_skill_response(payload: dict) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(data.get("combined_text") or "").strip()


def fetch_content(
    client: httpx.Client,
    worker_url: str,
    *,
    sitename: str,
    namespace: str,
    page_url: str | None,
    max_chars: int | None,
) -> tuple[str, str]:
    """
    Return (combined_text, description for logging).
    page_url None means all pages; otherwise single URL.
    """
    params: dict[str, str | int] = {
        "sitename": sitename,
        "namespace": namespace,
    }
    if page_url is not None:
        params["url"] = page_url
    if max_chars is not None and max_chars > 0:
        params["max_chars"] = max_chars
    q = urlencode(params)
    r = client.get(f"{worker_url}/skills/{SKILL}/pages/content?{q}")
    r.raise_for_status()
    text = combined_text_from_skill_response(r.json())
    if page_url is None:
        label = f"all URLs under {sitename!r}"
    else:
        label = page_url
    return text, label


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


def main() -> int:
    p = argparse.ArgumentParser(
        description="Send stored webscrape content + prompt to the AI server and print the reply."
    )
    p.add_argument("--namespace", default=DEFAULT_NAMESPACE, help="Storage namespace (default: webscrape)")
    p.add_argument("--sitename", required=True, help="Sitename / base URL used when scraping (e.g. https://example.com)")
    p.add_argument(
        "--url",
        required=True,
        help=f"Page URL to analyze, or {ALL_URLS_SENTINEL!r} for combined content of all stored pages",
    )
    p.add_argument("--prompt", required=True, help="Instruction for the model (content is appended as context)")
    p.add_argument("--registry-url", default=REGISTRY_URL, help="Registry base URL")
    p.add_argument("--worker-url", default="", help="Worker base URL (default: discover via registry)")
    p.add_argument(
        "--profile",
        default="fast",
        help="AIServer profile (default: fast)",
    )
    p.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help="Optional cap on context size (passed to pages/content)",
    )
    args = p.parse_args()

    sitename = args.sitename.strip()
    namespace = (args.namespace or "").strip() or DEFAULT_NAMESPACE
    url_arg = (args.url or "").strip()

    if not sitename.startswith(("http://", "https://")):
        print("Error: --sitename must be an http(s) URL", file=sys.stderr)
        return 1

    worker_url = (args.worker_url or "").strip().rstrip("/")
    if not worker_url:
        try:
            worker_url = _find_worker(args.registry_url.rstrip("/"))
        except RuntimeError:
            print("Error: No live worker in registry", file=sys.stderr)
            return 1

    page_url: str | None
    if url_arg == ALL_URLS_SENTINEL:
        page_url = None
    else:
        page_url = url_arg

    try:
        aiserver_url = get_aiserver_url(args.registry_url)
        with httpx.Client(timeout=120.0) as client:
            context, label = fetch_content(
                client,
                worker_url,
                sitename=sitename,
                namespace=namespace,
                page_url=page_url,
                max_chars=args.max_chars,
            )
    except httpx.HTTPStatusError as exc:
        print(f"Error: HTTP {exc.response.status_code}: {exc.response.text[:500]}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not context:
        print(f"Error: No content returned for {label!r}", file=sys.stderr)
        return 1

    full_prompt = (
        f"{args.prompt.strip()}\n\n"
        "---\n\n"
        "Scraped page content (context):\n\n"
        f"{context}"
    )

    try:
        raw = call_ai(aiserver_url, full_prompt, args.profile)
    except Exception as exc:
        print(f"Error: AI request failed: {exc}", file=sys.stderr)
        return 1

    print(extract_text(raw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
