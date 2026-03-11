"""
License: MIT
Description: Webscraper skill (connections) — crawl a website, extract text, optionally summarize via aiserver.

Adapted from apps/agents webscraper_skill:
- Exposes an APIRouter (worker-loadable), not a standalone FastAPI app.
- Uses the local registry to find aiserver (REGISTRY_SERVER_URL) and calls /generate.
- Stores markdown files under ./data/skills/webscraper_skill/.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, field_validator


router = APIRouter()

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "skills" / "webscraper_skill"
DATA_DIR.mkdir(parents=True, exist_ok=True)

_jobs: dict[str, dict[str, Any]] = {}


class ScrapeRequest(BaseModel):
    url: str
    max_pages: int = Field(default=30, ge=1, le=200)
    max_depth: int = Field(default=2, ge=1, le=8)
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    summarize: bool = True

    @field_validator("url")
    @classmethod
    def _url_valid(cls, v: str) -> str:
        u = v.strip()
        if not (u.startswith("http://") or u.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return u


STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "is",
        "was",
        "are",
        "were",
        "be",
        "this",
        "that",
        "these",
        "those",
        "you",
        "your",
        "our",
        "their",
        "we",
        "they",
        "it",
        "its",
        "not",
        "can",
        "will",
        "would",
        "should",
        "may",
        "might",
        "must",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "click",
        "here",
        "privacy",
        "terms",
    }
)


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


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"\b[a-zA-Z]+\b", text.lower())
    return [w for w in words if len(w) > 2 and w not in STOP_WORDS]


def _top_phrases(text: str, n: int = 10) -> list[tuple[str, int]]:
    words = _tokenize(text)
    return Counter(words).most_common(n)


async def _crawl(job: dict[str, Any], req: ScrapeRequest) -> tuple[list[dict[str, Any]], str]:
    parsed = urlparse(req.url)
    base_domain = parsed.netloc
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(req.url, 0)]
    pages: list[dict[str, Any]] = []

    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": "ConnectionsWebScraper/1.0"},
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
            except Exception:
                job["pages_failed"] += 1
                continue

    markdown = _build_markdown(req.url, pages)
    return pages, markdown


def _build_markdown(root_url: str, pages: list[dict[str, Any]]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"# Website Content: {root_url}", "", f"*Crawled {len(pages)} pages on {now} UTC*", "", "---", ""]
    for p in pages:
        lines.append(f"## {p.get('title') or p.get('url')}")
        lines.append("")
        lines.append(f"**URL:** {p.get('url')}")
        lines.append("")
        lines.append(str(p.get("content") or "")[:3000])
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def _registry_url() -> str:
    return os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")


def _aiserver_url() -> str:
    reg = _registry_url()
    with httpx.Client(timeout=3.0) as client:
        r = client.get(f"{reg}/servers/aiserver")
        r.raise_for_status()
        return str(r.json().get("url")).rstrip("/")


async def _summarize(markdown: str) -> str:
    aiserver = _aiserver_url()
    prompt = (
        "Summarize the following website content. Be concise, bullet points, and list key themes.\n\n"
        f"{markdown[:15000]}"
    )
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{aiserver}/generate", json={"prompt": prompt, "profile": "fast"})
        r.raise_for_status()
        out = r.json().get("output") or {}
        if isinstance(out, dict):
            return str(out.get("text") or "")
        return str(out)


async def _run_job(job_id: str, req: ScrapeRequest) -> None:
    job = _jobs[job_id]
    job["status"] = "crawling"
    pages, md = await _crawl(job, req)
    if not pages:
        job["status"] = "failed"
        job["error"] = "No pages crawled"
        job["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return

    md_path = DATA_DIR / f"{job_id}.md"
    md_path.write_text(md, encoding="utf-8")
    job["markdown_path"] = str(md_path)

    combined_text = " ".join(p.get("content", "") for p in pages)
    job["top_words"] = _top_phrases(combined_text, n=15)

    if req.summarize:
        job["status"] = "summarizing"
        try:
            job["summary"] = await _summarize(md)
        except Exception as e:
            job["summary"] = ""
            job["error"] = f"summary failed: {e}"

    job["status"] = "completed"
    job["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@router.post("/scrape")
async def start_scrape(body: ScrapeRequest, response: Response) -> dict[str, Any]:
    start = time.perf_counter()
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "url": body.url,
        "status": "pending",
        "pages_crawled": 0,
        "pages_failed": 0,
        "pages_skipped": 0,
        "urls_visited": 0,
        "max_pages": body.max_pages,
        "max_depth": body.max_depth,
        "summary": None,
        "top_words": [],
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "completed_at": None,
        "markdown_path": None,
    }
    _jobs[job_id] = job
    asyncio.create_task(_run_job(job_id, body))
    response.headers["X-Processing-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
    return {"job_id": job_id, "status": "pending", "url": body.url}


@router.get("/scrape")
def list_jobs(offset: int = 0, limit: int = 100) -> dict[str, Any]:
    all_jobs = sorted(_jobs.values(), key=lambda j: j.get("created_at", ""), reverse=True)
    page = all_jobs[offset : offset + limit]
    safe = [{k: v for k, v in j.items() if k != "markdown_path"} for j in page]
    return {"total": len(all_jobs), "offset": offset, "limit": limit, "jobs": safe}


@router.get("/scrape/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {k: v for k, v in job.items() if k != "markdown_path"}


@router.get("/scrape/{job_id}/markdown")
def get_markdown(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    p = job.get("markdown_path")
    if not p:
        raise HTTPException(status_code=404, detail="Markdown not ready")
    path = Path(str(p))
    if not path.exists():
        raise HTTPException(status_code=404, detail="Markdown file missing")
    return {"job_id": job_id, "url": job["url"], "markdown": path.read_text(encoding="utf-8")}


@router.get("/scrape/{job_id}/summary")
def get_summary(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "completed":
        raise HTTPException(status_code=400, detail=f"Job not completed (status={job.get('status')})")
    return {"job_id": job_id, "url": job["url"], "summary": job.get("summary"), "top_words": job.get("top_words")}


def get_router() -> APIRouter:
    return router

