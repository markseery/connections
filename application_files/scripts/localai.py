#!/usr/bin/env python3
"""
Quick local AI prompt using the 'local' profile (Ollama gemma4:e4b).

Usage:
  python3 localai.py "What is kubernetes?"
  python3 localai.py "Explain TCP handshake" --registry http://127.0.0.1:7002
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

SYSTEM = "responses must be terse and succinct without any thinking"

DEFAULT_REGISTRY_URL = os.environ.get(
    "REGISTRY_SERVER_URL", "http://127.0.0.1:7002"
).rstrip("/")


def _get_aiserver_url(registry_url: str) -> str:
    r = httpx.get(f"{registry_url}/servers/aiserver", timeout=5.0)
    r.raise_for_status()
    url = (r.json() or {}).get("url")
    if not url:
        raise ValueError("Registry missing url for aiserver")
    return str(url).rstrip("/")


def main() -> int:
    ap = argparse.ArgumentParser(description="Local AI prompt (Ollama)")
    ap.add_argument("prompt", help="User prompt")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY_URL, help="Registry URL")
    ap.add_argument("--timeout", type=float, default=120.0, help="AI timeout in seconds")
    args = ap.parse_args()

    try:
        aiserver_url = _get_aiserver_url(args.registry)
    except Exception as e:
        print(f"AI server discovery failed: {e}", file=sys.stderr)
        return 1

    prompt = f"[SYSTEM]: {SYSTEM}\n\n[USER]: {args.prompt}"

    try:
        with httpx.Client(timeout=args.timeout) as client:
            r = client.post(
                f"{aiserver_url}/generate",
                json={"prompt": prompt, "profile": "local"},
            )
            r.raise_for_status()
    except Exception as e:
        print(f"AI request failed: {e}", file=sys.stderr)
        return 1

    data = r.json()
    output = data.get("output")
    if isinstance(output, dict) and "text" in output:
        text = str(output["text"]).strip()
    elif isinstance(output, str):
        text = output.strip()
    else:
        text = str(data.get("output", data)).strip()

    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
