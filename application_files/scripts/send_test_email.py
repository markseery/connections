#!/usr/bin/env python3
"""
Send a test email via the notification skill (worker).

Requires: registry, worker with notification_skill. .env: EMAIL_SENDER, EMAIL_PASSWORD, SMTP_*.

Usage:
  python send_test_email.py you@example.com
  python send_test_email.py you@example.com --registry-url http://127.0.0.1:7002
  python send_test_email.py you@example.com --worker-url http://127.0.0.1:7030
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

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

_env = Path(__file__).resolve().parents[1] / ".env"
if _env.is_file():
    from dotenv import load_dotenv
    load_dotenv(_env)

REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
TIMEOUT = 30.0


def get_worker_url(registry_url: str, worker_url: str | None) -> str:
    if worker_url:
        return worker_url.rstrip("/")
    return _find_worker(registry_url).rstrip("/")


def send_test_email(worker_url: str, to_email: str, include_html: bool = True) -> dict:
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(
            f"{worker_url}/skills/notification_skill/send/test",
            json={"to": to_email, "include_html": include_html},
        )
        r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description="Send a test email via the notification skill.")
    ap.add_argument("email", help="Recipient email address")
    ap.add_argument(
        "--registry-url",
        default=REGISTRY_URL,
        help="Registry server URL (default: REGISTRY_SERVER_URL or 127.0.0.1:7002)",
    )
    ap.add_argument(
        "--worker-url",
        default=None,
        help="Worker base URL (overrides registry lookup)",
    )
    ap.add_argument(
        "--no-html",
        action="store_true",
        help="Send plain text only",
    )
    args = ap.parse_args()
    email = (args.email or "").strip()
    if not email:
        print("Error: email is required", file=sys.stderr)
        return 1

    try:
        worker_url = get_worker_url(args.registry_url, args.worker_url)
        result = send_test_email(worker_url, email, include_html=not args.no_html)
        # Common response shape: summary is canonical one-liner
        msg = result.get("summary") or result.get("status") or result
        print("Sent:", msg if isinstance(msg, str) else result.get("status", result))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
