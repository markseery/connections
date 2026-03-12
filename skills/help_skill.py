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


router = APIRouter()


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
    "webscraper_skill": {
        "description": "Crawl a website, extract text, and optionally summarize it.",
        "examples": [
            "scrape https://example.com",
            "list scraping jobs",
            "show summary for job abc123",
        ],
        "routes": {
            "POST /skills/webscraper_skill/scrape": "Start a new scrape job.",
            "GET /skills/webscraper_skill/scrape": "List all jobs.",
            "GET /skills/webscraper_skill/scrape/{job_id}": "Get job status.",
            "GET /skills/webscraper_skill/scrape/{job_id}/markdown": "Get extracted markdown.",
            "GET /skills/webscraper_skill/scrape/{job_id}/summary": "Get AI summary.",
            "POST /skills/webscraper_skill/summarize_text": "Summarize text by topic (body: text, topic).",
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
    """List all available skills with descriptions and example prompts."""
    discovered = _discover_skill_names()
    skills: list[dict[str, Any]] = []
    for name in sorted(set(list(SKILL_CATALOG.keys()) + discovered)):
        entry = _skill_detail(name)
        skills.append(entry)
    return {"skills": skills, "count": len(skills)}


@router.get("/skills/{skill_name}")
def skill_detail(skill_name: str) -> dict[str, Any]:
    """Detail for a single skill: description, routes, example prompts."""
    name = skill_name.strip().lower()
    discovered = _discover_skill_names()
    if name not in SKILL_CATALOG and name not in discovered:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    return _skill_detail(name)


@router.get("/examples")
def examples() -> dict[str, Any]:
    """Return example prompts grouped by skill."""
    discovered = _discover_skill_names()
    result: dict[str, list[str]] = {}
    for name in sorted(set(list(SKILL_CATALOG.keys()) + discovered)):
        catalog = SKILL_CATALOG.get(name, {})
        result[name] = catalog.get("examples", [])
    return {"examples": result}


@router.get("/about")
def about() -> dict[str, Any]:
    """Describe the system and how to interact with it."""
    return {
        "system": "Connections",
        "description": (
            "Connections is a microservice platform with an AI-powered chat agent. "
            "When you send a message, the chat agent decides whether to use a "
            "registered skill (like statistics or email) or answer directly via the "
            "AI model. Skills are dynamically loaded into worker servers and can be "
            "extended by adding new modules to the skills/ directory."
        ),
        "tips": [
            "Ask 'help' or 'what skills are available' to see this listing.",
            "Include numbers directly in your prompt for statistics: 'mean of 1, 2, 3'.",
            "For email, specify to:, subject:, and body: fields in your prompt.",
            "Stock queries need a ticker symbol: 'quote for AAPL'.",
            "You can chat naturally — if no skill matches, the AI answers directly.",
        ],
        "skill_count": len(set(list(SKILL_CATALOG.keys()) + _discover_skill_names())),
    }


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
        with httpx.Client(timeout=2.0) as client:
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
