"""
License: MIT
Description: CLI helper that sends a prompt to the local AI server (/generate).

Discovers the AI server URL/port from the registry server unless --url or --inprocess
is used. Set REGISTRY_SERVER_URL in .env or environment to point at the registry.

Usage:
  python ask_ai.py "hello"
  python ask_ai.py "hello" --profile fast --provider ollama
  python ask_ai.py "hello" --url http://127.0.0.1:7012
  python ask_ai.py "hello" --inprocess
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx


def _get_ai_server_url_from_registry(registry_url: str) -> str:
    """Query registry for aiserver; return its base URL."""
    base = registry_url.rstrip("/")
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{base}/servers/aiserver")
        r.raise_for_status()
        data = r.json()
        url = data.get("url")
        if not url:
            raise ValueError("Registry response missing 'url' for aiserver")
        return url.rstrip("/")


def _call_over_http(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    base = url.rstrip("/")
    with httpx.Client(timeout=120.0) as client:
        r = client.post(f"{base}/generate", json=payload)
        r.raise_for_status()
        return r.json()


def _call_inprocess(payload: dict[str, Any]) -> dict[str, Any]:
    from fastapi.testclient import TestClient
    from servers.aiserver.main import app

    with TestClient(app) as client:
        r = client.post("/generate", json=payload)
        r.raise_for_status()
        return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description="Send a prompt to the AI server.")
    ap.add_argument("prompt", help="Prompt text")
    ap.add_argument(
        "--profile",
        default="fast",
        help="One of: fast, chat, reason, agent, code, image, video (default: fast)",
    )
    ap.add_argument(
        "--provider",
        default=None,
        help="Optional provider: ollama, openai, xai, google (default: server default)",
    )
    ap.add_argument(
        "--url",
        default=None,
        help="AI server base URL (overrides registry lookup).",
    )
    ap.add_argument(
        "--registry-url",
        default=os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002"),
        help="Registry server URL for discovering aiserver (default: REGISTRY_SERVER_URL or 127.0.0.1:7002).",
    )
    ap.add_argument(
        "--inprocess",
        action="store_true",
        help="Call the AI server in-process (no HTTP server required).",
    )
    args = ap.parse_args()

    payload: dict[str, Any] = {"prompt": args.prompt, "profile": args.profile}
    if args.provider:
        payload["provider"] = args.provider

    try:
        if args.inprocess:
            out = _call_inprocess(payload)
        else:
            ai_url = args.url
            if not ai_url:
                ai_url = _get_ai_server_url_from_registry(args.registry_url)
            out = _call_over_http(ai_url, payload)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if not args.inprocess:
            print("Tip: if the server isn't running, try: python ask_ai.py \"...\" --inprocess", file=sys.stderr)
        return 1

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

