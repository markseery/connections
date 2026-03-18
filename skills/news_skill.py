"""
License: MIT
Description: News skill — combines yfinance ticker news with Perplexity web search
results to provide comprehensive, up-to-date news for any topic or stock symbol.
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from common.skill_response import skill_result

router = APIRouter()


class SearchRequest(BaseModel):
    """Body for POST /search.  Accepts ``query``, ``q``, or ``topic`` as the
    search term (first non-empty wins).  ``symbol`` and ``limit`` are optional."""

    query: str = ""
    q: str = ""
    topic: str = ""
    symbol: str = ""
    limit: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def _resolve_query(self) -> "SearchRequest":
        resolved = self.query or self.q or self.topic
        if isinstance(resolved, list):
            resolved = " ".join(str(x) for x in resolved)
        self.query = str(resolved).strip()
        return self


# ── Routes ──────────────────────────────────────────────────────────────


@router.post("/search")
def news_search(body: SearchRequest) -> dict[str, Any]:
    """Search for news on a topic or stock. Body: query or q or topic (required), symbol (optional), limit (optional). Use for news, headlines, or stock news."""
    query = body.query
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    symbol = body.symbol.strip().upper() or _detect_symbol(query)
    per_source = body.limit

    yf_articles: list[dict[str, Any]] = []
    web_articles: list[dict[str, Any]] = []

    if symbol:
        yf_articles = _yfinance_news(symbol, per_source)

    web_articles = _web_search_news(query, per_source)

    merged = _merge_and_dedupe(yf_articles, web_articles)

    yf_in_merged = sum(1 for a in merged if a.get("source") == "yfinance")
    web_in_merged = sum(1 for a in merged if a.get("source") == "web")
    print(
        f"[news_skill] query={query!r} symbol={symbol!r} "
        f"yf_fetched={len(yf_articles)} web_fetched={len(web_articles)} "
        f"merged={len(merged)} (yf={yf_in_merged} web={web_in_merged})",
        flush=True,
    )

    summary = _summarize_articles(query, merged)

    return skill_result(
        summary=summary,
        items=merged,
        query=query,
        symbol=symbol or None,
        sources={
            "yfinance": len(yf_articles),
            "web_search": len(web_articles),
            "in_results": {"yfinance": yf_in_merged, "web": web_in_merged},
        },
        count=len(merged),
    )


@router.get("/topic/{topic}")
def news_by_topic(topic: str, limit: int = 10, prompt: str = "") -> dict[str, Any]:
    """News for a topic. Replace {topic} with the topic. Query: limit, prompt. Use when user asks for news on a topic."""
    real_query = prompt.strip() or topic.strip()
    return news_search(SearchRequest(query=real_query, limit=min(limit, 20)))


@router.get("/stock/{symbol}")
def news_by_stock(symbol: str, limit: int = 10, prompt: str = "") -> dict[str, Any]:
    """News for a stock symbol. Replace {symbol} with ticker (e.g. AAPL). Query: limit, prompt. Use when user asks for stock or company news."""
    sym = symbol.strip().upper()
    real_query = prompt.strip() or f"latest news for {sym}"
    return news_search(SearchRequest(query=real_query, symbol=sym, limit=min(limit, 20)))


# ── yfinance news source ───────────────────────────────────────────────


def _yfinance_news(symbol: str, limit: int) -> list[dict[str, Any]]:
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        items = t.news or []
    except Exception as exc:
        print(f"[news_skill] yfinance fetch for {symbol} failed: {exc}", flush=True)
        return []

    articles: list[dict[str, Any]] = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        parsed = _parse_yf_item(it)
        if parsed:
            articles.append(parsed)
    return articles


def _parse_yf_item(item: dict[str, Any]) -> dict[str, Any] | None:
    content = item.get("content") or item
    if not isinstance(content, dict):
        return None

    title = content.get("title") or item.get("title") or ""

    publisher = ""
    prov = content.get("provider") or content.get("publisher") or item.get("publisher")
    if isinstance(prov, dict):
        publisher = prov.get("displayName") or prov.get("name") or ""
    elif isinstance(prov, str):
        publisher = prov

    link = ""
    url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl")
    if isinstance(url_obj, dict):
        link = url_obj.get("url") or ""
    elif isinstance(url_obj, str):
        link = url_obj
    if not link:
        link = content.get("link") or item.get("link") or ""

    pub_date = (
        content.get("pubDate")
        or content.get("providerPublishTime")
        or item.get("providerPublishTime")
        or ""
    )

    summary = content.get("summary") or ""

    if not title and not link:
        return None
    out: dict[str, Any] = {"title": title, "source": "yfinance"}
    if publisher:
        out["publisher"] = publisher
    if link:
        out["link"] = link
    if pub_date:
        out["published"] = _safe_value(pub_date)
    if summary:
        out["summary"] = summary[:500]
    return out


# ── Web search news source (via aiserver → perplexity) ─────────────────


def _web_search_news(query: str, limit: int) -> list[dict[str, Any]]:
    aiserver_url = _get_aiserver_url()
    if not aiserver_url:
        print("[news_skill] web_search SKIP: no aiserver url", flush=True)
        return []
    search_payload = {"prompt": query, "profile": "search"}
    print(f"[news_skill] web_search prompt → {search_payload}", flush=True)
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{aiserver_url}/generate",
                json=search_payload,
            )
            if r.status_code != 200:
                print(f"[news_skill] web_search FAIL: status {r.status_code}", flush=True)
                return []
            data = r.json()
    except Exception as exc:
        print(f"[news_skill] web_search ERROR: {exc}", flush=True)
        return []

    output = data.get("output") or {}
    results = output.get("results") or []
    print(
        f"[news_skill] web_search raw: {len(results)} results from perplexity",
        flush=True,
    )
    articles: list[dict[str, Any]] = []
    for item in results[:limit]:
        if not isinstance(item, dict):
            continue
        title = _clean_snippet(item.get("title", ""))
        if not title:
            continue
        article: dict[str, Any] = {"title": title, "source": "web"}
        url = item.get("url", "")
        if url:
            article["link"] = url
            article["publisher"] = _domain_label(url)
        if item.get("date"):
            article["published"] = item["date"]
        if item.get("snippet"):
            article["summary"] = _clean_snippet(item["snippet"])[:500]
        articles.append(article)
    print(
        f"[news_skill] web_search parsed: {len(articles)} articles with titles",
        flush=True,
    )
    return articles


# ── Merge & dedupe ─────────────────────────────────────────────────────


def _merge_and_dedupe(
    yf_articles: list[dict[str, Any]],
    web_articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Round-robin interleave yfinance and web results, deduplicating by
    title similarity so both sources are represented in the final list."""
    seen_titles: set[str] = set()
    merged: list[dict[str, Any]] = []

    def _add(article: dict[str, Any]) -> bool:
        title_key = article.get("title", "").lower().strip()[:80]
        if not title_key or title_key in seen_titles:
            return False
        seen_titles.add(title_key)
        merged.append(article)
        return True

    yi, wi = 0, 0
    while yi < len(yf_articles) or wi < len(web_articles):
        if yi < len(yf_articles):
            _add(yf_articles[yi])
            yi += 1
        if wi < len(web_articles):
            _add(web_articles[wi])
            wi += 1
    return merged


# ── AI summary ─────────────────────────────────────────────────────────


def _summarize_articles(query: str, articles: list[dict[str, Any]]) -> str:
    """Ask the AI server to produce a terse bullet-point summary of the
    merged news articles, scoped to the original user prompt."""
    if not articles:
        return ""
    aiserver_url = _get_aiserver_url()
    if not aiserver_url:
        return ""

    headlines = []
    for a in articles:
        line = a.get("title", "")
        if a.get("summary"):
            line += f" — {a['summary'][:200]}"
        if line:
            headlines.append(line)

    prompt = (
        f"The user asked: \"{query}\"\n\n"
        f"Below are {len(headlines)} news headlines and snippets from multiple sources.\n"
        "Summarize the key themes and takeaways as succinct, terse bullet points. "
        "Focus on what matters most to someone interested in this topic. "
        "Do NOT repeat every headline — distill into the most important points. "
        "Respond ONLY with bullet points, no preamble.\n\n"
        + "\n".join(f"- {h}" for h in headlines)
    )

    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{aiserver_url}/generate",
                json={"prompt": prompt, "profile": "fast"},
            )
            if r.status_code != 200:
                print(f"[news_skill] summary FAIL: status {r.status_code}", flush=True)
                return ""
            data = r.json()
    except Exception as exc:
        print(f"[news_skill] summary ERROR: {exc}", flush=True)
        return ""

    text = (data.get("output") or {}).get("text", "").strip()
    print(f"[news_skill] summary OK: {len(text)} chars", flush=True)
    return text


# ── Helpers ─────────────────────────────────────────────────────────────


def _detect_symbol(query: str) -> str:
    """Extract a stock ticker from the query and validate it against yfinance.

    1. Pull all uppercase 1–5 letter words from the query.
    2. Also consider capitalised words that *look* like tickers (e.g. lowercase
       input "crwv" won't match the regex, so we upper-case every short word
       and check it too).
    3. Filter out common English words.
    4. Validate each candidate with yfinance — only return it if the ticker
       has real market data (``shortName`` or ``regularMarketPrice``).
    """
    import re

    _SKIP = {
        "THE", "FOR", "AND", "NEWS", "GET", "SHOW", "LATEST", "RECENT",
        "STOCK", "ABOUT", "WHAT", "IS", "ARE", "HAS", "HOW", "WHY",
        "ALL", "ANY", "BUT", "CAN", "DID", "DO", "HIS", "HER", "ITS",
        "MAY", "NEW", "NOT", "NOW", "OLD", "OUR", "OUT", "OWN", "SAY",
        "SHE", "TOO", "USE", "WAS", "WAY", "WHO", "BOY", "DID", "MAN",
        "HIM", "LET", "PUT", "RUN", "TOP", "SET", "TRY", "ASK", "BIG",
    }

    words = re.findall(r'\b([A-Za-z]{1,5})\b', query)
    candidates: list[str] = []
    seen: set[str] = set()
    for w in words:
        upper = w.upper()
        if upper in _SKIP or upper in seen or len(upper) < 1:
            continue
        seen.add(upper)
        candidates.append(upper)

    for candidate in candidates:
        if _validate_ticker(candidate):
            return candidate
    return ""


def _validate_ticker(symbol: str) -> bool:
    """Return True if yfinance recognises this as a real traded symbol."""
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
        return bool(
            info.get("shortName")
            or info.get("longName")
            or info.get("regularMarketPrice")
        )
    except Exception as exc:
        print(f"[news_skill] ticker validation for {symbol} failed: {exc}", flush=True)
        return False


def _clean_snippet(text: str) -> str:
    """Strip markdown artifacts and table markup from web search snippets."""
    import re
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'^\|.*\|$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-|:\s]+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{2,}', '\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def _domain_label(url: str) -> str:
    """Extract a human-readable publisher name from a URL domain."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        host = host.lower().removeprefix("www.")
        parts = host.rsplit(".", 1)
        return parts[0].replace("-", " ").title() if parts else host
    except Exception as exc:
        print(f"[news_skill] domain label parse failed for {url}: {exc}", flush=True)
        return ""


def _get_aiserver_url() -> str | None:
    registry = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").strip().rstrip("/")
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.get(f"{registry}/servers/aiserver")
            if r.status_code != 200:
                return None
            return (r.json() or {}).get("url", "").rstrip("/") or None
    except Exception as exc:
        print(f"[news_skill] aiserver lookup failed: {exc}", flush=True)
        return None


def _safe_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (int, bool, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def get_router() -> APIRouter:
    return router
