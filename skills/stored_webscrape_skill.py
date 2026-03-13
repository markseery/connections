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

from decorations.monitor import monitor

router = APIRouter()

STORAGE_NAMESPACE = "webscrape"

_jobs: dict[str, dict[str, Any]] = {}


def _registry_url() -> str:
    return os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")


def _storage_url() -> str:
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

    try:
        storage_base = _storage_url()
        key_encoded = quote(base_url, safe="")
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
    """
    Start a background crawl of the given URL and same-domain links. Progress can be
    polled via GET /scrape/{job_id}. On completion, URLs + content are stored in the
    storage server under namespace \"webscrape\" with key = base URL.
    """
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
    return {"job_id": job_id, "status": "pending", "base_url": body.url}


@monitor
@router.get("/scrape/{job_id}")
def get_scrape_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return dict(job)


@monitor
@router.get("/scrape")
def list_scrape_jobs(offset: int = 0, limit: int = 100) -> dict[str, Any]:
    all_jobs = sorted(_jobs.values(), key=lambda j: j.get("created_at", ""), reverse=True)
    page = all_jobs[offset : offset + limit]
    return {"total": len(all_jobs), "offset": offset, "limit": limit, "jobs": page}


@monitor
@router.get("/stored")
def get_stored_by_query(base_url: str) -> dict[str, Any]:
    """
    Retrieve a stored scrape by base URL (query param). Use this when the URL
    is provided by another skill/plan; no path-encoding needed.
    """
    if not base_url.strip():
        raise HTTPException(status_code=400, detail="base_url is required")
    return _fetch_stored(base_url.strip())


def _fetch_stored(base_url: str) -> dict[str, Any]:
    storage_base = _storage_url()
    key_encoded = quote(base_url, safe="")
    url = f"{storage_base}/namespaces/{STORAGE_NAMESPACE}/records/{key_encoded}"

    with httpx.Client(timeout=10.0) as client:
        r = client.get(url)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Stored scrape not found for this base URL")
        r.raise_for_status()

    data = r.json()
    value = data.get("value")
    if value is None:
        raise HTTPException(status_code=502, detail="Storage returned no value")
    return {"namespace": STORAGE_NAMESPACE, "key": base_url, "value": value}


@monitor
@router.get("/stored/{key:path}")
def get_stored_by_path(key: str) -> dict[str, Any]:
    """
    Retrieve a stored scrape by base URL (path-encoded). E.g. use key
    https%3A%2F%2Fexample.com for https://example.com.
    """
    return _fetch_stored(key)


@monitor
@router.get("/list")
def list_stored() -> dict[str, Any]:
    """List all stored scrape keys (base URLs) in namespace webscrape."""
    storage_base = _storage_url()
    url = f"{storage_base}/namespaces/{STORAGE_NAMESPACE}/records"

    with httpx.Client(timeout=10.0) as client:
        r = client.get(url)
        r.raise_for_status()

    data = r.json()
    keys = data.get("keys") if isinstance(data.get("keys"), list) else []
    return {"namespace": STORAGE_NAMESPACE, "keys": keys, "count": len(keys)}
