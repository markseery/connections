"""
License: MIT
Description: Unified web scraper — **one** crawl implementation, multiple persistence surfaces:

- **Per-page storage** (namespace `webscrape` by default): key = sitename + NUL + page URL
  (`?query` stripped on discovery and in API); value includes namespace, sitename, url, content, contenthash.
- **Job-local markdown** under data/skills/webscraper_skill/{job_id}.md
- **Optional** aiserver summary for the whole crawl (body flag `summarize`).

Routes include crawl jobs, markdown/summary retrieval, storage CRUD (/pages, /sites, /stored),
parse_combined, and summarize_text.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import html
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from common.skill_response import skill_result
from decorations.monitor import monitor
from common.skill_lifecycle import find_live_worker

router = APIRouter()

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "skills" / "webscraper_skill"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STORAGE_NAMESPACE = "webscrape"
PAGE_KEY_SEP = "\x00"

_jobs: dict[str, dict[str, Any]] = {}

# When notifying about new URLs, summarize each page with AI if below this count.
NEW_URL_NOTIFY_SUMMARY_MAX = 10


# ── storage (per-page records) ───────────────────────────────────────────


def _strip_url_query(url: str) -> str:
    """Drop ? and everything after it so UTM/query variants share one stored page."""
    u = (url or "").strip()
    q = u.find("?")
    if q >= 0:
        u = u[:q]
    return u


def _canonical_sitename(url: str) -> str:
    u = _strip_url_query(url)
    u = u.rstrip("/")
    return u or (url or "").strip()


def _page_storage_key(sitename: str, page_url: str) -> str:
    sn = _canonical_sitename(sitename)
    pu = _strip_url_query((page_url or "").strip())
    return f"{sn}{PAGE_KEY_SEP}{pu}"


def _parse_page_storage_key(key: str) -> tuple[str, str]:
    if PAGE_KEY_SEP not in key:
        raise ValueError("invalid page key")
    sn, pu = key.split(PAGE_KEY_SEP, 1)
    return sn, pu


def _content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _registry_url() -> str:
    return os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")


def _storage_url() -> str:
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


def _storage_list_keys(client: httpx.Client, storage_base: str, namespace: str) -> list[str]:
    r = client.get(f"{storage_base}/namespaces/{quote(namespace, safe='')}/records")
    r.raise_for_status()
    data = r.json()
    keys = data.get("keys") if isinstance(data.get("keys"), list) else []
    return [str(k) for k in keys]


def _keys_for_site(all_keys: list[str], sitename: str) -> list[str]:
    sn = _canonical_sitename(sitename)
    prefix = sn + PAGE_KEY_SEP
    return [k for k in all_keys if k.startswith(prefix)]


def _storage_keys_matching_normalized_page_url(
    client: httpx.Client,
    storage_base: str,
    namespace: str,
    sitename_canon: str,
    page_url_normalized: str,
) -> list[str]:
    """Storage keys for this site where the page URL matches after stripping ?query (legacy rows kept full URL in key)."""
    all_keys = _storage_list_keys(client, storage_base, namespace)
    matched: list[str] = []
    for k in _keys_for_site(all_keys, sitename_canon):
        try:
            _, raw_pu = _parse_page_storage_key(k)
        except ValueError:
            continue
        if _strip_url_query(raw_pu) == page_url_normalized:
            matched.append(k)
    return matched


def _page_urls_from_keys(keys: list[str]) -> list[str]:
    urls: list[str] = []
    for k in keys:
        try:
            _, u = _parse_page_storage_key(k)
            urls.append(u)
        except ValueError:
            continue
    return sorted(set(urls))


def _load_prior_urls(storage_base: str, namespace: str, sitename: str) -> list[str]:
    """URLs already stored for this namespace + sitename. Empty list if none or on listing error."""
    sn = _canonical_sitename(sitename)
    ns = (namespace or "").strip() or STORAGE_NAMESPACE
    try:
        with httpx.Client(timeout=60.0) as client:
            all_keys = _storage_list_keys(client, storage_base, ns)
    except Exception as exc:
        print(
            f"[webscraper_skill] could not list existing URLs for namespace={ns!r} sitename={sn!r}: {exc}",
            flush=True,
        )
        return []
    keys = _keys_for_site(all_keys, sn)
    return _page_urls_from_keys(keys)


def _send_new_urls_notification(
    to_email: str,
    site_url: str,
    new_urls: list[str],
    namespace: str,
    job_id: str,
    *,
    per_url_summaries: dict[str, str] | None = None,
) -> None:
    worker_url = find_live_worker(_registry_url())
    if not worker_url:
        print("[webscraper_skill] no live worker for email notification; skipping", flush=True)
        return
    worker_url = worker_url.rstrip("/")
    n = len(new_urls)
    subject = f"Web scrape: {n} new page{'s' if n != 1 else ''} — {site_url[:120]}"
    lines = [
        f"Job: {job_id}",
        f"Site: {site_url}",
        f"Namespace: {namespace}",
        f"New URLs ({n}):",
        "",
    ]
    lines.extend(new_urls)
    summaries = per_url_summaries or {}
    if summaries:
        lines.extend(["", "---", "Per-page summaries (terse bullets)", ""])
        for u in new_urls:
            s = summaries.get(u, "").strip()
            lines.append(u)
            lines.append(s if s else "(no summary)")
            lines.append("")
    body = "\n".join(lines)
    safe_urls = "".join(
        f'<li><a href="{html.escape(u, quote=True)}">{html.escape(u)}</a></li>'
        for u in new_urls
    )
    html_summaries = ""
    if summaries:
        parts = ['<h3>Per-page summaries (terse bullets)</h3>']
        for u in new_urls:
            s = summaries.get(u, "").strip() or "(no summary)"
            parts.append(
                f'<h4 style="margin-bottom:0.25em"><a href="{html.escape(u, quote=True)}">'
                f"{html.escape(u)}</a></h4>"
                f'<pre style="white-space:pre-wrap;font-family:system-ui,sans-serif;margin-top:0">'
                f"{html.escape(s)}</pre>"
            )
        html_summaries = "".join(parts)
    html_body = (
        f"<p><b>Job:</b> {html.escape(job_id)}<br><b>Site:</b> {html.escape(site_url)}"
        f"<br><b>Namespace:</b> {html.escape(namespace)}</p>"
        f"<p><b>New URLs ({n})</b></p><ul>{safe_urls}</ul>"
        f"{html_summaries}"
    )
    try:
        with httpx.Client(timeout=25.0) as client:
            load_r = client.post(f"{worker_url}/worker/skills/notification_skill/load")
            if not load_r.is_success:
                print(
                    f"[webscraper_skill] notification_skill load failed: {load_r.status_code} {load_r.text}",
                    flush=True,
                )
                return
            r = client.post(
                f"{worker_url}/skills/notification_skill/send",
                json={
                    "to": [to_email],
                    "subject": subject[:500],
                    "body": body,
                    "html_body": html_body,
                },
            )
            if not r.is_success:
                print(
                    f"[webscraper_skill] notification send failed: {r.status_code} {r.text}",
                    flush=True,
                )
            else:
                print(f"[webscraper_skill] sent new-URL notification to {to_email!r}", flush=True)
    except Exception as exc:
        print(f"[webscraper_skill] notification error: {exc}", flush=True)


def _build_page_value(namespace: str, sitename: str, page_url: str, content: str) -> dict[str, Any]:
    sn = _canonical_sitename(sitename)
    pu = _strip_url_query((page_url or "").strip())
    return {
        "namespace": namespace,
        "sitename": sn,
        "url": pu,
        "content": content if isinstance(content, str) else "",
        "contenthash": _content_hash(content if isinstance(content, str) else ""),
    }


def _record_url(storage_base: str, namespace: str, storage_key: str) -> str:
    return f"{storage_base}/namespaces/{quote(namespace, safe='')}/records/{quote(storage_key, safe='')}"


def _aiserver_url() -> str:
    reg = _registry_url()
    with httpx.Client(timeout=3.0) as client:
        r = client.get(f"{reg}/servers/aiserver")
        r.raise_for_status()
        return str(r.json().get("url")).rstrip("/")


# ── HTML extraction + crawl (single implementation) ──────────────────────


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
        absolute = _strip_url_query(urljoin(base_url, href).split("#")[0])
        if absolute and absolute not in links:
            links.append(absolute)
    return links


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


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"\b[a-zA-Z]+\b", text.lower())
    return [w for w in words if len(w) > 2 and w not in STOP_WORDS]


def _top_phrases(text: str, n: int = 10) -> list[tuple[str, int]]:
    words = _tokenize(text)
    return Counter(words).most_common(n)


class ScrapeRequest(BaseModel):
    url: str
    max_pages: int = Field(default=30, ge=1, le=2000)
    max_depth: int = Field(default=2, ge=1, le=15)
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    summarize: bool = True
    namespace: str = Field(default=STORAGE_NAMESPACE, description="Storage namespace for per-page records.")

    @field_validator("url")
    @classmethod
    def _url_valid(cls, v: str) -> str:
        u = v.strip()
        if not (u.startswith("http://") or u.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return _strip_url_query(u)


async def _crawl(job: dict[str, Any], req: ScrapeRequest) -> tuple[list[dict[str, Any]], str]:
    root = _strip_url_query(req.url.strip())
    parsed = urlparse(root)
    base_domain = parsed.netloc
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(root, 0)]
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
            if req.include_patterns and url != root and not any(fnmatch.fnmatch(url, p) for p in req.include_patterns):
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
                print(f"[webscraper_skill] page scrape failed for {url}: {exc}", flush=True)
                job["pages_failed"] += 1
                continue

    markdown = _build_markdown(root, pages)
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


async def _summarize_page_terse_bullets(
    client: httpx.AsyncClient, aiserver: str, page_text: str
) -> str:
    """AIServer: a few succinct bullet points for one page's text (notification body)."""
    text = (page_text or "")[:12000]
    if len(text) < 40:
        return "(insufficient text for summary)"
    prompt = (
        "Summarize the following web page text in a few terse, succinct bullet points only. "
        "No introduction or conclusion. Use a short bullet list (about 3–5 bullets).\n\n"
        f"{text}"
    )
    r = await client.post(
        f"{aiserver}/generate",
        json={"prompt": prompt, "profile": "fast"},
    )
    r.raise_for_status()
    out = r.json().get("output") or {}
    if isinstance(out, dict):
        return str(out.get("text") or "").strip()
    return str(out).strip()


async def _run_job(job_id: str, req: ScrapeRequest) -> None:
    job = _jobs[job_id]
    job["status"] = "crawling"
    job["started_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    ns = (req.namespace or "").strip() or STORAGE_NAMESPACE
    sitename = _canonical_sitename(req.url)

    prior_urls: list[str] = []
    storage_base = ""
    try:
        storage_base = _storage_url()
        prior_urls = _load_prior_urls(storage_base, ns, sitename)
    except Exception as exc:
        print(
            f"[webscraper_skill] job {job_id}: could not resolve storage or list prior URLs (continuing with none): {exc}",
            flush=True,
        )
    prior_set = {(u or "").strip() for u in prior_urls}
    job["existing_url_count"] = len(prior_set)
    print(
        f"[webscraper_skill] job {job_id}: {len(prior_set)} existing URL(s) in namespace={ns!r} for this site",
        flush=True,
    )

    pages, md = await _crawl(job, req)
    if not pages:
        job["status"] = "failed"
        job["error"] = "No pages crawled; check URL and filters"
        job["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return

    md_path = DATA_DIR / f"{job_id}.md"
    md_path.write_text(md, encoding="utf-8")
    job["markdown_path"] = str(md_path)

    combined_text = " ".join(p.get("content", "") for p in pages)
    job["top_words"] = _top_phrases(combined_text, n=15)

    scraped_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        if not storage_base:
            storage_base = _storage_url()
        async with httpx.AsyncClient(timeout=120.0) as client:
            for p in pages:
                sk = _page_storage_key(sitename, p["url"])
                body = _build_page_value(ns, sitename, p["url"], p.get("content") or "")
                r = await client.put(_record_url(storage_base, ns, sk), json=body)
                r.raise_for_status()
        job["stored"] = True
        job["url_count"] = len(pages)
        job["scraped_at"] = scraped_at
        job["namespace"] = ns
        job["sitename"] = sitename

        crawled_ordered: list[str] = []
        seen_c: set[str] = set()
        for p in pages:
            u = (p.get("url") or "").strip()
            if u and u not in seen_c:
                seen_c.add(u)
                crawled_ordered.append(u)
        new_urls = [u for u in crawled_ordered if u not in prior_set]
        job["new_urls"] = new_urls

        print(f"[webscraper_skill] job {job_id}: {len(new_urls)} new URL(s) (not previously stored):", flush=True)
        if new_urls:
            for u in new_urls:
                print(f"  {u}", flush=True)
        else:
            print("  (none)", flush=True)

        to_email = (os.environ.get("EMAIL_RECEIVER_DEFAULT") or "").strip()
        if new_urls and to_email:
            per_url_summaries: dict[str, str] | None = None
            if len(new_urls) < NEW_URL_NOTIFY_SUMMARY_MAX:
                page_text_by_url = {
                    (p.get("url") or "").strip(): str(p.get("content") or "")
                    for p in pages
                }
                per_url_summaries = {}
                try:
                    aiserver = _aiserver_url()
                    async with httpx.AsyncClient(timeout=120.0) as ai_client:
                        for u in new_urls:
                            try:
                                per_url_summaries[u] = await _summarize_page_terse_bullets(
                                    ai_client, aiserver, page_text_by_url.get(u, "")
                                )
                            except Exception as sum_exc:
                                print(
                                    f"[webscraper_skill] per-URL notify summary failed for {u}: {sum_exc}",
                                    flush=True,
                                )
                                per_url_summaries[u] = f"(summary failed: {sum_exc})"
                except Exception as exc:
                    print(
                        f"[webscraper_skill] new-URL summary batch failed: {exc}",
                        flush=True,
                    )
                    per_url_summaries = {u: "(summary unavailable)" for u in new_urls}
            _send_new_urls_notification(
                to_email,
                req.url,
                new_urls,
                ns,
                job_id,
                per_url_summaries=per_url_summaries,
            )
        elif new_urls and not to_email:
            print(
                "[webscraper_skill] EMAIL_RECEIVER_DEFAULT not set; skipping new-URL email",
                flush=True,
            )
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = f"Failed to persist pages to storage: {exc}"
        job["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return

    if req.summarize:
        job["status"] = "summarizing"
        try:
            job["summary"] = await _summarize(md)
        except Exception as e:
            print(f"[webscraper_skill] summary generation failed: {e}", flush=True)
            job["summary"] = ""
            job["error"] = f"summary failed: {e}"

    job["status"] = "completed"
    job["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@monitor
@router.post("/scrape")
async def start_scrape(body: ScrapeRequest, response: Response) -> dict[str, Any]:
    """Crawl, write per-page storage records, save markdown, optional AI summary. Body: url; optional max_pages, max_depth, summarize, namespace, patterns."""
    start = time.perf_counter()
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "markdown_path": None,
        "stored": False,
        "url_count": 0,
        "scraped_at": None,
        "namespace": None,
        "sitename": None,
        "existing_url_count": None,
        "new_urls": [],
    }
    _jobs[job_id] = job
    asyncio.create_task(_run_job(job_id, body))
    response.headers["X-Processing-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
    return skill_result(summary=f"Scrape job started for **{body.url}**.", job_id=job_id, status="pending", url=body.url)


@monitor
@router.get("/scrape")
def list_jobs(offset: int = 0, limit: int = 100) -> dict[str, Any]:
    all_jobs = sorted(_jobs.values(), key=lambda j: j.get("created_at", ""), reverse=True)
    page = all_jobs[offset : offset + limit]
    safe = [{k: v for k, v in j.items() if k != "markdown_path"} for j in page]
    total = len(all_jobs)
    items = [{"title": j.get("job_id", ""), "link": j.get("url", ""), "summary": j.get("status", "")} for j in safe]
    return skill_result(summary=f"**{total}** scrape jobs.", items=items, total=total, offset=offset, limit=limit, jobs=safe)


@monitor
@router.get("/scrape/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    out = {k: v for k, v in job.items() if k != "markdown_path"}
    # Job uses "summary" for AI crawl summary; skill_result uses "summary" for the envelope line.
    ai_summary = out.pop("summary", None)
    packed = skill_result(
        summary=f"Job **{job_id}**: {out.get('status', 'unknown')} — {out.get('url', '')}",
        **out,
    )
    packed.setdefault("data", {})["summary"] = ai_summary
    return packed


@monitor
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
    return skill_result(summary=f"Markdown for **{job['url']}**.", text=path.read_text(encoding="utf-8"), job_id=job_id, url=job["url"])


@monitor
@router.get("/scrape/{job_id}/summary")
def get_summary(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "completed":
        raise HTTPException(status_code=400, detail=f"Job not completed (status={job.get('status')})")
    summ = job.get("summary") or ""
    return skill_result(
        summary=f"AI summary for **{job['url']}**.",
        text=summ,
        job_id=job_id,
        url=job["url"],
        top_words=job.get("top_words"),
    )


def _summarize_by_topic_sync(text: str, topic: str) -> str:
    aiserver = _aiserver_url()
    truncated = (text or "")[:15000]
    prompt = (
        f"Summarize ONLY content related to: {topic}. "
        "Focus on products, offerings, brand/positioning phrases, and concrete claims. "
        "Use terse bullet points. If nothing relevant, say so.\n\n"
        f"{truncated}"
    )
    with httpx.Client(timeout=120.0) as client:
        r = client.post(f"{aiserver}/generate", json={"prompt": prompt, "profile": "fast"})
        r.raise_for_status()
        out = r.json().get("output") or {}
        if isinstance(out, dict):
            return str(out.get("text") or "")
        return str(out)


class SummarizeTextRequest(BaseModel):
    text: str = ""
    markdown: str = ""
    topic: str = ""

    @model_validator(mode="after")
    def _resolve_text(self) -> SummarizeTextRequest:
        if not self.text and self.markdown:
            self.text = self.markdown
        return self


@monitor
@router.post("/summarize_text")
def summarize_text(body: SummarizeTextRequest) -> dict[str, Any]:
    text = body.text
    topic = body.topic.strip() or "key themes"
    if not text:
        raise HTTPException(status_code=400, detail="text or markdown is required")
    try:
        summary = _summarize_by_topic_sync(text, topic)
    except Exception as exc:
        print(f"[webscraper_skill] summarize_text failed: {exc}", flush=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return skill_result(summary=f"Summary by topic **{topic}**.", text=summary, topic=topic)


# ── combined text helpers (storage-backed pages) ───────────────────────────

_COMBINED_STOP_WORDS = frozenset(
    "a an and are as at be by for from has he in is it its of on that the to was were will with".split()
)


def _remove_stopwords(text: str) -> str:
    if not (text or "").strip():
        return ""
    words = text.split()
    strip_for_check = ",;!\"'()[]"
    kept = [w for w in words if w.lower().strip(strip_for_check) not in _COMBINED_STOP_WORDS]
    return " ".join(kept).strip()


PAGE_SEP = "\n\n---\n\n"
URL_LINE_PREFIX = "URL: "


def parse_combined_text(combined_text: str) -> list[tuple[str, str]]:
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
                url = seg[len(URL_LINE_PREFIX) : first_newline].strip()
                content = seg[first_newline:].strip()
            else:
                url = seg[len(URL_LINE_PREFIX) :].strip()
                content = ""
            pages.append((_strip_url_query(url), content))
        elif pages:
            last_url, last_content = pages[-1]
            pages[-1] = (last_url, (last_content + "\n\n" + seg).strip())
    return pages


def _format_page_block(url: str, content: str, *, stopwords: bool) -> str:
    body = _remove_stopwords(content) if stopwords else (content or "")
    return f"{URL_LINE_PREFIX}{url}\n\n{body}"


def _fetch_page_value(
    client: httpx.Client, storage_base: str, namespace: str, sitename: str, page_url: str
) -> dict[str, Any] | None:
    sk = _page_storage_key(sitename, page_url)
    r = client.get(_record_url(storage_base, namespace, sk))
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    val = data.get("value")
    return val if isinstance(val, dict) else None


class PageWriteBody(BaseModel):
    namespace: str = Field(default=STORAGE_NAMESPACE)
    sitename: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
    content: str = Field(default="")

    @field_validator("url")
    @classmethod
    def _norm_page_url(cls, v: str) -> str:
        return _strip_url_query(v.strip())

    @field_validator("sitename")
    @classmethod
    def _norm_sitename_body(cls, v: str) -> str:
        return _canonical_sitename(v)


class ParseCombinedRequest(BaseModel):
    combined_text: str = Field(..., description="Single block using URL: lines and --- separators")


@monitor
@router.get("/pages/urls")
def get_pages_urls(
    sitename: str,
    namespace: str = STORAGE_NAMESPACE,
) -> dict[str, Any]:
    if not sitename.strip():
        raise HTTPException(status_code=400, detail="sitename is required")
    ns = (namespace or "").strip() or STORAGE_NAMESPACE
    storage_base = _storage_url()
    with httpx.Client(timeout=60.0) as client:
        all_keys = _storage_list_keys(client, storage_base, ns)
    keys = _keys_for_site(all_keys, sitename)
    urls = _page_urls_from_keys(keys)
    items = [{"title": u, "link": u} for u in urls]
    return skill_result(
        summary=f"**{len(urls)}** URLs in **{ns}** for **{_canonical_sitename(sitename)}**.",
        items=items,
        namespace=ns,
        sitename=_canonical_sitename(sitename),
        urls=urls,
        url_count=len(urls),
    )


@monitor
@router.get("/pages/content")
def get_pages_content(
    sitename: str,
    namespace: str = STORAGE_NAMESPACE,
    url: str | None = None,
    max_chars: int | None = None,
    stopwords: bool = False,
) -> dict[str, Any]:
    if not sitename.strip():
        raise HTTPException(status_code=400, detail="sitename is required")
    ns = (namespace or "").strip() or STORAGE_NAMESPACE
    sn = _canonical_sitename(sitename)
    storage_base = _storage_url()

    with httpx.Client(timeout=120.0) as client:
        if url and url.strip():
            lookup = _strip_url_query(url.strip())
            val = _fetch_page_value(client, storage_base, ns, sn, lookup)
            if val is None:
                raise HTTPException(status_code=404, detail="Page not found for this sitename/namespace")
            pu = str(val.get("url") or lookup)
            content = str(val.get("content") or "")
            block = _format_page_block(pu, content, stopwords=stopwords)
            combined = block
            if max_chars is not None and max_chars > 0:
                combined = combined[:max_chars]
            return skill_result(
                summary=f"Content for **{pu}**.",
                namespace=ns,
                sitename=sn,
                combined_text=combined,
                combined_text_length=len(combined),
                page_url=pu,
            )

        all_keys = _storage_list_keys(client, storage_base, ns)
        site_keys = _keys_for_site(all_keys, sn)
        pairs: list[tuple[str, str]] = []
        for sk in sorted(site_keys):
            try:
                _, pu = _parse_page_storage_key(sk)
            except ValueError:
                continue
            val = _fetch_page_value(client, storage_base, ns, sn, pu)
            if val is None:
                continue
            pairs.append((str(val.get("url") or pu), str(val.get("content") or "")))

        parts: list[str] = []
        total = 0
        for pu, content in pairs:
            block = _format_page_block(pu, content, stopwords=stopwords)
            if max_chars is not None and max_chars > 0:
                if total >= max_chars:
                    break
                chunk = block[: max_chars - total]
                parts.append(chunk)
                total += len(chunk)
            else:
                parts.append(block)
                total += len(block) + len(PAGE_SEP)

        combined = PAGE_SEP.join(parts) if parts else ""
        return skill_result(
            summary=f"**{len(pairs)}** pages for **{sn}** in **{ns}**.",
            namespace=ns,
            sitename=sn,
            combined_text=combined,
            combined_text_length=len(combined),
            url_count=len(pairs),
        )


@monitor
@router.post("/pages")
def post_insert_page(body: PageWriteBody) -> dict[str, Any]:
    ns = (body.namespace or "").strip() or STORAGE_NAMESPACE
    sn = _canonical_sitename(body.sitename)
    pu = body.url.strip()
    storage_base = _storage_url()
    sk = _page_storage_key(sn, pu)
    payload = _build_page_value(ns, sn, pu, body.content)

    with httpx.Client(timeout=60.0) as client:
        gr = client.get(_record_url(storage_base, ns, sk))
        if gr.status_code == 200:
            raise HTTPException(status_code=409, detail="Page already exists; use PUT to update")
        pr = client.put(_record_url(storage_base, ns, sk), json=payload)
        pr.raise_for_status()

    return skill_result(
        summary=f"Inserted **{pu}** under **{sn}** ({ns}).",
        namespace=ns,
        sitename=sn,
        storage_key=sk,
        url=pu,
        contenthash=payload["contenthash"],
    )


@monitor
@router.put("/pages")
def put_update_page(body: PageWriteBody) -> dict[str, Any]:
    ns = (body.namespace or "").strip() or STORAGE_NAMESPACE
    sn = _canonical_sitename(body.sitename)
    pu = body.url.strip()
    storage_base = _storage_url()
    sk = _page_storage_key(sn, pu)
    payload = _build_page_value(ns, sn, pu, body.content)

    with httpx.Client(timeout=60.0) as client:
        gr = client.get(_record_url(storage_base, ns, sk))
        if gr.status_code == 404:
            raise HTTPException(status_code=404, detail="Page not found; use POST to insert")
        pr = client.put(_record_url(storage_base, ns, sk), json=payload)
        pr.raise_for_status()

    return skill_result(
        summary=f"Updated **{pu}** under **{sn}** ({ns}).",
        namespace=ns,
        sitename=sn,
        storage_key=sk,
        url=pu,
        contenthash=payload["contenthash"],
    )


@monitor
@router.delete("/pages")
def delete_page(
    sitename: str,
    url: str,
    namespace: str = STORAGE_NAMESPACE,
) -> dict[str, Any]:
    if not sitename.strip() or not url.strip():
        raise HTTPException(status_code=400, detail="sitename and url are required")
    ns = (namespace or "").strip() or STORAGE_NAMESPACE
    sn = _canonical_sitename(sitename)
    pu = _strip_url_query(url.strip())
    storage_base = _storage_url()

    with httpx.Client(timeout=60.0) as client:
        to_del = _storage_keys_matching_normalized_page_url(
            client, storage_base, ns, sn, pu
        )
        if not to_del:
            raise HTTPException(status_code=404, detail="Page not found")
        deleted = 0
        for k in to_del:
            r = client.delete(_record_url(storage_base, ns, k))
            if r.status_code == 404:
                continue
            r.raise_for_status()
            deleted += 1
        if deleted == 0:
            raise HTTPException(status_code=404, detail="Page not found")

    n = deleted
    summ = f"Deleted **{n}** record(s) for **{pu}** from **{sn}** ({ns})."
    return skill_result(
        summary=summ,
        namespace=ns,
        sitename=sn,
        storage_key=to_del[0],
        url=pu,
        deleted_count=n,
    )


@monitor
@router.get("/sites")
def list_sites(namespace: str = STORAGE_NAMESPACE) -> dict[str, Any]:
    ns = (namespace or "").strip() or STORAGE_NAMESPACE
    storage_base = _storage_url()
    with httpx.Client(timeout=60.0) as client:
        all_keys = _storage_list_keys(client, storage_base, ns)
    sites: set[str] = set()
    for k in all_keys:
        if PAGE_KEY_SEP not in k:
            continue
        try:
            sn, _ = _parse_page_storage_key(k)
            sites.add(sn)
        except ValueError:
            continue
    ordered = sorted(sites)
    items = [{"title": s, "link": s} for s in ordered]
    return skill_result(
        summary=f"**{len(ordered)}** sitenames in **{ns}**.",
        items=items,
        namespace=ns,
        sitenames=ordered,
        count=len(ordered),
    )


@monitor
@router.post("/parse_combined")
def post_parse_combined(body: ParseCombinedRequest) -> dict[str, Any]:
    pages = parse_combined_text(body.combined_text or "")
    items = [{"title": u, "link": u, "summary": (c or "")[:200]} for u, c in pages]
    n = len(pages)
    return skill_result(summary=f"**{n}** pages parsed.", items=items, pages=[{"url": u, "content": c} for u, c in pages], count=n)


class LegacyStoredRequest(BaseModel):
    base_url: str = Field(..., min_length=1)
    namespace: str = Field(default=STORAGE_NAMESPACE)
    max_chars: int | None = Field(default=None, ge=1, le=2_000_000)

    @field_validator("base_url")
    @classmethod
    def _norm_stored_base_url(cls, v: str) -> str:
        return _canonical_sitename(v.strip())


@monitor
@router.post("/stored")
def post_stored_legacy(body: LegacyStoredRequest) -> dict[str, Any]:
    ns = (body.namespace or "").strip() or STORAGE_NAMESPACE
    sn = _canonical_sitename(body.base_url)
    storage_base = _storage_url()
    with httpx.Client(timeout=120.0) as client:
        all_keys = _storage_list_keys(client, storage_base, ns)
        site_keys = _keys_for_site(all_keys, sn)
        content_by_url: dict[str, str] = {}
        for sk in site_keys:
            try:
                _, pu = _parse_page_storage_key(sk)
            except ValueError:
                continue
            val = _fetch_page_value(client, storage_base, ns, sn, pu)
            if val is None:
                continue
            u = str(val.get("url") or pu)
            content_by_url[u] = str(val.get("content") or "")
    urls = sorted(content_by_url.keys())
    if not urls:
        raise HTTPException(status_code=404, detail="No stored pages for this sitename/namespace")
    scraped_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    value: dict[str, Any] = {
        "base_url": sn,
        "scraped_at": scraped_at,
        "urls": urls,
        "content_by_url": content_by_url,
    }
    out = skill_result(
        summary=f"Stored scrape for **{sn}**: **{len(urls)}** URLs ({ns}).",
        namespace=ns,
        key=sn,
        value=value,
    )
    if body.max_chars is not None:
        data = out.get("data") or {}
        combined_parts: list[str] = []
        total = 0
        for u in urls:
            block = _format_page_block(u, content_by_url.get(u) or "", stopwords=True)
            if total >= body.max_chars:
                break
            chunk = block[: body.max_chars - total]
            combined_parts.append(chunk)
            total += len(chunk)
        data["combined_text"] = PAGE_SEP.join(combined_parts)
        data["combined_text_length"] = len(data["combined_text"])
        data["url_count"] = len(urls)
        out["data"] = data
    return out


@monitor
@router.get("/stored")
def get_stored_legacy(
    base_url: str,
    namespace: str = STORAGE_NAMESPACE,
    max_chars: int | None = None,
) -> dict[str, Any]:
    if not base_url.strip():
        raise HTTPException(status_code=400, detail="base_url is required")
    return post_stored_legacy(
        LegacyStoredRequest(base_url=base_url.strip(), namespace=namespace, max_chars=max_chars)
    )


def get_router() -> APIRouter:
    return router
