"""
License: MIT
Description: Stored webscrape skill — crawl a website, extract text, and persist
URLs + content in the storage server under namespace "webscrape" with key = base URL.
Other skills/plans can retrieve stored scrapes for repeated analysis without re-scraping.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse, quote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from common.skill_response import skill_result

from decorations.monitor import monitor

router = APIRouter()

STORAGE_NAMESPACE = "webscrape"

_jobs: dict[str, dict[str, Any]] = {}


def _canonical_base_url(url: str) -> str:
    """Single canonical form for storage key so store and load always match."""
    u = (url or "").strip().rstrip("/")
    return u or url


def _registry_url() -> str:
    return os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")


def _storage_url() -> str:
    """Storage server URL: prefer STORAGE_SERVER_URL from env (set by supervisor), else registry."""
    env_url = os.environ.get("STORAGE_SERVER_URL", "").strip().rstrip("/")
    if env_url:
        return env_url
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{_registry_url()}/servers/storage")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError("Registry missing url for storage")
        return str(url).rstrip("/")


def _extract_text_from_html(html: str) -> str:
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if not m:
        return ""
    return re.sub(r"<[^>]+>", "", m.group(1)).strip()


def _extract_links(html: str, base_url: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"']", html, re.IGNORECASE):
        href = match.group(1).strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        if absolute and absolute not in links:
            links.append(absolute)
    return links


class ScrapeRequest(BaseModel):
    url: str
    max_pages: int = Field(default=30, ge=1, le=2000)
    max_depth: int = Field(default=2, ge=1, le=15)
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def _url_valid(cls, v: str) -> str:
        u = v.strip()
        if not (u.startswith("http://") or u.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return u


async def _crawl(job: dict[str, Any], req: ScrapeRequest) -> list[dict[str, Any]]:
    parsed = urlparse(req.url)
    base_domain = parsed.netloc
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(req.url, 0)]
    pages: list[dict[str, Any]] = []

    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": "ConnectionsStoredWebScrape/1.0"},
    ) as client:
        while queue and len(pages) < req.max_pages:
            url, depth = queue.pop(0)
            if url in visited or depth > req.max_depth:
                continue
            if req.exclude_patterns and any(fnmatch.fnmatch(url, p) for p in req.exclude_patterns):
                continue
            if req.include_patterns and url != req.url and not any(fnmatch.fnmatch(url, p) for p in req.include_patterns):
                continue

            visited.add(url)
            job["urls_visited"] = len(visited)
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    job["pages_skipped"] += 1
                    continue
                ctype = r.headers.get("content-type", "")
                if "text/html" not in ctype:
                    job["pages_skipped"] += 1
                    continue
                html = r.text
                text = _extract_text_from_html(html)
                if len(text) < 80:
                    job["pages_skipped"] += 1
                    continue

                title = _extract_title(html) or url
                pages.append({"url": url, "title": title, "content": text[:8000], "depth": depth})
                job["pages_crawled"] = len(pages)

                for link in _extract_links(html, url):
                    pl = urlparse(link)
                    if pl.netloc == base_domain and link not in visited:
                        queue.append((link, depth + 1))
                await asyncio.sleep(0.1)
            except Exception as exc:
                print(f"[stored_webscrape_skill] page scrape failed for {url}: {exc}", flush=True)
                job["pages_failed"] += 1
                continue

    return pages


def _build_stored_payload(base_url: str, pages: list[dict[str, Any]]) -> dict[str, Any]:
    scraped_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    urls = [p["url"] for p in pages]
    content_by_url = {p["url"]: (p.get("content") or "") for p in pages}
    return {
        "base_url": base_url,
        "scraped_at": scraped_at,
        "urls": urls,
        "content_by_url": content_by_url,
    }


async def _run_job(job_id: str, req: ScrapeRequest) -> None:
    job = _jobs[job_id]
    job["status"] = "crawling"
    job["started_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    pages = await _crawl(job, req)
    if not pages:
        job["status"] = "failed"
        job["error"] = "No pages crawled; check URL and filters"
        job["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return

    base_url = req.url
    payload = _build_stored_payload(base_url, pages)
    storage_key = _canonical_base_url(base_url)

    try:
        storage_base = _storage_url()
        key_encoded = quote(storage_key, safe="")
        put_url = f"{storage_base}/namespaces/{STORAGE_NAMESPACE}/records/{key_encoded}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.put(put_url, json=payload)
            r.raise_for_status()
        job["stored"] = True
        job["url_count"] = len(pages)
        job["scraped_at"] = payload["scraped_at"]
        job["status"] = "completed"
        job["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = f"Failed to store scrape: {exc}"
        job["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@monitor
@router.post("/scrape")
async def start_scrape(body: ScrapeRequest) -> dict[str, Any]:
    """Crawl a website and store pages. Body: url (required), max_pages (optional), max_depth (optional). Returns job_id; poll GET /scrape/{job_id} for status. Use when user asks to scrape or crawl a site."""
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _jobs[job_id] = {
        "job_id": job_id,
        "url": body.url,
        "status": "pending",
        "pages_crawled": 0,
        "pages_failed": 0,
        "pages_skipped": 0,
        "urls_visited": 0,
        "max_pages": body.max_pages,
        "max_depth": body.max_depth,
        "stored": False,
        "error": None,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "scraped_at": None,
        "url_count": 0,
    }
    asyncio.create_task(_run_job(job_id, body))
    return skill_result(summary=f"Scrape job started for **{body.url}**.", job_id=job_id, status="pending", base_url=body.url)


@monitor
@router.get("/scrape/{job_id}")
def get_scrape_job(job_id: str) -> dict[str, Any]:
    """Get scrape job status and result by job_id. Use after POST /scrape to poll until completed."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    out = dict(job)
    return skill_result(summary=f"Job **{job_id}**: {job.get('status', 'unknown')} — {job.get('url', '')}", **out)


@monitor
@router.get("/scrape")
def list_scrape_jobs(offset: int = 0, limit: int = 100) -> dict[str, Any]:
    """List scrape jobs. Query: offset, limit. Use when user asks to list or see crawl jobs."""
    all_jobs = sorted(_jobs.values(), key=lambda j: j.get("created_at", ""), reverse=True)
    page = all_jobs[offset : offset + limit]
    total = len(all_jobs)
    items = [{"title": j.get("job_id", ""), "link": j.get("url", ""), "summary": j.get("status", "")} for j in page]
    return skill_result(summary=f"**{total}** scrape jobs.", items=items, total=total, offset=offset, limit=limit, jobs=page)


# Basic English stop words; removed before truncation to keep more meaningful content within max_chars.
_STOP_WORDS = frozenset(
    "a an and are as at be by for from has he in is it its of on that the to was were will with".split()
)


def _remove_stopwords(text: str) -> str:
    """Remove basic stop words and normalize whitespace. Runs before truncation to reduce character count.
    Does not strip ':' so URLs (e.g. https:) are never altered."""
    if not (text or "").strip():
        return ""
    words = text.split()
    # Strip only punctuation that cannot be part of a URL (omit ':', '/', '.', '?', '&')
    strip_for_check = ",;!\"'()[]"
    kept = [
        w for w in words
        if w.lower().strip(strip_for_check) not in _STOP_WORDS
    ]
    return " ".join(kept).strip()


# Format of combined_text: sections joined by PAGE_SEP; each section is "URL: {url}\n\n{content}".
# Truncation may cut a block mid-way, so the last segment might be content-only (no "URL: " line).
PAGE_SEP = "\n\n---\n\n"
URL_LINE_PREFIX = "URL: "


def parse_combined_text(combined_text: str) -> list[tuple[str, str]]:
    """
    Parse combined_text back into (url, content) pairs. Page boundaries are marked by PAGE_SEP
    and each section starts with "URL: <url>" then newlines then content. Segments without
    "URL: " (e.g. from truncation) are appended to the previous page's content.
    """
    if not (combined_text or "").strip():
        return []
    segments = combined_text.strip().split(PAGE_SEP)
    pages: list[tuple[str, str]] = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if seg.startswith(URL_LINE_PREFIX):
            first_newline = seg.find("\n")
            if first_newline >= 0:
                url = seg[len(URL_LINE_PREFIX):first_newline].strip()
                content = seg[first_newline:].strip()
            else:
                url = seg[len(URL_LINE_PREFIX):].strip()
                content = ""
            pages.append((url, content))
        elif pages:
            # Truncation left content-only segment; append to last page
            last_url, last_content = pages[-1]
            pages[-1] = (last_url, (last_content + "\n\n" + seg).strip())
    return pages


def _combined_text(value: dict[str, Any], max_chars: int) -> str:
    """Build one text block from content_by_url: each section starts with URL so the page is always identified."""
    content_by_url = value.get("content_by_url") or {}
    parts: list[str] = []
    total = 0
    for url, text in content_by_url.items():
        if total >= max_chars:
            break
        # Remove stop words from body only; URL is never modified
        reduced = _remove_stopwords(text or "")
        block = f"{URL_LINE_PREFIX}{url}\n\n{reduced}"
        chunk = block[: max_chars - total]
        if chunk:
            parts.append(chunk)
            total += len(chunk)
    return PAGE_SEP.join(parts) if parts else ""


class StoredRequest(BaseModel):
    """Body for POST /stored (e.g. from run_prompt_with_context skill steps)."""
    base_url: str = Field(..., min_length=1, description="Base URL of the site (e.g. https://example.com)")
    max_chars: int | None = Field(default=None, ge=1, le=2_000_000, description="If set, include combined_text truncated to this many chars (avoids exceeding AI context)")


@monitor
@router.post("/stored")
def post_stored(body: StoredRequest) -> dict[str, Any]:
    """Get previously scraped site content by base URL. Body: base_url (required), max_chars (optional). Use when user asks for stored or cached site content."""
    out = _fetch_stored(body.base_url.strip())
    if body.max_chars is not None:
        data = out.get("data") or {}
        value = data.get("value")
        if isinstance(value, dict):
            combined = _combined_text(value, body.max_chars)
            data["combined_text"] = combined
            data["combined_text_length"] = len(combined)
            data["url_count"] = len(value.get("content_by_url") or value.get("urls") or [])
            out["data"] = data
    return out


class ParseCombinedRequest(BaseModel):
    """Body for POST /parse_combined: parse a combined_text block back into (url, content) pages."""
    combined_text: str = Field(..., description="Single block from stored response combined_text field")


@monitor
@router.post("/parse_combined")
def post_parse_combined(body: ParseCombinedRequest) -> dict[str, Any]:
    """Parse combined_text from stored scrape into pages. Body: combined_text (required). Use when splitting stored content into url+content pairs."""
    pages = parse_combined_text(body.combined_text or "")
    items = [{"title": u, "link": u, "summary": (c or "")[:200]} for u, c in pages]
    n = len(pages)
    return skill_result(summary=f"**{n}** pages parsed.", items=items, pages=[{"url": u, "content": c} for u, c in pages], count=n)


@monitor
@router.get("/stored")
def get_stored_by_query(base_url: str) -> dict[str, Any]:
    """Get previously scraped site content by base URL. Query: base_url (required). Use when user asks for stored or cached site content."""
    if not base_url.strip():
        raise HTTPException(status_code=400, detail="base_url is required")
    return _fetch_stored(base_url.strip())


def _fetch_stored(base_url: str) -> dict[str, Any]:
    """Fetch stored scrape by base URL. Uses canonical key; on 404 tries alternate (e.g. with/without trailing slash)."""
    storage_base = _storage_url()
    canonical = _canonical_base_url(base_url)
    keys_to_try = [canonical]
    if base_url.strip() != canonical:
        keys_to_try.append(base_url.strip())

    last_404_detail: str | None = None
    for key in keys_to_try:
        if not key:
            continue
        key_encoded = quote(key, safe="")
        url = f"{storage_base}/namespaces/{STORAGE_NAMESPACE}/records/{key_encoded}"
        with httpx.Client(timeout=10.0) as client:
            r = client.get(url)
            if r.status_code == 404:
                last_404_detail = "Stored scrape not found for this base URL"
                continue
            r.raise_for_status()
        data = r.json()
        value = data.get("value")
        if value is None:
            raise HTTPException(status_code=502, detail="Storage returned no value")
        urls = value.get("urls") if isinstance(value, dict) else []
        n = len(urls) if isinstance(urls, list) else 0
        return skill_result(summary=f"Stored scrape for **{key}**: **{n}** URLs.", namespace=STORAGE_NAMESPACE, key=key, value=value)

    raise HTTPException(status_code=404, detail=last_404_detail or "Stored scrape not found for this base URL")


@monitor
@router.get("/stored/{key:path}")
def get_stored_by_path(key: str) -> dict[str, Any]:
    """Get stored scrape by base URL (path-encoded). Replace {key} with URL-encoded base URL."""
    return _fetch_stored(key)


@monitor
@router.get("/list")
def list_stored() -> dict[str, Any]:
    """List all stored scrape base URLs. Use when user asks what sites have been scraped or cached."""
    storage_base = _storage_url()
    url = f"{storage_base}/namespaces/{STORAGE_NAMESPACE}/records"

    with httpx.Client(timeout=10.0) as client:
        r = client.get(url)
        r.raise_for_status()

    data = r.json()
    keys = data.get("keys") if isinstance(data.get("keys"), list) else []
    n = len(keys)
    items = [{"title": k, "link": k} for k in keys]
    return skill_result(summary=f"**{n}** stored scrapes.", items=items, namespace=STORAGE_NAMESPACE, keys=keys, count=n)
