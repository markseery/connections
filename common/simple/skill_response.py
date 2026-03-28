"""
License: MIT
Description: Standard skill response format and a single formatter for chat/UI display.

CANONICAL RESPONSE SHAPE (skills should return this so no per-skill formatting is needed):
- summary (str, optional): Human-readable summary; markdown allowed.
- items (list, optional): Display list. Each item: title, link or url, summary (optional).
  "articles" is accepted as an alias for "items".
- text (str, optional): Plain or markdown body.
- examples (dict, optional): Map of label -> list of strings (e.g. skill name -> example prompts).

The formatter also normalizes common non-canonical keys (new_items, list_name, counts, etc.)
into this shape so existing skills render consistently without code changes per skill.
"""

from __future__ import annotations

import json
from typing import Any

from common.simple.models import skill_result  # noqa: F401  — re-export for convenient access

# Standard field names skills can use for consistent display
SUMMARY = "summary"
ITEMS = "items"
ARTICLES = "articles"
TEXT = "text"
QUERY = "query"
EXAMPLES = "examples"


def _normalize_to_standard(data: dict[str, Any]) -> dict[str, Any]:
    """
    Map common skill response shapes into the canonical summary + items/text shape.
    No per-skill branches: we only recognize generic patterns (list-like key + counts).
    """
    if data.get(SUMMARY) or data.get(ITEMS) or data.get(ARTICLES) or data.get(TEXT) or data.get(EXAMPLES):
        return data
    # new_items (e.g. rss_new_skill): list of { title, link, ... }
    new_items = data.get("new_items")
    if isinstance(new_items, list) and new_items and all(isinstance(x, dict) for x in new_items[:3]):
        list_name = data.get("list_name") or ""
        count = data.get("new_items_count", len(new_items))
        feeds = data.get("feeds_count") or ""
        summary_line = f"**{list_name}**: {count} new items" + (f" from {feeds} feeds." if feeds else ".")
        return {**data, SUMMARY: summary_line, ITEMS: new_items}
    # results / entries: list of dicts with title/link
    for key in ("results", "entries", "entries_list"):
        raw = data.get(key)
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            summary_line = data.get("summary") or f"**{len(raw)}** items."
            return {**data, SUMMARY: summary_line, ITEMS: raw}
    return data


def skill_response_to_markdown(data: Any) -> str:
    """
    Render any skill response_data as markdown for chat/UI. Single code path for all skills.
    Expects optional: summary, items (or articles), text. Falls back to generic display.
    """
    if data is None:
        return "_No data_"
    if isinstance(data, str):
        return data.strip() or "_Empty_"
    if not isinstance(data, dict):
        return str(data)
    data = _normalize_to_standard(data)

    parts: list[str] = []

    summary = data.get(SUMMARY)
    if isinstance(summary, str) and summary.strip():
        query = data.get(QUERY)
        head = f"**Query:** {query}\n\n" if isinstance(query, str) and query.strip() else ""
        parts.append((head + summary.strip()).strip())
        parts.append("")

    # items or articles: same shape — title, link/url, summary
    raw_list = data.get(ITEMS) or data.get(ARTICLES)
    if isinstance(raw_list, list) and raw_list:
        parts.append("### Results")
        parts.append("")
        for a in raw_list[:25]:
            if not isinstance(a, dict):
                parts.append(f"- {a}")
                continue
            title = a.get("title") or a.get("name") or "Untitled"
            link = a.get("link") or a.get("url") or ""
            summ = a.get("summary") or ""
            if link:
                parts.append(f"- [{title}]({link})" + (f" — {summ}" if summ else ""))
            else:
                parts.append(f"- **{title}**" + (f" — {summ}" if summ else ""))

    text = data.get(TEXT)
    if isinstance(text, str) and text.strip():
        if parts:
            parts.append("")
        parts.append(text.strip())

    # examples: dict of label -> list of strings (e.g. skill_name -> example prompts)
    examples = data.get(EXAMPLES)
    if isinstance(examples, dict) and examples:
        if parts:
            parts.append("")
        parts.append("## Example prompts")
        parts.append("")
        for label, prompt_list in examples.items():
            if not isinstance(prompt_list, list):
                continue
            # Skip sections with no prompts so we don't show empty headers
            prompts = [p for p in prompt_list[:30] if p is not None]
            if not prompts:
                continue
            parts.append(f"### {label}")
            parts.append("")
            for p in prompts:
                parts.append(f"- {p}" if isinstance(p, str) else f"- {str(p)}")
            parts.append("")

    if parts:
        return "\n".join(parts).strip()

    # Generic fallback: avoid dumping huge lists/objects as raw key-value
    def _safe_value(v: Any) -> str:
        if isinstance(v, list):
            return f"{len(v)} items" if v else "[]"
        if isinstance(v, dict):
            return "<object>"
        return str(v)
    lines = [f"**{k}:** {_safe_value(v)}" for k, v in data.items() if v is not None and v != ""]
    if lines:
        return "\n".join(lines)
    try:
        compact = json.dumps(data, default=str)
        if len(compact) <= 400:
            return f"```json\n{compact}\n```"
    except Exception:
        pass
    return "```json\n{}\n```"
