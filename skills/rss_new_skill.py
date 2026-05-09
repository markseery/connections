"""
License: MIT
Description: RSS new-item fetcher as a worker-loadable skill.

Load feed list from data/lists/{list_name}.json, fetch feeds via rss_skill,
diff against storage (rss_notified), return new items within the configured max age only.
For each item fetches the article URL and adds a sanitized "content" field (HTML/JS
removed, whitespace normalized). Does not send email or persist; caller handles notification/persistence.

Input: list_name (required), dry_run (optional), worker_url (optional).
Requires: registry, storage, rss_skill.
"""

from __future__ import annotations

import base64
import html as html_module
import json
import os
import re
import ssl
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.simple.skill_response import skill_result

from common.complex.google_news_decoder import GoogleNewsDecoder
from common.complex.skill_lifecycle import find_live_worker
from common.compound.skill_config import SkillConfig

router = APIRouter()

REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
STORAGE_NAMESPACE = "rss_notified"
_conf = SkillConfig("rss_new_skill")

# ── async job store ──────────────────────────────────────────────────────

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _truncate_to_words(text: str, max_words: int) -> str:
    """Return text truncated to at most max_words (whitespace-separated)."""
    if not text or max_words <= 0:
        return text or ""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def _content_snippet(content: str) -> str:
    """One-line snippet from article content for display (no newlines, truncated)."""
    n = _conf.get("content_snippet_chars", 220)
    if not content:
        return ""
    s = " ".join((content or "").split())
    if len(s) <= n:
        return s.strip()
    cut = s[: n + 1].rsplit(" ", 1)
    return (cut[0] if cut else s[:n]).strip() + "…"


def _ssl_verify_context() -> ssl.SSLContext:
    """Use certifi CA bundle when available so TLS works on macOS (Python.org builds)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()
LISTS_DIR = Path(__file__).resolve().parents[1] / "data" / "lists"
USER_AGENT = "ConnectionsRSSNewSkill/1.0"
# Hosts that are aggregator wrappers; we'll try to extract the real article URL from the page
WRAPPER_HOSTS = frozenset({"news.google.com", "www.news.google.com"})

# Set RSS_NEW_SKILL_DEBUG=1 (or true/yes) for verbose fetch/extract logging to stderr
_DEBUG = os.environ.get("RSS_NEW_SKILL_DEBUG", "").strip().lower() in ("1", "true", "yes")
# When run(body) is called with body.debug=True, logs are appended here and returned in response
_debug_log: list[str] | None = None

_google_news_decoder: GoogleNewsDecoder | None = None


def _get_google_news_decoder() -> GoogleNewsDecoder:
    global _google_news_decoder
    if _google_news_decoder is None:
        _google_news_decoder = GoogleNewsDecoder(
            timeout=_conf.get("content_fetch_timeout", 20.0),
            user_agent=USER_AGENT,
            ssl_verify=_ssl_verify_context(),
            log=_log,
        )
    return _google_news_decoder


def _log(msg: str) -> None:
    if _DEBUG:
        print(f"[rss_new_skill] {msg}", file=sys.stderr, flush=True)
    if _debug_log is not None:
        _debug_log.append(msg)


class RunRequest(BaseModel):
    list_name: str = Field(..., min_length=1, description="Feed list name in data/lists (e.g. ai-news)")
    dry_run: bool = Field(default=False, description="If true, skip storage read; all items in range returned as new")
    worker_url: str | None = Field(default=None, description="Worker base URL; if omitted, discovered from registry")
    debug: bool = Field(default=False, description="If true, include fetch_debug list in response with per-URL log lines")
    skip_content: bool = Field(default=False, description="If true, do not fetch article content; for warmup (save links only)")


def _load_feed_list(list_name: str) -> list[str]:
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
    raise ValueError(f"Invalid list format in {path}")


def _storage_url() -> str:
    env_url = os.environ.get("STORAGE_SERVER_URL", "").strip().rstrip("/")
    if env_url:
        return env_url
    with httpx.Client(timeout=_conf.get("registry_timeout", 5.0)) as client:
        r = client.get(f"{REGISTRY_URL}/servers/storage")
        r.raise_for_status()
        u = (r.json() or {}).get("url")
        if not u:
            raise ValueError("Registry has no storage url")
        return str(u).rstrip("/")


def _item_id_from_link(link: str) -> str:
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
    link = (item.get("link") or "").strip()
    if link:
        c = _item_id_from_link(link)
        if c:
            return c
    return (item.get("id") or item.get("title") or "").strip() or str(id(item))


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


def _list_notified_item_ids(storage_base: str) -> set[str]:
    url = f"{storage_base}/namespaces/{STORAGE_NAMESPACE}/records"
    with httpx.Client(timeout=_conf.get("storage_fetch_timeout", 15.0)) as client:
        r = client.get(url)
        r.raise_for_status()
    data = r.json()
    keys = data.get("keys") if isinstance(data, dict) else None
    return set(keys) if isinstance(keys, list) else set()


def _sanitize_content(html_raw: str, max_chars: int | None = None) -> str:
    """Remove HTML, script, style, normalize whitespace; return plain text."""
    if max_chars is None:
        max_chars = _conf.get("content_max_chars", 100_000)
    if not (html_raw or "").strip():
        return ""
    s = str(html_raw).strip()
    # Remove script and style blocks (and their content)
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining tags
    s = re.sub(r"<[^>]+>", " ", s)
    # Decode HTML entities
    s = html_module.unescape(s)
    # Collapse whitespace and strip
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_chars:
        s = s[:max_chars].rsplit(" ", 1)[0] if " " in s[:max_chars] else s[:max_chars]
    return s


def _unwrap_google_redirect_url(href: str) -> str | None:
    """If href is a Google /url?q=... redirect, return the decoded target URL."""
    _log(f"unwrap_redirect in: {href[:120]}...")
    if not href or not href.startswith(("http://", "https://")):
        _log("unwrap_redirect out: None (bad href)")
        return None
    try:
        p = urlparse(href)
        netloc = (p.netloc or "").lower()
        if "google.com" not in netloc:
            _log("unwrap_redirect out: None (not google)")
            return None
        if p.path != "/url" and not p.path.rstrip("/").endswith("/url"):
            _log(f"unwrap_redirect out: None (path={p.path})")
            return None
        qs = parse_qs(p.query)
        for key in ("q", "url", "u"):
            if key in qs and qs[key]:
                raw = qs[key][0].strip()
                if raw.startswith(("http://", "https://")):
                    _log(f"unwrap_redirect out: {raw[:100]}...")
                    return raw
    except Exception as e:
        _log(f"unwrap_redirect exception: {e}")
    _log("unwrap_redirect out: None")
    return None


def _is_angular_license_stub(text: str) -> bool:
    """True if content is the Angular framework license/footer (Google News often returns this instead of article)."""
    if not text or len(text) < _conf.get("angular_stub_min_chars", 200):
        return False
    t = text.strip()
    return (
        "The MIT License" in t
        and "angular.dev" in t
        and "Angular" in t
    )


def _is_static_resource_url(url: str) -> bool:
    """Return True if url is clearly a static asset (CSS, fonts, etc.), not an article page."""
    if not url or not url.startswith(("http://", "https://")):
        return True
    try:
        p = urlparse(url)
        netloc = (p.netloc or "").lower()
        path = (p.path or "").lower()
        if any(x in netloc for x in ("fonts.", "gstatic", "googleapis")):
            return True
        if path.endswith((".css", ".js", ".woff2", ".woff", ".ttf", ".otf")):
            return True
        if "/css" in path or "fonts.googleapis.com" in url:
            return True
    except Exception:
        pass
    return False


def _extract_article_url(html: str, current_url: str) -> str | None:
    """
    Extract the real article URL from an aggregator wrapper page (e.g. Google News).
    Tries: canonical, og:url, Google /url?q= redirect links, then first outbound https link to a different host.
    """
    _log(f"extract_article_url: current_url={current_url[:80]}..., html_len={len(html or '')}")
    if not (html or "").strip() or not (current_url or "").strip():
        _log("extract_article_url: None (empty html or url)")
        return None
    try:
        parsed = urlparse(current_url)
        current_netloc = (parsed.netloc or "").lower().replace("www.", "", 1)
    except Exception as e:
        _log(f"extract_article_url: None (parse error: {e})")
        return None

    def is_article_domain(netloc: str) -> bool:
        n = (netloc or "").lower().replace("www.", "", 1)
        if not n or n == current_netloc or n in WRAPPER_HOSTS:
            return False
        skip_domains = (
            "google.com", "googleusercontent.com", "gstatic.com",
            "google-analytics", "googletagmanager", "doubleclick", "googleadservices",
            "fonts.googleapis", "fonts.gstatic", "googleapis.com",
            "facebook.com", "twitter.com", "youtube.com", "accounts.",
        )
        if any(skip in n for skip in skip_domains):
            return False
        return True

    # 1. <link rel="canonical" href="..."> (unwrap Google redirect if present)
    m = re.search(r'<link[^>]+rel\s*=\s*["\']canonical["\'][^>]+href\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not m:
        m = re.search(r'<link[^>]+href\s*=\s*["\']([^"\']+)["\'][^>]+rel\s*=\s*["\']canonical["\']', html, re.IGNORECASE)
    if m:
        href = m.group(1).strip()
        _log(f"extract step 1 canonical: href={href[:80]}...")
        if href.startswith(("http://", "https://")):
            unwrapped = _unwrap_google_redirect_url(href)
            if unwrapped and is_article_domain(urlparse(unwrapped).netloc):
                _log(f"extract_article_url: return (canonical unwrapped) {unwrapped[:80]}...")
                return unwrapped
            if is_article_domain(urlparse(href).netloc):
                _log(f"extract_article_url: return (canonical) {href[:80]}...")
                return href
    else:
        _log("extract step 1: no canonical")

    # 2. <meta property="og:url" content="...">
    m = re.search(r'<meta[^>]+property\s*=\s*["\']og:url["\'][^>]+content\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not m:
        m = re.search(r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+property\s*=\s*["\']og:url["\']', html, re.IGNORECASE)
    if m:
        href = m.group(1).strip()
        _log(f"extract step 2 og:url: href={href[:80]}...")
        if href.startswith(("http://", "https://")):
            unwrapped = _unwrap_google_redirect_url(href)
            if unwrapped and is_article_domain(urlparse(unwrapped).netloc):
                _log(f"extract_article_url: return (og:url unwrapped) {unwrapped[:80]}...")
                return unwrapped
            if is_article_domain(urlparse(href).netloc):
                _log(f"extract_article_url: return (og:url) {href[:80]}...")
                return href
    else:
        _log("extract step 2: no og:url")

    # 3. Any <a href="..."> that is a Google /url?q=... redirect to an article domain
    a_count = 0
    for m in re.finditer(r'<a[^>]+href\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE):
        a_count += 1
        href = m.group(1).strip().split("#")[0]
        abs_url = href if href.startswith(("http://", "https://")) else urljoin(current_url, href)
        unwrapped = _unwrap_google_redirect_url(abs_url)
        if unwrapped and is_article_domain(urlparse(unwrapped).netloc):
            _log(f"extract_article_url: return (step 3 a href #{a_count} unwrapped) {unwrapped[:80]}...")
            return unwrapped
    _log(f"extract step 3: checked {a_count} <a> links, no google redirect to article")

    # 4. First direct <a href="https://..."> to a different article host
    for m in re.finditer(r'<a[^>]+href\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE):
        href = m.group(1).strip().split("#")[0]
        if not href.startswith(("http://", "https://")):
            href = urljoin(current_url, href)
        if not href.startswith(("http://", "https://")):
            continue
        try:
            p = urlparse(href)
            if is_article_domain(p.netloc):
                _log(f"extract_article_url: return (step 4 direct link) {href[:80]}...")
                return href
        except Exception:
            continue
    _log("extract step 4: no direct outbound article link")

    # 5. Scan raw HTML for embedded https URLs (e.g. in JSON-LD, data attributes, or script)
    # Match https://domain/path until " ", "'", '"', ")", "]", "}", "\\", or ">"
    embedded_count = 0
    for m in re.finditer(r'https://[^\s\'")\]\}\\>]+', html):
        embedded_count += 1
        cand = m.group(0).rstrip(".,;:")
        try:
            p = urlparse(cand)
            if not p.netloc:
                continue
            if is_article_domain(p.netloc):
                _log(f"extract_article_url: return (step 5 embedded #{embedded_count}) {cand[:80]}...")
                return cand
        except Exception:
            continue
    _log(f"extract step 5: checked {embedded_count} embedded https URLs, none article domain")
    _log("extract_article_url: return None")
    return None


def _fetch_page_content(url: str) -> str:
    """Fetch URL and return sanitized plain-text content. For Google News URLs, decodes via GoogleNewsDecoder first."""
    _log(f"fetch_page_content: url={url[:100]}...")
    if not (url or "").strip().startswith(("http://", "https://")):
        _log("fetch_page_content: skip (invalid url)")
        return ""
    try:
        decoder = _get_google_news_decoder()
        if decoder.is_google_news_article_url(url):
            decoded = decoder.decode(url)
            if decoded and decoded != url:
                _log("fetch_page_content: using decoded URL for content fetch")
                url = decoded
        with httpx.Client(
            timeout=_conf.get("content_fetch_timeout", 20.0),
            follow_redirects=True,
            verify=_ssl_verify_context(),
            headers={"User-Agent": USER_AGENT},
        ) as client:
            r = client.get(url)
            _log(f"fetch_page_content: initial GET status={r.status_code} final_url={r.url!s} content_type={r.headers.get('content-type', '')}")
            if not r.is_success:
                _log("fetch_page_content: return '' (initial request failed)")
                return ""
            ctype = (r.headers.get("content-type") or "").lower()
            if "text/html" not in ctype and "application/xhtml" not in ctype:
                _log(f"fetch_page_content: return '' (not html, ctype={ctype[:50]})")
                return ""
            html = r.text or ""
            final_url = str(r.url)
            _log(f"fetch_page_content: html_len={len(html)} final_url={final_url[:80]}...")
            content = _sanitize_content(html)
            _log(f"fetch_page_content: sanitized content_len={len(content)} preview={repr(content[:150])}")
            # If content is stub (short, "Google News", or Angular license boilerplate), try to get real article URL
            if len(content) < _conf.get("min_content_length", 250) or _is_angular_license_stub(content):
                min_len = _conf.get("min_content_length", 250)
                reason = "Angular stub" if _is_angular_license_stub(content) else f"content short (< {min_len})"
                _log(f"fetch_page_content: {reason}, trying extract_article_url")
                article_url = _extract_article_url(html, final_url)
                if article_url and _is_static_resource_url(article_url):
                    _log(f"fetch_page_content: article_url looks like static resource, ignoring")
                    article_url = None
                _log(f"fetch_page_content: article_url={article_url[:100] + '...' if article_url else 'None'}")
                if article_url and article_url != url:
                    _log(f"fetch_page_content: GET article_url")
                    r2 = client.get(article_url)
                    _log(f"fetch_page_content: second GET status={r2.status_code} final_url={r2.url!s} content_type={r2.headers.get('content-type', '')}")
                    if r2.is_success:
                        ctype2 = (r2.headers.get("content-type") or "").lower()
                        if "text/html" in ctype2 or "application/xhtml" in ctype2:
                            content = _sanitize_content(r2.text or "")
                            _log(f"fetch_page_content: second content_len={len(content)}")
                        else:
                            _log(f"fetch_page_content: second response not html, ctype2={ctype2[:50]}")
                    else:
                        _log(f"fetch_page_content: second GET failed status={r2.status_code}")
                else:
                    _log("fetch_page_content: no article_url or same as original, keeping initial content")
            _log(f"fetch_page_content: return content_len={len(content)}")
            return content
    except Exception as e:
        _log(f"fetch_page_content: exception {type(e).__name__}: {e}")
        return ""


def _fetch_feed(worker_url: str, feed_url: str) -> dict[str, Any] | None:
    with httpx.Client(timeout=_conf.get("feed_timeout", 45.0)) as client:
        r = client.post(f"{worker_url}/skills/rss_skill/feed", json={"url": feed_url})
        if not r.is_success:
            return None
        return r.json()


def _process_feed(
    worker_url: str,
    feed_url: str,
    seen: set[str],
) -> tuple[list[dict[str, str]], list[str], bool]:
    data = _fetch_feed(worker_url, feed_url)
    if not data:
        _log(f"feed failed (no data): {feed_url[:80]}...")
        return [], [], False
    items = data.get("items") or []
    feed_title = (data.get("feed") or {}).get("title") or feed_url
    if not items:
        _log(f"feed returned 0 items (may need auth for Google Alerts): {feed_url[:80]}...")
        return [], [], True

    entries = []
    ids_to_add = []
    for item in items:
        iid = _item_id(item)
        if iid in seen:
            continue
        seen.add(iid)
        ids_to_add.append(iid)
        if not _published_within_days((item.get("published") or item.get("updated") or "").strip(), _conf.get("max_age_days", 30)):
            continue
        entries.append({
            "feed_title": feed_title,
            "title": (item.get("title") or "(No title)").strip(),
            "link": (item.get("link") or "").strip(),
            "published": (item.get("published") or item.get("updated") or "").strip(),
        })
    return entries, ids_to_add, True


def _execute_run(body: RunRequest) -> dict[str, Any]:
    """Core processing logic for fetching new RSS items."""
    global _debug_log
    list_name = body.list_name.strip()
    dry_run = body.dry_run
    skip_content = body.skip_content
    _debug_log = [] if body.debug else None
    if skip_content:
        print("[rss_new_skill] skip_content=True: no article fetch, links only", file=sys.stderr, flush=True)
        _log("skip_content=True: no article fetch")

    t0 = time.perf_counter()
    feeds = _load_feed_list(list_name)
    elapsed = time.perf_counter() - t0
    msg = f"Loaded feed list in {elapsed:.2f}s: {len(feeds)} feeds"
    print(f"[rss_new_skill] {msg}", file=sys.stderr, flush=True)
    _log(msg)

    storage_base = ""
    if not dry_run:
        storage_base = _storage_url()

    worker_url = (body.worker_url or "").strip().rstrip("/")
    if not worker_url:
        w = find_live_worker(REGISTRY_URL)
        if not w:
            raise RuntimeError("No live worker in registry")
        worker_url = w.rstrip("/")

    t0 = time.perf_counter()
    seen: set[str] = _list_notified_item_ids(storage_base) if not dry_run else set()
    elapsed = time.perf_counter() - t0
    msg = f"Storage fetch in {elapsed:.2f}s: {len(seen)} already notified"
    print(f"[rss_new_skill] {msg}", file=sys.stderr, flush=True)
    _log(msg)

    all_entries: list[dict[str, str]] = []
    new_item_ids: list[str] = []
    errors = 0

    for i, feed_url in enumerate(feeds):
        try:
            t0 = time.perf_counter()
            entries, ids_to_add, ok = _process_feed(worker_url, feed_url, seen)
            elapsed = time.perf_counter() - t0
            short_url = (feed_url[:56] + "...") if len(feed_url) > 56 else feed_url
            msg = f"Feed {i + 1}/{len(feeds)} in {elapsed:.2f}s: {len(entries)} new entries ({short_url})"
            print(f"[rss_new_skill] {msg}", file=sys.stderr, flush=True)
            _log(msg)
            if not ok:
                errors += 1
                continue
            all_entries.extend(entries)
            new_item_ids.extend(ids_to_add)
        except Exception:
            errors += 1

    if skip_content:
        for entry in all_entries:
            entry["content"] = ""
    else:
        def _fetch_content_for_link(link: str) -> str:
            try:
                return _fetch_page_content(link) if link else ""
            except Exception:
                return ""

        links = [(entry.get("link") or "").strip() for entry in all_entries]
        n_links = len(links)
        msg = f"Fetching content for {n_links} items (workers={_conf.get('content_fetch_max_workers', 6)})..."
        print(f"[rss_new_skill] {msg}", file=sys.stderr, flush=True)
        _log(msg)
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=_conf.get("content_fetch_max_workers", 6)) as executor:
            contents = list(executor.map(_fetch_content_for_link, links))
        elapsed = time.perf_counter() - t0
        msg = f"Content fetch done in {elapsed:.2f}s for {n_links} items"
        print(f"[rss_new_skill] {msg}", file=sys.stderr, flush=True)
        _log(msg)
        for entry, content in zip(all_entries, contents):
            entry["content"] = content

        if n_links > _conf.get("content_cap_links_threshold", 100):
            cap_words = _conf.get("content_cap_words", 500)
            thresh = _conf.get("content_cap_links_threshold", 100)
            for entry in all_entries:
                entry["content"] = _truncate_to_words(
                    entry.get("content") or "", cap_words
                )
            msg = f"Capped content to {cap_words} words per item (>{thresh} links)"
            print(f"[rss_new_skill] {msg}", file=sys.stderr, flush=True)
            _log(msg)

        decoder = _get_google_news_decoder()
        filtered_entries = []
        filtered_ids = []
        for i, entry in enumerate(all_entries):
            link = (entry.get("link") or "").strip()
            content = (entry.get("content") or "").strip()
            if decoder.is_google_news_article_url(link) and (
                len(content) < _conf.get("min_content_length", 250)
                or content == "Google News"
                or _is_angular_license_stub(content)
            ):
                continue
            filtered_entries.append(entry)
            filtered_ids.append(new_item_ids[i])
        all_entries = filtered_entries
        new_item_ids = filtered_ids

    n_feeds = len(feeds)
    n_new = len(all_entries)
    summary = f"**{list_name}**: {n_new} new items from {n_feeds} feeds."
    items = [
        {
            "title": e.get("title") or "Untitled",
            "link": e.get("link") or "",
            "summary": _content_snippet(e.get("content") or ""),
        }
        for e in all_entries
    ]
    extra: dict[str, Any] = dict(
        ok=errors == 0,
        list_name=list_name,
        dry_run=dry_run,
        feeds_count=n_feeds,
        already_notified_count=len(seen),
        new_items_count=n_new,
        new_items=all_entries,
        new_item_ids=new_item_ids,
        errors=errors,
    )
    if _debug_log is not None:
        extra["fetch_debug"] = _debug_log
    _debug_log = None
    return skill_result(summary=summary, items=items, **extra)


@router.post("/run")
def run(body: RunRequest) -> dict[str, Any]:
    """Accept a run request, execute in a background thread, return job_id immediately.
    Poll GET /jobs/{job_id} for status and results. Use when user asks for new items from a feed list."""
    job_id = str(uuid.uuid4())

    try:
        _load_feed_list(body.list_name.strip())
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "result": None,
            "error": None,
        }

    def _bg() -> None:
        try:
            result = _execute_run(body)
            with _jobs_lock:
                _jobs[job_id]["status"] = "completed"
                _jobs[job_id]["result"] = result
                _jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            with _jobs_lock:
                _jobs[job_id]["status"] = "failed"
                _jobs[job_id]["error"] = str(exc)
                _jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()

    threading.Thread(target=_bg, daemon=True).start()

    return {"job_id": job_id, "status": "running", "poll": f"/skills/rss_new_skill/jobs/{job_id}"}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    """Poll job status. Returns full result when completed."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    out: dict[str, Any] = {
        "job_id": job_id,
        "status": job["status"],
        "started_at": job.get("started_at"),
    }
    if job["status"] == "completed":
        out["result"] = job["result"]
        out["completed_at"] = job.get("completed_at")
    elif job["status"] == "failed":
        out["error"] = job["error"]
        out["completed_at"] = job.get("completed_at")
    return out


def get_router() -> APIRouter:
    return router
