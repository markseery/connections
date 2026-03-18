"""
License: MIT
Description: RSS skill — fetches an RSS or Atom feed URL and returns a normalized
JSON structure regardless of feed format or protocol (HTTP/HTTPS). Uses feedparser
for robust parsing of RSS 2.0, Atom, and common variants.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import feedparser
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

router = APIRouter()

FEED_TIMEOUT = 30.0
USER_AGENT = "ConnectionsRSSSkill/1.0"
# Request feed formats so servers (e.g. Google Alerts) return XML instead of HTML
FEED_ACCEPT = "application/atom+xml, application/rss+xml, application/xml, text/xml, */*"


def _normalize_google_news_feed_url(url: str) -> str:
    """
    Rewrite Google News web URLs to RSS feed URLs so feedparser gets XML, not HTML.
    - /publications/<id> -> /rss/publications/<id>
    - /topics/<id> -> /rss/topics/<id>
    Leaves /rss/... and other hosts unchanged.
    """
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return u
    try:
        parsed = urlparse(u)
        netloc = (parsed.netloc or "").lower()
        if "news.google.com" not in netloc:
            return u
        path = (parsed.path or "").strip("/")
        if path.startswith("rss/"):
            return u
        if path.startswith("publications/"):
            new_path = "/rss/" + path
            return urlunparse((parsed.scheme, parsed.netloc, new_path, parsed.params, parsed.query, parsed.fragment))
        if path.startswith("topics/"):
            new_path = "/rss/" + path
            return urlunparse((parsed.scheme, parsed.netloc, new_path, parsed.params, parsed.query, parsed.fragment))
    except Exception:
        pass
    return u


def _to_iso(parsed: Any) -> str | None:
    """Convert feedparser time tuple or struct_time to ISO8601 string."""
    if parsed is None:
        return None
    if hasattr(parsed, "tm_year"):
        # struct_time
        try:
            dt = datetime(*parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        except Exception:
            return None
    if isinstance(parsed, str) and parsed.strip():
        return parsed.strip()
    return None


def _str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    return str(x).strip()


def _normalize_feed(parsed: Any, feed_url: str) -> dict[str, Any]:
    """Build standard feed object from feedparser result."""
    feed = getattr(parsed, "feed", None) or {}
    # feed can be a dict-like object (feedparser.FeedDict)
    title = _str(feed.get("title"))
    link = _str(feed.get("link"))
    description = _str(feed.get("description") or feed.get("subtitle") or feed.get("tagline"))
    language = _str(feed.get("language") or feed.get("lang"))
    updated = _to_iso(feed.get("updated_parsed") or feed.get("published_parsed"))
    if not updated:
        updated = _str(feed.get("updated") or feed.get("published"))
    # Detect feed type from parsed
    version = getattr(parsed, "version", "") or ""
    feed_type = "atom" if "atom" in version.lower() else "rss"

    return {
        "title": title or "Untitled",
        "link": link or feed_url,
        "description": description,
        "language": language or None,
        "updated": updated,
        "feed_type": feed_type,
        "url": feed_url,
    }


def _normalize_entry(entry: Any) -> dict[str, Any]:
    """Build standard item object from a feedparser entry."""
    id_ = _str(entry.get("id") or entry.get("guid") or entry.get("link"))
    title = _str(entry.get("title")) or "(No title)"
    link = _str(entry.get("link"))
    published = _to_iso(entry.get("published_parsed") or entry.get("created_parsed"))
    if not published and entry.get("published"):
        published = _str(entry.get("published"))
    updated = _to_iso(entry.get("updated_parsed") or entry.get("modified_parsed"))
    if not updated and entry.get("updated"):
        updated = _str(entry.get("updated"))
    if not updated and published:
        updated = published

    summary = _str(entry.get("summary"))
    content = ""
    if entry.get("content"):
        contents = entry.get("content") or []
        if isinstance(contents, list) and contents:
            content = _str(contents[0].get("value") if isinstance(contents[0], dict) else contents[0])
        elif isinstance(contents, str):
            content = _str(contents)
    if not content and entry.get("description"):
        content = _str(entry.get("description"))

    authors: list[str] = []
    author = _str(entry.get("author") or entry.get("dc_creator"))
    if author:
        authors.append(author)
    for a in entry.get("authors") or []:
        if isinstance(a, dict) and a.get("name"):
            authors.append(_str(a.get("name")))
        elif isinstance(a, str):
            authors.append(_str(a))

    return {
        "id": id_ or link or title,
        "title": title,
        "link": link or None,
        "published": published,
        "updated": updated,
        "summary": summary or None,
        "content": content or None,
        "authors": authors if authors else None,
    }


def fetch_and_parse(feed_url: str) -> dict[str, Any]:
    """Fetch feed URL and return normalized JSON. Raises HTTPException on failure."""
    feed_url = _normalize_google_news_feed_url(feed_url)
    try:
        with httpx.Client(
            timeout=FEED_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": FEED_ACCEPT},
        ) as client:
            r = client.get(feed_url)
            r.raise_for_status()
            body = r.text
            content_type = r.headers.get("content-type", "")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Feed returned {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch feed: {e}") from e

    parsed = feedparser.parse(body, response_headers={"content-type": content_type})
    if getattr(parsed, "bozo", False) and not getattr(parsed, "entries", None):
        exc = getattr(parsed, "bozo_exception", None)
        msg = str(exc) if exc else "Invalid or unsupported feed format"
        raise HTTPException(status_code=422, detail=msg)

    feed_obj = _normalize_feed(parsed, feed_url)
    entries = getattr(parsed, "entries", []) or []
    items = [_normalize_entry(e) for e in entries]
    n = len(items)
    summary = f"Feed: **{feed_url}**, **{n}** items."
    return {
        "summary": summary,
        "feed": feed_obj,
        "items": items,
        "url": feed_url,
        "item_count": n,
    }


class FeedRequest(BaseModel):
    url: str = Field(..., min_length=1)

    @field_validator("url")
    @classmethod
    def _url_valid(cls, v: str) -> str:
        u = v.strip()
        if not (u.startswith("http://") or u.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return u


@router.get("/feed")
def get_feed(url: str) -> dict[str, Any]:
    """Fetch and parse an RSS or Atom feed. Query: url (required). Use when user asks to read or fetch a feed URL."""
    if not url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    return fetch_and_parse(url.strip())


@router.post("/feed")
def post_feed(body: FeedRequest) -> dict[str, Any]:
    """Fetch and parse an RSS or Atom feed. Body: url (required). Use when user asks to read or fetch a feed."""
    return fetch_and_parse(body.url)


def get_router() -> APIRouter:
    return router
