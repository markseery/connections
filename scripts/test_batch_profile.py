#!/usr/bin/env python3
"""Test the 'batch' profile (mlx provider) via the AI server."""

from __future__ import annotations

import argparse
import os
import sys

import httpx

DEFAULT_REGISTRY = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")


def main() -> int:
    ap = argparse.ArgumentParser(description="Test the batch profile via the AI server")
    ap.add_argument("--prompt", default="What is the capital of France? Reply in one sentence.")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    args = ap.parse_args()

    print(f"Discovering aiserver from {args.registry} …")
    try:
        r = httpx.get(f"{args.registry}/servers/aiserver", timeout=5.0)
        r.raise_for_status()
        aiserver = (r.json() or {}).get("url", "").rstrip("/")
        if not aiserver:
            print("ERROR: aiserver not found in registry", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"  aiserver: {aiserver}")

    payload = {"prompt": args.prompt, "profile": "batch"}
    print(f"\nPOST {aiserver}/generate")
    print(f"  profile: batch")
    print(f"  prompt:  {args.prompt}")
    print()

    try:
        r = httpx.post(f"{aiserver}/generate", json=payload, timeout=300.0)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"ERROR {e.response.status_code}: {e.response.text[:500]}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    data = r.json()
    print(f"Provider: {data.get('provider')}")
    print(f"Model:    {data.get('model')}")
    print(f"Profile:  {data.get('profile')}")
    print()

    output = data.get("output", {})
    if isinstance(output, dict):
        print(output.get("text", ""))
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
