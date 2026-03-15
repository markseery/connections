"""
License: MIT
Description: Option A — track notified RSS items in storage (namespace rss_notified),
one record per feed; email new items to EMAIL_RECEIVER_DEFAULT via notification_skill.
Items with publication date older than MAX_AGE_DAYS (30) are not emailed but are still
marked as seen so they are not re-counted on the next run.

Requires: registry, storage, worker (rss_skill + notification_skill). .env with
EMAIL_RECEIVER_DEFAULT and notification_skill SMTP settings.
Storage must be a single persistent instance (same URL every run) or tracking will fail.

Usage:
  python rss_notify_new.py
  python rss_notify_new.py --dry-run
  python rss_notify_new.py https://example.com/feed.xml
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from common.skill_lifecycle import find_live_worker

# Load .env from project root so EMAIL_RECEIVER_DEFAULT is available
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.is_file():
    from dotenv import load_dotenv
    load_dotenv(_env_path)


REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
STORAGE_NAMESPACE = "rss_notified"
FEED_TIMEOUT = 45.0
NOTIFY_TIMEOUT = 20.0
MAX_NOTIFIED_IDS = 500  # cap per-feed history
MAX_AGE_DAYS = 30  # ignore items with publication date older than this (no email, still mark seen)

DEFAULT_FEEDS = [
    "https://research.google/blog/rss/",
    "https://deepmind.google/blog/rss.xml",
    "https://openai.com/news/rss.xml",
    "https://bair.berkeley.edu/blog/feed.xml",
    "https://news.mit.edu/rss/feed",
    "https://news.mit.edu/rss/research",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "https://www.kdnuggets.com/feed",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.ft.com/artificial-intelligence?format=rss",
    "https://arstechnica.com/ai/feed/",
    "https://news.microsoft.com/source/topics/ai/feed/",
    "https://aws.amazon.com/blogs/aws/category/artificial-intelligence/feed/",
    "https://www.infoworld.com/artificial-intelligence/feed/",
    "https://www.computerworld.com/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://news.crunchbase.com/sections/ai/feed/",
]


def _worker_url() -> str:
    url = find_live_worker(REGISTRY_URL)
    if not url:
        raise RuntimeError("No live worker found in registry")
    return url.rstrip("/")


def _storage_url() -> str:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{REGISTRY_URL}/servers/storage")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError("Registry missing url for storage")
        return str(url).rstrip("/")


def _receiver_email() -> str:
    email = (os.environ.get("EMAIL_RECEIVER_DEFAULT") or "").strip()
    if not email:
        raise ValueError("EMAIL_RECEIVER_DEFAULT is not set in .env")
    return email


def _ensure_skills_loaded(worker_url: str) -> None:
    with httpx.Client(timeout=10.0) as client:
        for name in ("rss_skill", "notification_skill"):
            r = client.post(f"{worker_url}/worker/skills/{name}/load")
            r.raise_for_status()


def _fetch_feed(worker_url: str, feed_url: str) -> dict[str, Any] | None:
    with httpx.Client(timeout=FEED_TIMEOUT) as client:
        r = client.post(
            f"{worker_url}/skills/rss_skill/feed",
            json={"url": feed_url},
        )
        if not r.is_success:
            return None
        return r.json()


def _storage_key(feed_url: str) -> str:
    """Normalize feed URL for storage key so GET/PUT always use the same record."""
    u = (feed_url or "").strip().rstrip("/")
    return u or feed_url or ""


def _get_notified_state(storage_base: str, feed_url: str) -> list[str]:
    key = quote(_storage_key(feed_url), safe="")
    url = f"{storage_base}/namespaces/{STORAGE_NAMESPACE}/records/{key}"
    with httpx.Client(timeout=10.0) as client:
        r = client.get(url)
        if r.status_code == 404:
            return []
        r.raise_for_status()
    data = r.json()
    value = data.get("value") if isinstance(data, dict) else None
    if not isinstance(value, dict):
        return []
    ids = value.get("notified_ids")
    return list(ids) if isinstance(ids, list) else []


def _put_notified_state(
    storage_base: str,
    feed_url: str,
    notified_ids: list[str],
) -> None:
    key = quote(_storage_key(feed_url), safe="")
    url = f"{storage_base}/namespaces/{STORAGE_NAMESPACE}/records/{key}"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    body = {
        "feed_url": _storage_key(feed_url),
        "updated_at": now,
        "notified_ids": notified_ids[-MAX_NOTIFIED_IDS:],
    }
    with httpx.Client(timeout=10.0) as client:
        r = client.put(url, json=body)
        r.raise_for_status()


def _send_notification(worker_url: str, to_email: str, subject: str, body: str) -> bool:
    with httpx.Client(timeout=NOTIFY_TIMEOUT) as client:
        r = client.post(
            f"{worker_url}/skills/notification_skill/send",
            json={
                "to": [to_email],
                "subject": subject[:500],
                "body": body,
            },
        )
        return r.is_success


def _item_id(item: dict[str, Any]) -> str:
    return (item.get("id") or item.get("link") or item.get("title") or "").strip() or str(id(item))


def _parse_published(published_str: str) -> datetime | None:
    """Parse publication date string to timezone-aware datetime (UTC). Returns None if unparseable."""
    if not (published_str or "").strip():
        return None
    s = published_str.strip()
    # ISO-style (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
    if s:
        try:
            s_iso = s[:-1] + "+00:00" if s.endswith("Z") else s
            return datetime.fromisoformat(s_iso).astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc)
    except (ValueError, TypeError):
        pass
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _published_within_days(published_str: str, days: int) -> bool:
    """True if no date (include in notification) or published within the last `days` days."""
    dt = _parse_published(published_str)
    if dt is None:
        return True  # no date = include
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return dt >= cutoff


def process_feed(
    worker_url: str,
    storage_base: str,
    feed_url: str,
) -> tuple[list[dict[str, str]], list[str], bool]:
    """Process one feed: fetch, diff. Do not send email or update storage.
    Returns (entries for email: [{feed_title, title, link, published}], item_ids to mark notified, fetch_ok).
    """
    data = _fetch_feed(worker_url, feed_url)
    if not data:
        return [], [], False
    feed_meta = data.get("feed") or {}
    feed_title = feed_meta.get("title") or feed_url
    items = data.get("items") or []
    if not items:
        print("       0 items", flush=True)
        return [], [], True

    notified_ids = _get_notified_state(storage_base, feed_url)
    seen = set(notified_ids)
    new_items = [i for i in items if _item_id(i) not in seen]
    n_new = len(new_items)
    print(f"       {len(items)} items, {n_new} new", flush=True)

    entries: list[dict[str, str]] = []
    ids_to_add: list[str] = []
    for item in new_items:
        item_id = _item_id(item)
        title = (item.get("title") or "(No title)").strip()
        link = (item.get("link") or "").strip()
        published = (item.get("published") or item.get("updated") or "").strip()
        # Always mark as seen (ids_to_add) so we don't re-count next run; only email if within MAX_AGE_DAYS
        ids_to_add.append(item_id)
        if not _published_within_days(published, MAX_AGE_DAYS):
            continue  # skip adding to entries (no email for old items)
        entries.append({"feed_title": feed_title, "title": title, "link": link, "published": published})

    return entries, ids_to_add, True


def _format_published(published: str) -> str:
    """Short display for publication date (e.g. ISO date only)."""
    if not published:
        return ""
    s = published.strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]  # YYYY-MM-DD
    return s


def _build_summary_email(entries: list[dict[str, str]]) -> tuple[str, str]:
    """Build subject and plain body for one email listing all new items."""
    n = len(entries)
    subject = f"RSS: {n} new item{'s' if n != 1 else ''}"
    lines = []
    for e in entries:
        title = (e.get("title") or "(No title)").strip()
        link = (e.get("link") or "").strip()
        feed = (e.get("feed_title") or "").strip()
        pub = _format_published(e.get("published") or "")
        if feed and pub:
            lines.append(f"[{feed}] {title} ({pub})")
        elif feed:
            lines.append(f"[{feed}] {title}")
        elif pub:
            lines.append(f"{title} ({pub})")
        else:
            lines.append(title)
        if link:
            lines.append(link)
        lines.append("")
    body = "\n".join(lines).strip()
    return subject, body


def main() -> int:
    parser = argparse.ArgumentParser(description="Notify new RSS items via email (Option A)")
    parser.add_argument("--dry-run", action="store_true", help="Do not send email or update storage")
    parser.add_argument(
        "feeds",
        nargs="*",
        default=None,
        help="Feed URLs (default: built-in list)",
    )
    args = parser.parse_args()

    try:
        to_email = _receiver_email()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    try:
        worker_url = _worker_url()
        storage_base = _storage_url()
    except (RuntimeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    _ensure_skills_loaded(worker_url)
    feeds = args.feeds if args.feeds else DEFAULT_FEEDS
    mode = " (dry-run)" if args.dry_run else ""
    print(f"RSS notify{mode}: {len(feeds)} feed(s) → {to_email}", flush=True)

    all_entries: list[dict[str, str]] = []
    storage_updates: list[tuple[str, list[str]]] = []  # (feed_url, ids_to_add)
    total_err = 0

    for i, feed_url in enumerate(feeds, 1):
        print(f"  [{i}/{len(feeds)}] {feed_url}", flush=True)
        print("       fetching...", flush=True)
        try:
            entries, ids_to_add, fetch_ok = process_feed(worker_url, storage_base, feed_url)
            if not fetch_ok:
                print("       -> fetch failed", flush=True)
                total_err += 1
            else:
                all_entries.extend(entries)
                if ids_to_add:
                    storage_updates.append((feed_url, ids_to_add))
                    print(f"       -> {len(ids_to_add)} new", flush=True)
                else:
                    print("       -> 0 new", flush=True)
        except Exception as e:
            print(f"       -> ERROR {e}", flush=True)
            total_err += 1

    total_new = len(all_entries)

    # Persist "seen" ids so next run we don't re-count. If we emailed, only update after send succeeds.
    def apply_storage_updates() -> None:
        for feed_url, ids_to_add in storage_updates:
            notified_ids = _get_notified_state(storage_base, feed_url)
            notified_ids.extend(ids_to_add)
            _put_notified_state(storage_base, feed_url, notified_ids)

    if storage_updates:
        if all_entries:
            if args.dry_run:
                print(f"[dry-run] would send 1 email with {total_new} new item(s)", flush=True)
            else:
                subject, body = _build_summary_email(all_entries)
                print(f"Sending 1 email with {total_new} new item(s)...", flush=True)
                if _send_notification(worker_url, to_email, subject, body):
                    apply_storage_updates()
                    print("Email sent, storage updated.", flush=True)
                else:
                    print("Email send failed; storage not updated (will retry next run).", flush=True)
                    total_err += 1
        else:
            # New items were all older than MAX_AGE_DAYS; still mark seen so we don't re-count
            if args.dry_run:
                print("[dry-run] would update storage (all new items older than 30 days).", flush=True)
            else:
                apply_storage_updates()
                print("Storage updated (no email; all new items older than 30 days).", flush=True)

    print(f"Done: {total_new} new, {total_err} errors", flush=True)
    return 0 if total_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
