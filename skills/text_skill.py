"""
License: MIT
Description: Text manipulation and AI-powered text processing skill.

Pure transformations (case, find, replace, etc.) run locally.
AI-powered routes (summarize, translate, extract, rewrite) call the aiserver.

Input: POST body per route.
Requires: aiserver (for AI routes only).
"""

from __future__ import annotations

import re
import textwrap
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from common.skill_response import skill_result
from common.skill_config import SkillConfig
from decorations.monitor import monitor

router = APIRouter()
_conf = SkillConfig("text_skill")


_AISERVER_FALLBACK = "http://127.0.0.1:7012"


def _aiserver_url() -> str:
    try:
        from common.registry_client import get_server_url
        return get_server_url("aiserver")
    except Exception:
        return _AISERVER_FALLBACK


def _ai_generate(prompt: str, profile: str | None = None) -> str:
    aiserver = _aiserver_url()
    profile = profile or _conf.get("default_ai_profile", "fast")
    timeout = _conf.get("ai_timeout", 120.0)
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            f"{aiserver}/generate",
            json={"prompt": prompt, "profile": profile},
        )
        r.raise_for_status()
        out = r.json().get("output") or {}
        if isinstance(out, dict):
            return str(out.get("text") or "")
        return str(out)


# ── Request models ─────────────────────────────────────────────────────────


class TextRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Input text")


class SummarizeRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to summarize")
    topic: str = Field(default="", description="Focus topic (blank = general)")
    style: str = Field(default="bullets", description="Output style: bullets, paragraph, or oneliner")
    max_chars: int | None = Field(default=None, description="Override max input chars (uses config default)")


class FindRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to search in")
    pattern: str = Field(..., min_length=1, description="Search string or regex pattern")
    regex: bool = Field(default=False, description="Treat pattern as regex")
    case_sensitive: bool = Field(default=False, description="Case-sensitive matching")


class ReplaceRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Input text")
    find: str = Field(..., min_length=1, description="String or regex to find")
    replace_with: str = Field(default="", description="Replacement string")
    regex: bool = Field(default=False, description="Treat find as regex")
    case_sensitive: bool = Field(default=True, description="Case-sensitive matching")
    max_replacements: int = Field(default=0, description="Max replacements (0 = unlimited)")


class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to translate")
    target_language: str = Field(..., min_length=1, description="Target language (e.g. 'Spanish', 'fr', 'Japanese')")
    source_language: str = Field(default="auto", description="Source language or 'auto' to detect")
    tone: str = Field(default="neutral", description="Tone: neutral, formal, casual")


class ExtractRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to extract from")
    extract: str = Field(
        ..., min_length=1,
        description="What to extract: emails, urls, dates, numbers, names, or a custom description",
    )


class RewriteRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to rewrite")
    tone: str = Field(default="professional", description="Target tone: professional, casual, academic, concise, friendly")
    instructions: str = Field(default="", description="Additional rewriting instructions")


class WordCountRequest(BaseModel):
    text: str = Field(..., description="Text to analyze")


class TruncateRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to truncate")
    max_length: int = Field(..., gt=0, description="Maximum character length")
    ellipsis: bool = Field(default=True, description="Append '...' when truncated")


class WrapRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to wrap")
    width: int = Field(default=80, gt=0, description="Line width in characters")


class SlugifyRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to convert to a URL slug")


class SplitRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to split")
    delimiter: str = Field(default="\n", description="Delimiter to split on")
    strip_empty: bool = Field(default=True, description="Remove empty segments")


# ── Pure transformation routes ─────────────────────────────────────────────


@monitor
@router.post("/touppercase")
def to_uppercase(body: TextRequest) -> dict[str, Any]:
    """Convert text to UPPERCASE."""
    result = body.text.upper()
    return skill_result(summary="Converted to uppercase.", text=result)


@monitor
@router.post("/tolowercase")
def to_lowercase(body: TextRequest) -> dict[str, Any]:
    """Convert text to lowercase."""
    result = body.text.lower()
    return skill_result(summary="Converted to lowercase.", text=result)


@monitor
@router.post("/tocamelcase")
def to_camel_case(body: TextRequest) -> dict[str, Any]:
    """Convert text to camelCase."""
    words = re.split(r"[\s_\-]+", body.text.strip())
    if not words:
        return skill_result(summary="Empty input.", text="")
    result = words[0].lower() + "".join(w.capitalize() for w in words[1:])
    return skill_result(summary="Converted to camelCase.", text=result)


@monitor
@router.post("/totitlecase")
def to_title_case(body: TextRequest) -> dict[str, Any]:
    """Convert text to Title Case."""
    result = body.text.title()
    return skill_result(summary="Converted to Title Case.", text=result)


@monitor
@router.post("/tosnakecase")
def to_snake_case(body: TextRequest) -> dict[str, Any]:
    """Convert text to snake_case."""
    s = body.text.strip()
    s = re.sub(r"([A-Z])", r" \1", s)
    result = re.sub(r"[\s\-]+", "_", s).strip("_").lower()
    return skill_result(summary="Converted to snake_case.", text=result)


@monitor
@router.post("/tokebabcase")
def to_kebab_case(body: TextRequest) -> dict[str, Any]:
    """Convert text to kebab-case."""
    s = body.text.strip()
    s = re.sub(r"([A-Z])", r" \1", s)
    result = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return skill_result(summary="Converted to kebab-case.", text=result)


@monitor
@router.post("/reverse")
def reverse_text(body: TextRequest) -> dict[str, Any]:
    """Reverse the input text."""
    result = body.text[::-1]
    return skill_result(summary="Text reversed.", text=result)


@monitor
@router.post("/wordcount")
def word_count(body: WordCountRequest) -> dict[str, Any]:
    """Count words, characters, lines, and sentences."""
    text = body.text
    words = text.split()
    lines = text.splitlines()
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    return skill_result(
        summary=f"**{len(words)}** words, **{len(text)}** chars, **{len(lines)}** lines, **{len(sentences)}** sentences.",
        words=len(words),
        characters=len(text),
        lines=len(lines),
        sentences=len(sentences),
    )


@monitor
@router.post("/truncate")
def truncate_text(body: TruncateRequest) -> dict[str, Any]:
    """Truncate text to a maximum length."""
    text = body.text
    if len(text) <= body.max_length:
        return skill_result(summary="Text within limit, no truncation needed.", text=text)
    cut = body.max_length - (3 if body.ellipsis else 0)
    result = text[:max(cut, 0)] + ("..." if body.ellipsis else "")
    return skill_result(
        summary=f"Truncated from {len(text)} to {len(result)} chars.",
        text=result,
    )


@monitor
@router.post("/wrap")
def wrap_text(body: WrapRequest) -> dict[str, Any]:
    """Wrap text to a specified line width."""
    result = textwrap.fill(body.text, width=body.width)
    return skill_result(summary=f"Wrapped to {body.width} chars per line.", text=result)


@monitor
@router.post("/slugify")
def slugify(body: SlugifyRequest) -> dict[str, Any]:
    """Convert text to a URL-safe slug."""
    s = body.text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    result = re.sub(r"[\s_]+", "-", s).strip("-")
    return skill_result(summary="Converted to slug.", text=result)


@monitor
@router.post("/split")
def split_text(body: SplitRequest) -> dict[str, Any]:
    """Split text by delimiter into a list of segments."""
    parts = body.text.split(body.delimiter)
    if body.strip_empty:
        parts = [p for p in parts if p.strip()]
    return skill_result(
        summary=f"Split into **{len(parts)}** segments.",
        items=[{"text": p, "index": i} for i, p in enumerate(parts)],
        count=len(parts),
    )


# ── Search / Replace routes ────────────────────────────────────────────────


@monitor
@router.post("/find")
def find_in_text(body: FindRequest) -> dict[str, Any]:
    """Find all occurrences of a string or regex in text."""
    flags = 0 if body.case_sensitive else re.IGNORECASE
    try:
        if body.regex:
            matches = [m.group() for m in re.finditer(body.pattern, body.text, flags)]
            positions = [(m.start(), m.end()) for m in re.finditer(body.pattern, body.text, flags)]
        else:
            pattern = re.escape(body.pattern)
            matches = [m.group() for m in re.finditer(pattern, body.text, flags)]
            positions = [(m.start(), m.end()) for m in re.finditer(pattern, body.text, flags)]
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}") from exc

    return skill_result(
        summary=f"Found **{len(matches)}** match(es).",
        items=[{"match": m, "start": p[0], "end": p[1]} for m, p in zip(matches, positions)],
        count=len(matches),
        pattern=body.pattern,
    )


@monitor
@router.post("/replace")
def replace_in_text(body: ReplaceRequest) -> dict[str, Any]:
    """Replace occurrences of a string or regex in text."""
    flags = 0 if body.case_sensitive else re.IGNORECASE
    count = body.max_replacements if body.max_replacements > 0 else 0
    try:
        if body.regex:
            result, n = re.subn(body.find, body.replace_with, body.text, count=count, flags=flags)
        else:
            pattern = re.escape(body.find)
            result, n = re.subn(pattern, body.replace_with, body.text, count=count, flags=flags)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}") from exc

    return skill_result(
        summary=f"Replaced **{n}** occurrence(s).",
        text=result,
        replacements=n,
    )


# ── AI-powered routes ──────────────────────────────────────────────────────


@monitor
@router.post("/summarize")
def summarize_text(body: SummarizeRequest) -> dict[str, Any]:
    """Summarize text with optional topic focus and style."""
    max_chars = body.max_chars or int(_conf.get("summarize_max_chars", 30000))
    truncated = body.text[:max_chars]
    topic_line = f"Focus on: {body.topic}. " if body.topic.strip() else ""

    style_instructions = {
        "bullets": "Use concise bullet points.",
        "paragraph": "Write a cohesive paragraph.",
        "oneliner": "Provide a single-sentence summary.",
    }
    style_inst = style_instructions.get(body.style, style_instructions["bullets"])

    prompt = (
        f"Summarize the following text. {topic_line}{style_inst} "
        "Be thorough but concise. Preserve key facts, names, and numbers.\n\n"
        f"{truncated}"
    )
    try:
        summary = _ai_generate(prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI summarization failed: {exc}") from exc

    return skill_result(
        summary=f"Summary{' on ' + body.topic if body.topic.strip() else ''} ({body.style}).",
        text=summary,
        style=body.style,
        topic=body.topic or "general",
    )


@monitor
@router.post("/translate")
def translate_text(body: TranslateRequest) -> dict[str, Any]:
    """Translate text to a target language."""
    max_chars = int(_conf.get("translate_max_chars", 30000))
    truncated = body.text[:max_chars]

    source_clause = (
        f"from {body.source_language} " if body.source_language != "auto" else ""
    )
    prompt = (
        f"Translate the following text {source_clause}to {body.target_language}. "
        f"Use a {body.tone} tone. Return ONLY the translated text, nothing else.\n\n"
        f"{truncated}"
    )
    try:
        translated = _ai_generate(prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Translation failed: {exc}") from exc

    return skill_result(
        summary=f"Translated to **{body.target_language}** ({body.tone}).",
        text=translated,
        target_language=body.target_language,
        source_language=body.source_language,
        tone=body.tone,
    )


@monitor
@router.post("/extract")
def extract_from_text(body: ExtractRequest) -> dict[str, Any]:
    """Extract structured data from text (emails, URLs, dates, names, or custom)."""
    builtin_patterns: dict[str, str] = {
        "emails": r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        "urls": r"https?://[^\s<>\"')\]]+",
        "numbers": r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?",
    }

    extract_key = body.extract.strip().lower()
    if extract_key in builtin_patterns:
        matches = re.findall(builtin_patterns[extract_key], body.text)
        unique = list(dict.fromkeys(matches))
        return skill_result(
            summary=f"Extracted **{len(unique)}** {extract_key}.",
            items=[{"value": v, "index": i} for i, v in enumerate(unique)],
            count=len(unique),
            type=extract_key,
        )

    max_chars = int(_conf.get("extract_max_chars", 30000))
    prompt = (
        f"Extract all {body.extract} from the following text. "
        "Return each extracted item on its own line, nothing else.\n\n"
        f"{body.text[:max_chars]}"
    )
    try:
        raw = _ai_generate(prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Extraction failed: {exc}") from exc

    items = [line.strip().lstrip("- ") for line in raw.splitlines() if line.strip()]
    return skill_result(
        summary=f"Extracted **{len(items)}** items ({body.extract}).",
        items=[{"value": v, "index": i} for i, v in enumerate(items)],
        count=len(items),
        type=body.extract,
    )


@monitor
@router.post("/rewrite")
def rewrite_text(body: RewriteRequest) -> dict[str, Any]:
    """Rewrite text in a different tone or style."""
    max_chars = int(_conf.get("rewrite_max_chars", 30000))
    truncated = body.text[:max_chars]

    extra = f" Additional instructions: {body.instructions}" if body.instructions.strip() else ""
    prompt = (
        f"Rewrite the following text in a {body.tone} tone. "
        f"Preserve the original meaning and key information.{extra}\n\n"
        f"{truncated}"
    )
    try:
        rewritten = _ai_generate(prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Rewrite failed: {exc}") from exc

    return skill_result(
        summary=f"Rewritten in **{body.tone}** tone.",
        text=rewritten,
        tone=body.tone,
    )


def get_router() -> APIRouter:
    return router
