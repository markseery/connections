"""
RSS new-item notifier: one summary email per run for new items.

- Stores one record per RSS item in storage (namespace rss_notified). Key = item id (normalized link).
- Item identity = normalized link only (scheme + netloc + path; no query/fragment).
- One email at end with all new items (title, link, date); recipient from EMAIL_RECEIVER_DEFAULT.
- Only emails items published within MAX_AGE_DAYS; older new items are still marked notified.
- Persists each new item as its own record, even if the email send fails.

Requires: registry, storage, worker (rss_skill + notification_skill). .env: EMAIL_RECEIVER_DEFAULT, SMTP settings.

Usage: python rss_notify_new.py <list_name> [--dry-run]
  List name = file in data/lists (e.g. ai-feeds -> data/lists/ai-feeds.json).
  JSON: array of feed URLs, or object with "feeds" / "urls" / "url_list".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import httpx

from common.skill_lifecycle import find_live_worker

_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.is_file():
    from dotenv import load_dotenv
    load_dotenv(_env_path)

# --- config ---
REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
STORAGE_NAMESPACE = "rss_notified"
FEED_TIMEOUT = 45.0
NOTIFY_TIMEOUT = 20.0
MAX_AGE_DAYS = 30
LISTS_DIR = Path(__file__).resolve().parent / "data" / "lists"


def _load_feed_list(list_name: str) -> list[str]:
    """Load feed URLs from data/lists/{list_name}.json. Accepts name with or without .json."""
    name = (list_name or "").strip()
    if not name:
        raise ValueError("list name is required")
    if not name.endswith(".json"):
        name = f"{name}.json"
    path = LISTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Feed list not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [str(u).strip() for u in raw if str(u).strip()]
    if isinstance(raw, dict):
        for key in ("feeds", "urls", "url_list"):
            if key in raw and isinstance(raw[key], list):
                return [str(u).strip() for u in raw[key] if str(u).strip()]
    raise ValueError(f"Invalid list format in {path}: expected JSON array or object with 'feeds'/'urls'/'url_list'")


# --- discovery ---
def _worker_url() -> str:
    u = find_live_worker(REGISTRY_URL)
    if not u:
        raise RuntimeError("No live worker in registry")
    return u.rstrip("/")


def _storage_url() -> str:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{REGISTRY_URL}/servers/storage")
        r.raise_for_status()
        u = (r.json() or {}).get("url")
        if not u:
            raise ValueError("Registry has no storage url")
        return str(u).rstrip("/")


def _receiver_email() -> str:
    e = (os.environ.get("EMAIL_RECEIVER_DEFAULT") or "").strip()
    if not e:
        raise ValueError("EMAIL_RECEIVER_DEFAULT not set in .env")
    return e


# --- canonical item id (single source of truth) ---
def _item_id_from_link(link: str) -> str:
    """Canonical item id: scheme + netloc + path only. No query, no fragment."""
    if not link or not isinstance(link, str):
        return ""
    s = link.strip()
    if not (s.startswith("http://") or s.startswith("https://")):
        return s
    try:
        p = urlparse(s)
        path = (p.path or "/").rstrip("/") or "/"
        return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", "", ""))
    except Exception:
        return s


def _item_id(item: dict[str, Any]) -> str:
    """Stable id for an rss_skill item. Prefer normalized link."""
    link = (item.get("link") or "").strip()
    if link:
        c = _item_id_from_link(link)
        if c:
            return c
    return (item.get("id") or item.get("title") or "").strip() or str(id(item))


# --- dates ---
def _parse_published(s: str) -> datetime | None:
    if not (s or "").strip():
        return None
    s = s.strip()
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
    dt = _parse_published(published_str)
    if dt is None:
        return True
    return dt >= (datetime.now(timezone.utc) - timedelta(days=days))


# --- storage: one record per item (key = item id) ---
def _list_notified_item_ids(storage_base: str) -> set[str]:
    """List all keys in rss_notified; each key is an item id (normalized link) we already notified."""
    url = f"{storage_base}/namespaces/{STORAGE_NAMESPACE}/records"
    with httpx.Client(timeout=15.0) as client:
        r = client.get(url)
        r.raise_for_status()
    data = r.json()
    keys = data.get("keys") if isinstance(data, dict) else None
    return set(keys) if isinstance(keys, list) else set()


def _put_notified_item(storage_base: str, item_id: str) -> None:
    """Store one record for this item (key = item_id)."""
    key = quote(item_id, safe="")
    url = f"{storage_base}/namespaces/{STORAGE_NAMESPACE}/records/{key}"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    body = {"link": item_id, "notified_at": now}
    with httpx.Client(timeout=10.0) as client:
        r = client.put(url, json=body)
        r.raise_for_status()


# --- worker: fetch feed ---
def _fetch_feed(worker_url: str, feed_url: str) -> dict[str, Any] | None:
    with httpx.Client(timeout=FEED_TIMEOUT) as client:
        r = client.post(f"{worker_url}/skills/rss_skill/feed", json={"url": feed_url})
        if not r.is_success:
            return None
        return r.json()


# --- process one feed: diff against seen, update seen ---
def _process_feed(
    worker_url: str,
    feed_url: str,
    seen: set[str],
) -> tuple[list[dict[str, str]], list[str], bool]:
    """
    Fetch feed; items not in `seen` are new. Append new item ids to seen. Return (entries_for_email, ids_to_add, fetch_ok).
    """
    data = _fetch_feed(worker_url, feed_url)
    if not data:
        return [], [], False
    items = data.get("items") or []
    feed_title = (data.get("feed") or {}).get("title") or feed_url
    if not items:
        return [], [], True

    entries = []
    ids_to_add = []
    for item in items:
        iid = _item_id(item)
        if iid in seen:
            continue
        seen.add(iid)
        ids_to_add.append(iid)
        if not _published_within_days((item.get("published") or item.get("updated") or "").strip(), MAX_AGE_DAYS):
            continue
        entries.append({
            "feed_title": feed_title,
            "title": (item.get("title") or "(No title)").strip(),
            "link": (item.get("link") or "").strip(),
            "published": (item.get("published") or item.get("updated") or "").strip(),
        })

    return entries, ids_to_add, True


# --- email ---
def _format_date(s: str) -> str:
    if not s or len(s) < 10 or s[4] != "-" or s[7] != "-":
        return s.strip() if s else ""
    return s[:10].strip()


def _build_email(entries: list[dict[str, str]]) -> tuple[str, str, str]:
    """Return (subject, plain_body, html_body). Title is hyperlinked in HTML; URL not repeated."""
    n = len(entries)
    subject = f"RSS: {n} new item{'s' if n != 1 else ''}"
    plain_lines = []
    html_parts = []
    for e in entries:
        title = (e.get("title") or "(No title)").strip()
        link = (e.get("link") or "").strip()
        feed = (e.get("feed_title") or "").strip()
        pub = _format_date(e.get("published") or "")
        # Plain: one line per item, no separate URL line
        if feed and pub:
            plain_lines.append(f"[{feed}] {title} ({pub})")
        elif feed:
            plain_lines.append(f"[{feed}] {title}")
        elif pub:
            plain_lines.append(f"{title} ({pub})")
        else:
            plain_lines.append(title)
        # HTML: title as hyperlink; optional feed and date after
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


def _send_email(worker_url: str, to: str, subject: str, body: str, html_body: str | None = None) -> bool:
    payload: dict = {"to": [to], "subject": subject[:500], "body": body}
    if html_body:
        payload["html_body"] = html_body
    with httpx.Client(timeout=NOTIFY_TIMEOUT) as client:
        r = client.post(
            f"{worker_url}/skills/notification_skill/send",
            json=payload,
        )
        return r.is_success


# --- persist: one PUT per new item ---
def _persist_new_items(storage_base: str, item_ids: list[str]) -> None:
    for iid in item_ids:
        _put_notified_item(storage_base, iid)


def main() -> int:
    parser = argparse.ArgumentParser(description="RSS new-item notifier (one email per run)")
    parser.add_argument("list_name", help="Feed list name in data/lists (e.g. ai-feeds -> data/lists/ai-feeds.json)")
    parser.add_argument("--dry-run", action="store_true", help="No email, no storage writes")
    args = parser.parse_args()

    try:
        to_email = _receiver_email()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    try:
        feeds = _load_feed_list(args.list_name)
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    try:
        worker_url = _worker_url()
        storage_base = _storage_url()
    except (RuntimeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    with httpx.Client(timeout=10.0) as client:
        for name in ("rss_skill", "notification_skill"):
            client.post(f"{worker_url}/worker/skills/{name}/load").raise_for_status()

    seen = _list_notified_item_ids(storage_base)
    print(f"RSS notify: {len(feeds)} feed(s), {len(seen)} already notified → {to_email}" + (" (dry-run)" if args.dry_run else ""), flush=True)

    all_entries: list[dict[str, str]] = []
    ids_to_persist: list[str] = []
    errors = 0

    for i, feed_url in enumerate(feeds, 1):
        print(f"  [{i}/{len(feeds)}] {feed_url}", flush=True)
        print("       fetching...", flush=True)
        try:
            entries, new_ids, ok = _process_feed(worker_url, feed_url, seen)
            if not ok:
                print("       -> fetch failed", flush=True)
                errors += 1
                continue
            if new_ids:
                if entries:
                    print(f"       {len(new_ids)} new, {len(entries)} in email", flush=True)
                else:
                    print(f"       {len(new_ids)} new (all older than 30d)", flush=True)
            else:
                print("       0 new", flush=True)
            all_entries.extend(entries)
            ids_to_persist.extend(new_ids)
        except Exception as e:
            print(f"       -> {e}", flush=True)
            errors += 1

    total = len(all_entries)
    if ids_to_persist and not args.dry_run:
        if all_entries:
            subject, body, html_body = _build_email(all_entries)
            print(f"Sending 1 email ({total} item(s))...", flush=True)
            sent = _send_email(worker_url, to_email, subject, body, html_body)
            _persist_new_items(storage_base, ids_to_persist)
            print("Email sent, storage updated." if sent else "Email send failed; storage updated (no retry of same links).", flush=True)
            if not sent:
                errors += 1
        else:
            _persist_new_items(storage_base, ids_to_persist)
            print("Storage updated (no email; new items older than 30 days).", flush=True)
    elif ids_to_persist and args.dry_run:
        print(f"[dry-run] would send 1 email ({total} item(s)) and store {len(ids_to_persist)} item(s)" if all_entries else f"[dry-run] would store {len(ids_to_persist)} item(s) only", flush=True)

    print(f"Done: {total} new, {errors} errors", flush=True)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
