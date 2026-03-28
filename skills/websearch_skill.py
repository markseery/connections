"""
License: MIT
Description: Web search skill -- searches the web using Google Custom Search
(free tier: 100 queries/day) with DuckDuckGo HTML fallback, extracts page
content from top results, and returns an AI-generated summary with citations.

Input: POST /search with body {"query": "...", "limit": 5}
Output: skill_result with summary (markdown + citations) and items list.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.compound.skill_config import SkillConfig
from common.simple.skill_response import skill_result

router = APIRouter()
_conf = SkillConfig("websearch_skill")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class SearchRequest(BaseModel):
    query: str = ""
    q: str = ""
    limit: int = Field(default=5, ge=1, le=10, description="Number of results to fetch and summarize")

    def resolved_query(self) -> str:
        return (self.query or self.q).strip()


# -- Routes ----------------------------------------------------------------


@router.post("/search")
def search(body: SearchRequest) -> dict[str, Any]:
    """Search the web, extract content from results, and return an AI summary with citations. Body: query (required), limit (optional, default 5)."""
    query = body.resolved_query()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    limit = body.limit

    try:
        raw_results = _google_search(query, limit)
        source = "google"
        if not raw_results:
            raw_results = _ddg_search(query, limit)
            source = "duckduckgo"
    except Exception as exc:
        print(f"[websearch] search phase failed: {exc}", flush=True)
        raise HTTPException(status_code=502, detail=f"Search failed: {exc}")

    if not raw_results:
        return skill_result(summary="No search results found.", query=query, count=0)

    print(f"[websearch] {source}: {len(raw_results)} results for {query!r}", flush=True)

    try:
        extracted = _extract_all(raw_results)
    except Exception as exc:
        print(f"[websearch] extraction phase failed: {exc}", flush=True)
        extracted = [
            {"title": r.get("title", ""), "url": r["url"],
             "content": r.get("snippet", ""), "excerpt": r.get("snippet", "")}
            for r in raw_results if r.get("snippet")
        ]

    print(f"[websearch] extracted content from {len(extracted)} pages", flush=True)

    if not extracted:
        return skill_result(summary="Search returned results but content extraction failed.",
                            query=query, source=source, count=0)

    try:
        summary = _summarize(query, extracted)
    except Exception as exc:
        print(f"[websearch] summarization failed: {exc}", flush=True)
        summary = _plain_summary(extracted)

    citations = _format_citations(extracted)

    full_summary = summary
    if citations:
        full_summary += "\n\n### Sources\n\n" + citations

    items = [
        {
            "title": e["title"],
            "link": e["url"],
            "summary": e["excerpt"][:200] if e.get("excerpt") else "",
        }
        for e in extracted
    ]

    return skill_result(
        summary=full_summary,
        items=items,
        query=query,
        source=source,
        count=len(extracted),
    )


# -- Google Custom Search --------------------------------------------------


def _google_search(query: str, limit: int) -> list[dict[str, str]]:
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    cse_id = os.environ.get("GOOGLE_CSE_ID", "").strip()
    if not api_key or not cse_id:
        print("[websearch] google SKIP: GOOGLE_API_KEY or GOOGLE_CSE_ID not set", flush=True)
        return []

    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": api_key,
        "cx": cse_id,
        "q": query,
        "num": min(limit, 10),
    }
    try:
        with httpx.Client(timeout=_conf.get("search_timeout", 15.0)) as client:
            r = client.get(url, params=params)
            if r.status_code != 200:
                print(f"[websearch] google FAIL: {r.status_code} {r.text[:200]}", flush=True)
                return []
            data = r.json()
    except Exception as exc:
        print(f"[websearch] google ERROR: {exc}", flush=True)
        return []

    results: list[dict[str, str]] = []
    for item in data.get("items", [])[:limit]:
        title = item.get("title", "")
        link = item.get("link", "")
        snippet = item.get("snippet", "")
        if link:
            results.append({"title": title, "url": link, "snippet": snippet})
    return results


# -- DuckDuckGo HTML fallback ----------------------------------------------


def _ddg_search(query: str, limit: int) -> list[dict[str, str]]:
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    headers = {"User-Agent": _USER_AGENT}
    try:
        with httpx.Client(
            timeout=_conf.get("search_timeout", 15.0),
            follow_redirects=True,
        ) as client:
            r = client.get(url, headers=headers)
            if r.status_code != 200:
                print(f"[websearch] ddg FAIL: {r.status_code}", flush=True)
                return []
    except Exception as exc:
        print(f"[websearch] ddg ERROR: {exc}", flush=True)
        return []

    try:
        soup = BeautifulSoup(r.text, "lxml")
    except Exception:
        soup = BeautifulSoup(r.text, "html.parser")

    results: list[dict[str, str]] = []

    for result_div in soup.select(".result__body")[:limit]:
        try:
            title_el = result_div.select_one(".result__a")
            snippet_el = result_div.select_one(".result__snippet")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if isinstance(href, list):
                href = href[0] if href else ""
            link = _resolve_ddg_url(str(href))
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if link:
                results.append({"title": title, "url": link, "snippet": snippet})
        except Exception:
            continue

    return results


def _resolve_ddg_url(href: str) -> str:
    if "duckduckgo.com/l/" in href or "uddg=" in href:
        from urllib.parse import parse_qs, urlparse as _urlparse
        parsed = _urlparse(href)
        qs = parse_qs(parsed.query)
        real = qs.get("uddg", [""])[0]
        if real:
            return real
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://duckduckgo.com" + href
    return href


# -- Content extraction ----------------------------------------------------


def _extract_all(results: list[dict[str, str]]) -> list[dict[str, str]]:
    extracted: list[dict[str, str]] = []
    max_chars = _conf.get("max_page_chars", 15000)
    for r in results:
        url = r["url"]
        title = r.get("title", "")
        try:
            content = _fetch_page_text(url, max_chars)
        except Exception as exc:
            print(f"[websearch] fetch failed for {url}: {exc}", flush=True)
            content = ""
        if content:
            extracted.append({
                "title": title,
                "url": url,
                "content": content,
                "excerpt": content[:500],
            })
        else:
            snippet = r.get("snippet", "")
            if snippet:
                extracted.append({
                    "title": title,
                    "url": url,
                    "content": snippet,
                    "excerpt": snippet,
                })
    return extracted


def _fetch_page_text(url: str, max_chars: int) -> str:
    headers = {"User-Agent": _USER_AGENT}
    try:
        with httpx.Client(
            timeout=_conf.get("fetch_timeout", 10.0),
            follow_redirects=True,
        ) as client:
            r = client.get(url, headers=headers)
            if r.status_code != 200:
                return ""
            content_type = r.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return ""
    except Exception:
        return ""

    try:
        soup = BeautifulSoup(r.text, "lxml")
    except Exception:
        soup = BeautifulSoup(r.text, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                      "noscript", "iframe", "svg", "form"]):
        tag.decompose()

    article = soup.find("article") or soup.find("main") or soup.find("body")
    if not article:
        return ""

    text = article.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)

    if len(text) > max_chars:
        text = text[:max_chars]

    return text.strip()


# -- AI summarization ------------------------------------------------------


def _summarize(query: str, extracted: list[dict[str, str]]) -> str:
    if not extracted:
        return ""

    aiserver_url = _get_aiserver_url()
    if not aiserver_url:
        return _plain_summary(extracted)

    max_content = _conf.get("summary_content_chars", 8000)
    content_block = ""
    for i, e in enumerate(extracted, 1):
        title = e["title"]
        url = e["url"]
        truncated = e["content"][:max_content]
        content_block += f"\n--- Source {i}: {title} ({url}) ---\n{truncated}\n"

    prompt = (
        f'The user searched for: "{query}"\n\n'
        f"Below is extracted content from {len(extracted)} web pages.\n\n"
        "Produce a comprehensive summary that answers the user's query. "
        "Use the information from all sources. Write in clear prose with "
        "bullet points where appropriate. "
        "At the end, list each source as a numbered citation in the format:\n"
        "[N] Title - URL\n\n"
        "Reference citations in the summary text as [N] where relevant.\n\n"
        + content_block
    )

    try:
        with httpx.Client(timeout=_conf.get("ai_timeout", 60.0)) as client:
            r = client.post(
                aiserver_url + "/generate",
                json={"prompt": prompt, "profile": "fast"},
            )
            if r.status_code != 200:
                print(f"[websearch] summary AI FAIL: {r.status_code}", flush=True)
                return _plain_summary(extracted)
            data = r.json()
    except Exception as exc:
        print(f"[websearch] summary AI ERROR: {exc}", flush=True)
        return _plain_summary(extracted)

    output = data.get("output") or {}
    text = output.get("text", "") if isinstance(output, dict) else str(output)
    print(f"[websearch] summary: {len(text)} chars", flush=True)
    return text.strip()


def _plain_summary(extracted: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for e in extracted:
        title = e["title"]
        excerpt = e["excerpt"]
        parts.append(f"**{title}**\n{excerpt}")
    return "\n\n".join(parts)


def _format_citations(extracted: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for i, e in enumerate(extracted, 1):
        title = e.get("title", "Untitled")
        url = e.get("url", "")
        domain = _domain_label(url)
        label = title + " (" + domain + ")" if domain else title
        lines.append(f"{i}. [{label}]({url})")
    return "\n".join(lines)


# -- Helpers ---------------------------------------------------------------


def _get_aiserver_url() -> str | None:
    registry = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").strip().rstrip("/")
    try:
        with httpx.Client(timeout=_conf.get("registry_timeout", 2.0)) as client:
            r = client.get(registry + "/servers/aiserver")
            if r.status_code != 200:
                return None
            return (r.json() or {}).get("url", "").rstrip("/") or None
    except Exception as exc:
        print(f"[websearch] aiserver lookup failed: {exc}", flush=True)
        return None


def _domain_label(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        host = host.lower().removeprefix("www.")
        parts = host.rsplit(".", 1)
        return parts[0].replace("-", " ").title() if parts else host
    except Exception:
        return ""


def get_router() -> APIRouter:
    return router
