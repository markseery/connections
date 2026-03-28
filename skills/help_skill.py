"""
License: MIT
Description: Help skill — lists available skills, provides example prompts, and
describes system capabilities. Dynamically discovers skills from the
configuration server so the listing is always up to date.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from common.simple.skill_response import skill_result
from common.compound.skill_config import SkillConfig


router = APIRouter()
_conf = SkillConfig("help_skill")


# ── Skill catalog ───────────────────────────────────────────────────────
# Static metadata for known skills: description, example prompts, and
# accepted argument hints.  If a skill is configured but not listed here
# it still appears in the output with its route descriptions from config.

SKILL_CATALOG: dict[str, dict[str, Any]] = {
    "statistics": {
        "description": "Compute basic statistics on a list of numbers.",
        "examples": [
            "what is the mean of 2, 5, 7, 23",
            "calculate the median of 10, 20, 30, 40, 50",
            "standard deviation of 3, 7, 11, 15",
        ],
        "routes": {
            "POST /skills/statistics/mean": "Compute the arithmetic mean.",
            "POST /skills/statistics/median": "Compute the median.",
            "POST /skills/statistics/stddev": "Compute the population standard deviation.",
        },
    },
    "math_skill": {
        "description": "Arithmetic and math: sum, multiply, divide, subtract, power, root, modulo, log, factorial, percentage.",
        "examples": [
            "sum of 10, 20, 30",
            "total cost of 1 share each: add 82.12, 116.33, 42.96",
            "what is 20% of 80",
            "15 is what percent of 60",
            "factorial of 5",
            "square root of 144",
            "2 to the power of 10",
        ],
        "routes": {
            "POST /skills/math_skill/sum": "Sum a list of numbers (body: values or numbers).",
            "POST /skills/math_skill/add": "Same as sum.",
            "POST /skills/math_skill/multiply": "Product of numbers (body: values or numbers).",
            "POST /skills/math_skill/divide": "Divide a by b (body: a, b).",
            "POST /skills/math_skill/subtract": "Subtract b from a (body: a, b).",
            "POST /skills/math_skill/power": "Exponentiation base^exponent (body: base, exponent).",
            "POST /skills/math_skill/root": "Nth root (body: value, n; n=2 for square root).",
            "POST /skills/math_skill/sqrt": "Square root (body: value).",
            "POST /skills/math_skill/modulo": "a mod b (body: a, b).",
            "POST /skills/math_skill/log": "Logarithm (body: value, base optional default 10).",
            "POST /skills/math_skill/ln": "Natural log (body: value).",
            "POST /skills/math_skill/factorial": "Factorial n! (body: n, non-negative int).",
            "POST /skills/math_skill/percent_of": "percent% of value (body: percent, value).",
            "POST /skills/math_skill/what_percent": "x is what % of of_y (body: x, of_y).",
        },
    },
    "notification_skill": {
        "description": "Send emails and review notification history.",
        "examples": [
            "send email to:user@example.com subject:hello body:'Hi there!'",
            "list my recent notifications",
            "show email configuration",
        ],
        "routes": {
            "POST /skills/notification_skill/send": "Send an email.",
            "GET /skills/notification_skill/notifications": "List past notifications.",
            "GET /skills/notification_skill/stats": "Notification statistics.",
            "GET /skills/notification_skill/config": "View email config (masked).",
        },
    },
    "stock_skill": {
        "description": "Look up stock quotes, fundamentals, and earnings.",
        "examples": [
            "get a quote for AAPL",
            "show fundamentals for TSLA",
            "earnings for MSFT",
        ],
        "routes": {
            "GET /skills/stock_skill/quote/{symbol}": "Real-time quote.",
            "GET /skills/stock_skill/fundamentals/{symbol}": "Key fundamentals.",
            "GET /skills/stock_skill/earnings/{symbol}": "Earnings data.",
        },
    },
    "news_skill": {
        "description": "Search for news on any topic or stock. Combines yfinance ticker news with web search results.",
        "examples": [
            "latest news for CRWV",
            "news about artificial intelligence",
            "what's happening with TSLA stock",
        ],
        "routes": {
            "POST /skills/news_skill/search": "Search news by topic/symbol (body: query, symbol, limit).",
            "GET /skills/news_skill/topic/{topic}": "News for a topic.",
            "GET /skills/news_skill/stock/{symbol}": "News for a stock ticker.",
        },
    },
    "rss_skill": {
        "description": "Fetch an RSS or Atom feed URL and return normalized JSON (same structure for any feed).",
        "examples": [
            "get RSS feed from https://example.com/feed.xml",
            "parse Atom feed https://blog.example.com/atom.xml",
        ],
        "routes": {
            "GET /skills/rss_skill/feed": "Fetch feed (query param: url).",
            "POST /skills/rss_skill/feed": "Fetch feed (body: { \"url\": \"...\" }).",
        },
    },
    "rss_new_skill": {
        "description": "RSS new-item fetcher: load a feed list from data/lists, fetch via rss_skill, diff against storage (rss_notified). For each item fetches the article URL and adds a sanitized content field (HTML/JS removed). Returns new items only; no email or persist.",
        "examples": [
            "get new RSS items for list ai-news",
            "get new RSS items for ai-news dry run",
        ],
        "routes": {
            "POST /skills/rss_new_skill/run": "Return new items with content (body: list_name, dry_run?, worker_url?). Response: new_items (feed_title, title, link, published, content), new_item_ids, counts.",
        },
    },
    "rss_new_and_save_skill": {
        "description": "RSS new items + storage update: calls rss_new_skill then persists new_item_ids to storage (rss_notified). Single place for storage update logic; no notification.",
        "examples": [
            "get new RSS items for ai-news and save to storage",
            "rss new and save for list general-news dry run",
        ],
        "routes": {
            "POST /skills/rss_new_and_save_skill/run": "Get new items and persist (body: list_name, dry_run?, worker_url?). Response: same as rss_new_skill plus persisted_count, persist_errors?.",
        },
    },
    "webscraper_skill": {
        "description": "Single crawl implementation: per-page storage (webscrape namespace), job markdown + optional AI summary, and page CRUD.",
        "examples": [
            "scrape https://example.com",
            "list scraping jobs",
            "list stored URLs for https://example.com",
            "get combined stored content for a site",
            "insert or update one page via POST or PUT /pages",
        ],
        "routes": {
            "POST /skills/webscraper_skill/scrape": "Crawl; persist each page to storage; save markdown; optional summarize (body: url, summarize?, namespace?, max_pages, max_depth).",
            "GET /skills/webscraper_skill/scrape": "List scrape jobs.",
            "GET /skills/webscraper_skill/scrape/{job_id}": "Job status.",
            "GET /skills/webscraper_skill/scrape/{job_id}/markdown": "Job markdown file.",
            "GET /skills/webscraper_skill/scrape/{job_id}/summary": "Job AI summary.",
            "GET /skills/webscraper_skill/pages/urls": "List page URLs for namespace + sitename.",
            "GET /skills/webscraper_skill/pages/content": "Content with URL: lines; optional url for one page; max_chars, stopwords.",
            "POST /skills/webscraper_skill/pages": "Insert page record.",
            "PUT /skills/webscraper_skill/pages": "Update page record.",
            "DELETE /skills/webscraper_skill/pages": "Delete page (query: namespace, sitename, url).",
            "GET /skills/webscraper_skill/sites": "List sitenames in namespace.",
            "GET/POST /skills/webscraper_skill/stored": "Aggregate urls + content_by_url for base_url (StoredSiteContent).",
            "POST /skills/webscraper_skill/parse_combined": "Parse combined_text to pages.",
        },
    },
    "workflow_skill": {
        "description": "Save and run reusable multi-step workflow templates with parameters.",
        "examples": [
            "run workflow scrape_and_summarize with url https://example.com",
            "run workflow webscrape_and_extract with url https://example.com topic products",
            "list workflow templates",
            "show workflow executions",
        ],
        "routes": {
            "POST /skills/workflow_skill/templates": "Save a new workflow template.",
            "GET /skills/workflow_skill/templates": "List all saved templates.",
            "GET /skills/workflow_skill/templates/{name}": "Get a template by name.",
            "DELETE /skills/workflow_skill/templates/{name}": "Delete a template.",
            "POST /skills/workflow_skill/run/{name}": "Run a saved template with parameters.",
            "POST /skills/workflow_skill/execute": "Execute an ad-hoc workflow plan.",
            "GET /skills/workflow_skill/executions": "List all workflow executions.",
            "GET /skills/workflow_skill/executions/{workflow_id}": "Get execution details.",
        },
    },
    "text_skill": {
        "description": "Text manipulation and AI-powered text processing (summarize, translate, find, replace, case conversion, extract, rewrite).",
        "examples": [
            "summarize this article",
            "translate this text to Spanish",
            "find all email addresses in this text",
            "replace foo with bar in this text",
            "convert this to camelCase",
            "rewrite this in a professional tone",
            "extract all URLs from this text",
            "count words in this paragraph",
        ],
        "routes": {
            "POST /skills/text_skill/summarize": "Summarize text with optional topic focus and style (bullets, paragraph, oneliner).",
            "POST /skills/text_skill/translate": "Translate text to a target language with tone control.",
            "POST /skills/text_skill/find": "Find all occurrences of a string or regex.",
            "POST /skills/text_skill/replace": "Replace string or regex matches.",
            "POST /skills/text_skill/extract": "Extract emails, URLs, dates, numbers, names, or custom entities.",
            "POST /skills/text_skill/rewrite": "Rewrite text in a different tone/style.",
            "POST /skills/text_skill/touppercase": "Convert to UPPERCASE.",
            "POST /skills/text_skill/tolowercase": "Convert to lowercase.",
            "POST /skills/text_skill/tocamelcase": "Convert to camelCase.",
            "POST /skills/text_skill/totitlecase": "Convert to Title Case.",
            "POST /skills/text_skill/tosnakecase": "Convert to snake_case.",
            "POST /skills/text_skill/tokebabcase": "Convert to kebab-case.",
            "POST /skills/text_skill/reverse": "Reverse text.",
            "POST /skills/text_skill/wordcount": "Count words, chars, lines, sentences.",
            "POST /skills/text_skill/truncate": "Truncate to max length.",
            "POST /skills/text_skill/wrap": "Word-wrap to a column width.",
            "POST /skills/text_skill/slugify": "Convert to URL slug.",
            "POST /skills/text_skill/split": "Split text by delimiter.",
        },
    },
    "help_skill": {
        "description": "This skill! Lists available skills and provides usage examples.",
        "examples": [
            "help",
            "what skills are available",
            "show me example prompts",
        ],
        "routes": {
            "GET /skills/help_skill/skills": "List all available skills with examples.",
            "GET /skills/help_skill/skills/{skill_name}": "Detail for one skill.",
            "GET /skills/help_skill/examples": "Example prompts for every skill.",
            "GET /skills/help_skill/about": "About the system.",
        },
    },
}


# ── Routes ──────────────────────────────────────────────────────────────


@router.get("/skills")
def list_skills() -> dict[str, Any]:
    """List all available skills with descriptions and examples. Use when user asks what skills or capabilities exist."""
    discovered = _discover_skill_names()
    skills: list[dict[str, Any]] = []
    for name in sorted(set(list(SKILL_CATALOG.keys()) + discovered)):
        entry = _skill_detail(name)
        skills.append(entry)
    count = len(skills)
    items = [{"title": s.get("skill_name", ""), "summary": s.get("description", "")} for s in skills]
    return skill_result(summary=f"**{count}** skills available.", items=items, skills=skills, count=count)


@router.get("/skills/{skill_name}")
def skill_detail(skill_name: str) -> dict[str, Any]:
    """Detail for one skill: description, routes, examples. Replace {skill_name} with skill name. Use when user asks how a skill works."""
    name = skill_name.strip().lower()
    discovered = _discover_skill_names()
    if name not in SKILL_CATALOG and name not in discovered:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    detail = _skill_detail(name)
    desc = detail.get("description", "No description available.")
    return skill_result(summary=f"Skill: **{name}**", text=desc, **detail)


@router.get("/examples")
def examples() -> dict[str, Any]:
    """Example prompts per skill. Use when user asks for examples or what they can ask."""
    discovered = _discover_skill_names()
    result: dict[str, list[str]] = {}
    for name in sorted(set(list(SKILL_CATALOG.keys()) + discovered)):
        catalog = SKILL_CATALOG.get(name, {})
        result[name] = catalog.get("examples", [])
    return skill_result(summary="Example prompts by skill.", examples=result)


@router.get("/about")
def about() -> dict[str, Any]:
    """System description and how to interact. Use when user asks what this is or how it works."""
    description = (
        "Connections is a microservice platform with an AI-powered chat agent. "
        "When you send a message, the chat agent decides whether to use a "
        "registered skill (like statistics or email) or answer directly via the "
        "AI model. Skills are dynamically loaded into worker servers and can be "
        "extended by adding new modules to the skills/ directory."
    )
    return skill_result(
        summary="Connections: AI-powered chat with skills.",
        text=description,
        system="Connections",
        tips=[
            "Ask 'help' or 'what skills are available' to see this listing.",
            "Include numbers directly in your prompt for statistics: 'mean of 1, 2, 3'.",
            "For email, specify to:, subject:, and body: fields in your prompt.",
            "Stock queries need a ticker symbol: 'quote for AAPL'.",
            "You can chat naturally — if no skill matches, the AI answers directly.",
        ],
        skill_count=len(set(list(SKILL_CATALOG.keys()) + _discover_skill_names())),
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _skill_detail(name: str) -> dict[str, Any]:
    catalog = SKILL_CATALOG.get(name, {})
    return {
        "skill_name": name,
        "description": catalog.get("description", "No description available."),
        "examples": catalog.get("examples", []),
        "routes": catalog.get("routes", {}),
    }


def _discover_skill_names() -> list[str]:
    """Best-effort discovery of configured skill names from the config server."""
    registry = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").strip().rstrip("/")
    try:
        with httpx.Client(timeout=_conf.get("registry_timeout", 2.0)) as client:
            r = client.get(f"{registry}/servers/configuration")
            if r.status_code != 200:
                return []
            config_url = (r.json() or {}).get("url", "").rstrip("/")
            if not config_url:
                return []
            r2 = client.get(f"{config_url}/configs")
            if r2.status_code != 200:
                return []
            keys = (r2.json() or {}).get("keys") or []
            return [k.split(":", 1)[1] for k in keys if isinstance(k, str) and k.startswith("skill:") and ":" in k]
    except Exception as exc:
        print(f"[help_skill] skill discovery failed: {exc}", flush=True)
        return []


def get_router() -> APIRouter:
    return router
