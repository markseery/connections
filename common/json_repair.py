"""
License: MIT
Description: Resilient JSON extraction from LLM output.

LLMs frequently return JSON wrapped in prose, fenced code blocks, or with
structural errors (orphaned fields, trailing commas, unbalanced braces).
This module provides a single entry point — `parse_llm_json` — that handles
all of these cases and returns a plain dict, or raises ValueError on failure.
"""

from __future__ import annotations

import json
import re
from typing import Any


def parse_llm_json(text: str) -> dict[str, Any]:
    """
    Extract and parse the first JSON object from raw LLM output.

    Pipeline:
      1. Strip fenced code blocks (```json ... ```)
      2. Extract the outermost {...} using brace-depth tracking
      3. Attempt json.loads
      4. On failure, run structural repair and retry

    Raises ValueError if no JSON object can be recovered.
    """
    t = _strip_fences(text)

    candidate = extract_brace_block(t)
    if candidate is None:
        raise ValueError("No JSON object found in text")

    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError as exc:
        print(f"[json_repair] raw parse failed, attempting repair: {exc}", flush=True)

    repaired = repair_json(candidate)
    try:
        obj = json.loads(repaired)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError as exc:
        print(f"[json_repair] repaired parse also failed: {exc}", flush=True)

    raise ValueError(f"Could not parse JSON from text: {candidate[:200]}")


def parse_llm_json_or_none(text: str) -> dict[str, Any] | None:
    """Like parse_llm_json but returns None instead of raising."""
    try:
        return parse_llm_json(text)
    except (ValueError, Exception) as exc:
        print(f"[json_repair] parse_llm_json_or_none failed: {exc}", flush=True)
        return None


# ── Extraction ──────────────────────────────────────────────────────────


def extract_brace_block(text: str) -> str | None:
    """Find the first top-level {...} block using brace-depth tracking."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "\"":
                in_str = False
            continue
        if ch == "\"":
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ── Repair ──────────────────────────────────────────────────────────────


def repair_json(text: str) -> str:
    """
    Rebuild a malformed JSON plan by extracting individual {...} blocks and
    absorbing orphaned key:value pairs back into the preceding block.

    Handles the most common LLM mistake: fields (especially "depends_on")
    placed outside their enclosing object but still inside the parent array.
    """
    # Extract top-level simple fields (like "objective").
    top_fields: dict[str, Any] = {}
    for m in re.finditer(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"', text):
        key = m.group(1)
        if key in {"objective", "use_skill"}:
            top_fields[key] = m.group(2)

    # Look for an array field (like "steps") and rebuild its contents.
    array_match = re.search(r'"(\w+)"\s*:\s*\[', text)
    if not array_match:
        return text

    array_key = array_match.group(1)
    remainder = text[array_match.end():]
    objects: list[dict[str, Any]] = []

    pos = 0
    while pos < len(remainder):
        brace_start = remainder.find("{", pos)
        if brace_start == -1:
            _absorb_orphans(remainder[pos:], objects)
            break

        _absorb_orphans(remainder[pos:brace_start], objects)

        block = extract_brace_block(remainder[brace_start:])
        if block is None:
            break
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                objects.append(obj)
        except json.JSONDecodeError as exc:
            print(f"[json_repair] skipping malformed block in repair: {exc}", flush=True)
        pos = brace_start + len(block)

    result: dict[str, Any] = {**top_fields, array_key: objects}
    return json.dumps(result, ensure_ascii=False)


# ── Internals ───────────────────────────────────────────────────────────


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    return t


def _absorb_orphans(gap: str, objects: list[dict[str, Any]]) -> None:
    """
    Scan a text gap between {...} blocks for orphaned "key":value pairs
    and merge them into the last object.
    """
    if not objects:
        return
    for m in re.finditer(
        r'"(\w+)"\s*:\s*(\[[^\]]*\]|"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null)',
        gap,
    ):
        key = m.group(1)
        val_str = m.group(2)
        try:
            val = json.loads(val_str)
        except Exception as exc:
            print(f"[json_repair] orphan value parse failed for key={key}: {exc}", flush=True)
            continue
        objects[-1][key] = val
