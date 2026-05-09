"""
RSS new-item notifier: one summary email per run for new items.

Calls rss_new_and_save_skill to get new items and update storage, then (unless --dry-run)
sends one email via notification_skill. Storage update is done by the skill. Requires:
registry, worker with rss_new_and_save_skill and notification_skill. Env: EMAIL_RECEIVER_DEFAULT.

Usage: python rss_notify_new.py <list_name> [--dry-run]
  List name = file in data/lists (e.g. ai-news -> data/lists/ai-news.json).
"""

from __future__ import annotations

import argparse
import json
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
SKILL_TIMEOUT = 120.0
NOTIFY_TIMEOUT = 20.0


def _format_date(s: str) -> str:
    if not s or len(s) < 10 or s[4] != "-" or s[7] != "-":
        return s.strip() if s else ""
    return s[:10].strip()


def _build_email(entries: list[dict[str, str]]) -> tuple[str, str, str]:
    n = len(entries)
    subject = f"RSS: {n} new item{'s' if n != 1 else ''}"
    plain_lines = []
    html_parts = []
    for e in entries:
        title = (e.get("title") or "(No title)").strip()
        link = (e.get("link") or "").strip()
        feed = (e.get("feed_title") or "").strip()
        pub = _format_date(e.get("published") or "")
        if feed and pub:
            plain_lines.append(f"[{feed}] {title} ({pub})")
        elif feed:
            plain_lines.append(f"[{feed}] {title}")
        elif pub:
            plain_lines.append(f"{title} ({pub})")
        else:
            plain_lines.append(title)
        if link:
            safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            safe_link = link.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            anchor = f'<a href="{safe_link}">{safe_title}</a>'
        else:
            anchor = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        suffix = []
        if feed:
            suffix.append(feed)
        if pub:
            suffix.append(pub)
        line = anchor + (" — " + " ".join(suffix) if suffix else "")
        html_parts.append(f"<p>{line}</p>")
    plain_body = "\n".join(plain_lines).strip()
    html_body = "\n".join(html_parts)
    return subject, plain_body, html_body


def _send_email(worker_url: str, to: str, subject: str, body: str, html_body: str | None = None) -> tuple[bool, int, str]:
    """Send email via notification_skill. Returns (success, status_code, response_text)."""
    payload: dict = {"to": [to], "subject": subject[:500], "body": body}
    if html_body:
        payload["html_body"] = html_body
    with httpx.Client(timeout=NOTIFY_TIMEOUT) as client:
        r = client.post(f"{worker_url}/skills/notification_skill/send", json=payload)
        return (r.is_success, r.status_code, r.text or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="RSS new-item notifier (one email per run)")
    parser.add_argument("list_name", help="Feed list name in data/lists (e.g. ai-news -> data/lists/ai-news.json)")
    parser.add_argument("--dry-run", action="store_true", help="No email, no storage writes")
    args = parser.parse_args()

    try:
        worker_url = _find_worker(REGISTRY_URL).rstrip("/")
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    payload = {"list_name": args.list_name.strip(), "dry_run": args.dry_run, "worker_url": worker_url}
    with httpx.Client(timeout=SKILL_TIMEOUT) as client:
        r = client.post(f"{worker_url}/skills/rss_new_and_save_skill/run", json=payload)
        if not r.is_success:
            print(f"Error: {r.status_code} {r.text}", file=sys.stderr)
            return 1

    data = r.json()
    print(json.dumps(data, indent=2))

    if not data.get("ok", False):
        return 1

    new_items = data.get("new_items") or []
    if args.dry_run or not new_items:
        return 0

    for err in data.get("persist_errors") or []:
        print(f"Warning: {err}", file=sys.stderr)

    n = len(new_items)
    print(f"Sending email for {n} new item(s)...", file=sys.stderr)

    to_email = (os.environ.get("EMAIL_RECEIVER_DEFAULT") or "").strip()
    if not to_email:
        print("Error: EMAIL_RECEIVER_DEFAULT not set", file=sys.stderr)
        return 1

    subject, plain_body, html_body = _build_email(new_items)
    ok, status, err_text = _send_email(worker_url, to_email, subject, plain_body, html_body)
    if not ok:
        print(f"Error: Failed to send email: {status} {err_text}", file=sys.stderr)
        return 1
    print("Email sent.", file=sys.stderr)
    persisted = data.get("persisted_count", 0)
    if persisted:
        print(f"Storage already updated by skill ({persisted} item IDs).", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
