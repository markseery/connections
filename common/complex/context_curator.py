"""
License: MIT
Description: Pre-conversation context curation for AI deliberation sessions.

Runs parallel operations to gather fresh context before a conversation begins:

1. **AI-generated search queries** — asks the AI to craft targeted search queries
   from the topic, then runs each through the websearch skill (Google Custom Search
   with DuckDuckGo fallback) for real web results with extracted content and citations.
2. **Per-site extraction** (profile=agent, one call per file) — reads each scraped
   site file individually, asks the AI to extract only topic-relevant information
   within a strict word budget, so every company is covered regardless of file size.

The combined output is returned as a single context string that can be prepended
to the conversation's existing context block.

Usage:
    from common.context_curator import ContextCurator

    curator = ContextCurator(topic="How should we position against CoreWeave?")
    curated = curator.curate()
    full_context = curated + "\\n\\n" + existing_context
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from common.compound.http_client import http_client
from common.compound.registry_client import get_server_url
from common.complex.skill_lifecycle import find_live_worker

_ROOT = Path(__file__).resolve().parents[2]
_SITES_DIR = _ROOT / "data" / "webscrape" / "sites"

_EXTRACTION_WORDS = 500
_MAX_TOTAL_EXTRACTION_WORDS = 5_000
_MAX_INPUT_CHARS = 600_000
_NUM_SEARCH_QUERIES = 3


class ContextCurator:
    """Gathers web search results and scraped-site extractions relevant to a topic.

    Parameters
    ----------
    topic : str
        The conversation topic used to orient all AI calls.
    context : str
        Additional context description (from the YAML config) used when
        generating search queries.
    sites_dir : Path | None
        Directory containing scraped ``.md`` files. Defaults to
        ``data/webscrape/sites`` relative to the project root.
    timeout : float
        Per-call timeout in seconds for each AI/skill request.
    num_queries : int
        Number of search queries to generate and execute.
    verbose : bool
        Print progress to stdout.
    """

    def __init__(
        self,
        topic: str,
        *,
        context: str = "",
        sites_dir: Path | None = None,
        timeout: float = 180.0,
        num_queries: int = _NUM_SEARCH_QUERIES,
        verbose: bool = True,
    ) -> None:
        self.topic = topic
        self.context = context
        self.sites_dir = sites_dir or _SITES_DIR
        self.timeout = timeout
        self.num_queries = num_queries
        self.verbose = verbose

        self.web_context: str = ""
        self.site_context: str = ""

    def curate(self) -> str:
        """Generate search queries, run web searches and per-site extractions, return combined context."""
        site_files = self._discover_site_files()
        words_per_site = self._words_per_site(len(site_files))

        registry_url = os.environ.get(
            "REGISTRY_SERVER_URL", "http://127.0.0.1:7002"
        ).strip().rstrip("/")
        worker_url = find_live_worker(registry_url)

        queries = self._generate_search_queries()

        if not worker_url and self.verbose:
            print("[context_curator] no live worker found — skipping web searches",
                  flush=True)

        with ThreadPoolExecutor(max_workers=3) as pool:
            search_futures: dict[Any, str] = {}
            if worker_url and queries:
                for q in queries:
                    f = pool.submit(self._web_search, worker_url, q)
                    search_futures[f] = q

            site_futures = {
                pool.submit(self._extract_one_site, path, words_per_site): path
                for path in site_files
            }

            search_results: list[str] = []
            for future in as_completed(search_futures):
                query = search_futures[future]
                try:
                    text = future.result()
                    if text:
                        search_results.append(text)
                except Exception as exc:
                    if self.verbose:
                        print(f"[context_curator] web search failed for "
                              f"{query!r}: {exc}", flush=True)

            extractions: list[tuple[str, str]] = []
            retry_queue: list[tuple[Path, int]] = []

            for future in as_completed(site_futures):
                path = site_futures[future]
                company = path.stem.replace("_", " ").replace("-", " ").title()
                try:
                    text = future.result()
                    if text:
                        extractions.append((company, text))
                except Exception as exc:
                    status = _http_status(exc)
                    if self.verbose:
                        print(f"[context_curator] {company} extraction failed "
                              f"(HTTP {status}): {exc}", flush=True)
                    retry_queue.append((path, status))

        if retry_queue:
            self._retry_failed(retry_queue, words_per_site, extractions)

        if search_results:
            self.web_context = "\n\n---\n\n".join(search_results)

        extractions.sort(key=lambda t: t[0])
        if extractions:
            parts = [f"### {company}\n\n{text}" for company, text in extractions]
            self.site_context = "\n\n".join(parts)

        sections: list[str] = []
        if self.web_context:
            sections.append(f"## Web Research\n\n{self.web_context}")
        if self.site_context:
            sections.append(
                f"## Competitive Intelligence (from scraped websites)\n\n{self.site_context}"
            )

        combined = "\n\n---\n\n".join(sections)

        if self.verbose and combined:
            web_len = len(self.web_context)
            site_len = len(self.site_context)
            print(f"[context_curator] curated {web_len + site_len:,} chars "
                  f"(web: {web_len:,}, sites: {site_len:,})", flush=True)

        return combined

    def _discover_site_files(self) -> list[Path]:
        if not self.sites_dir.is_dir():
            return []
        return sorted(self.sites_dir.glob("*.md"))

    def _words_per_site(self, n_sites: int) -> int:
        if n_sites == 0:
            return _EXTRACTION_WORDS
        return max(200, min(_EXTRACTION_WORDS, _MAX_TOTAL_EXTRACTION_WORDS // n_sites))

    def _generate_search_queries(self) -> list[str]:
        """Ask AI to produce targeted search queries for the topic."""
        if self.verbose:
            print(f"[context_curator] generating {self.num_queries} search queries…",
                  flush=True)

        context_block = ""
        if self.context:
            context_block = f"\nCONTEXT:\n{self.context}\n"

        prompt = (
            f"Generate exactly {self.num_queries} web search queries that would find "
            f"information relevant to the following topic. The queries should cover "
            f"different angles: recent news, technical details, and competitive "
            f"landscape.\n\n"
            f"TOPIC: {self.topic}\n"
            f"{context_block}\n"
            f"Return ONLY a JSON array of strings, nothing else. Example:\n"
            f'["query one", "query two", "query three"]'
        )

        try:
            text = _ai_call(prompt, profile="agent", timeout=self.timeout)
            text = _strip_json_fences(text)
            queries = json.loads(text)
            if isinstance(queries, list):
                queries = [str(q).strip() for q in queries if str(q).strip()]
                if self.verbose:
                    for q in queries:
                        print(f"[context_curator]   → {q}", flush=True)
                return queries[:self.num_queries]
        except (json.JSONDecodeError, Exception) as exc:
            if self.verbose:
                print(f"[context_curator] query generation failed: {exc}", flush=True)

        if self.verbose:
            print("[context_curator] falling back to topic as search query", flush=True)
        return [self.topic]

    def _web_search(self, worker_url: str, query: str) -> str:
        """Call the websearch skill on the worker and return the summary."""
        if self.verbose:
            print(f"[context_curator] searching: {query!r}", flush=True)

        url = f"{worker_url}/skills/websearch_skill/search"
        payload = {"query": query, "limit": 5}

        with http_client("context_curator", timeout=self.timeout) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()

        data = r.json()
        summary = data.get("summary", "")

        if self.verbose:
            print(f"[context_curator] search result: {len(summary):,} chars "
                  f"for {query!r}", flush=True)
        return summary

    def _retry_failed(
        self,
        retry_queue: list[tuple[Path, int]],
        words_per_site: int,
        extractions: list[tuple[str, str]],
    ) -> None:
        """Retry failed extractions sequentially after the initial parallel batch."""
        for path, status in retry_queue:
            company = path.stem.replace("_", " ").replace("-", " ").title()

            if status in (429, 503):
                if self.verbose:
                    print(f"[context_curator] retrying {company} "
                          f"(HTTP {status}, transient)…", flush=True)
                time.sleep(2.0)
                try:
                    text = self._extract_one_site(path, words_per_site)
                    if text:
                        extractions.append((company, text))
                    continue
                except Exception as exc:
                    if self.verbose:
                        print(f"[context_curator] {company} retry failed: {exc}",
                              flush=True)

            elif status == 400:
                file_len = path.stat().st_size
                halved = file_len // 2
                if self.verbose:
                    print(f"[context_curator] retrying {company} with halved input "
                          f"({halved:,} chars, was 400 Bad Request)…", flush=True)
                try:
                    text = self._extract_one_site(path, words_per_site, max_input_chars=halved)
                    if text:
                        extractions.append((company, text))
                    continue
                except Exception as exc:
                    if self.verbose:
                        print(f"[context_curator] {company} retry failed: {exc}",
                              flush=True)

            else:
                if self.verbose:
                    print(f"[context_curator] {company} not retried (HTTP {status})",
                          flush=True)

    def _extract_one_site(
        self, path: Path, max_words: int, max_input_chars: int | None = None,
    ) -> str:
        """Extract topic-relevant information from a single site file.

        Parameters
        ----------
        max_input_chars : int | None
            If set, truncate the file content to this many characters before
            sending to the AI.  Used on retry when the full file exceeded the
            model's context window (400 Bad Request).
        """
        company = path.stem.replace("_", " ").replace("-", " ").title()

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

        if not content.strip():
            return ""

        cap = max_input_chars if max_input_chars is not None else _MAX_INPUT_CHARS
        if len(content) > cap:
            content = content[:cap]

        if self.verbose:
            print(f"[context_curator] extracting {company} "
                  f"({len(content):,} chars, budget {max_words} words)…", flush=True)

        prompt = (
            f"Extract from the text below any information relevant to the "
            f"following topic. Summarise in bullet points. "
            f"Keep your response under {max_words} words. "
            f"Omit navigation, footers, legal text, and boilerplate.\n\n"
            f"TOPIC: {self.topic}\n"
            f"COMPANY: {company}\n\n"
            f"---\n\n{content}"
        )

        text = _ai_call(prompt, profile="agent", timeout=self.timeout)

        if self.verbose:
            print(f"[context_curator] {company}: {len(text):,} chars", flush=True)
        return text


# ── Helpers ───────────────────────────────────────────────────────────────


def _ai_call(prompt: str, *, profile: str, timeout: float) -> str:
    """Call the AI server. Raises on failure — no silent fallbacks."""
    aiserver = get_server_url("aiserver")
    payload: dict[str, Any] = {"prompt": prompt, "profile": profile}

    with http_client("context_curator", timeout=timeout) as client:
        r = client.post(f"{aiserver}/generate", json=payload)
        r.raise_for_status()

    data = r.json()
    out = data.get("output")
    if isinstance(out, dict):
        return str(out.get("text", "")).strip()
    if isinstance(out, str):
        return out.strip()
    return str(out).strip()


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences that some models wrap around JSON output."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _http_status(exc: Exception) -> int:
    """Extract an HTTP status code from an exception, or 0 if unavailable."""
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            return int(response.status_code)
        except (ValueError, TypeError, AttributeError):
            pass
    return 0
