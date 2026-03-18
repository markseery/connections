"""
Retrieve full site content from stored webscrape and iterate over each page.

Uses the worker's stored_webscrape_skill to fetch the stored scrape for a base URL,
then exposes (url, content) pairs one by one. Also supports building from an existing
combined_text block (e.g. from a prior step that used max_chars).
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import httpx

def _find_live_worker(*args, **kwargs):
    from .skill_lifecycle import find_live_worker
    return find_live_worker(*args, **kwargs)

STORED_SKILL_NAME = "stored_webscrape_skill"
DEFAULT_REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
FETCH_TIMEOUT = 30.0
LOAD_SKILL_TIMEOUT = 10.0

# Same format as stored_webscrape_skill combined_text output
_PAGE_SEP = "\n\n---\n\n"
_URL_PREFIX = "URL: "


def _parse_combined_text(combined_text: str) -> list[tuple[str, str]]:
    """Parse combined_text block into (url, content) pairs. Matches stored_webscrape_skill format."""
    if not (combined_text or "").strip():
        return []
    segments = combined_text.strip().split(_PAGE_SEP)
    pages: list[tuple[str, str]] = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if seg.startswith(_URL_PREFIX):
            first_newline = seg.find("\n")
            if first_newline >= 0:
                url = seg[len(_URL_PREFIX) : first_newline].strip()
                content = seg[first_newline :].strip()
            else:
                url = seg[len(_URL_PREFIX) :].strip()
                content = ""
            pages.append((url, content))
        elif pages:
            last_url, last_content = pages[-1]
            pages[-1] = (last_url, (last_content + "\n\n" + seg).strip())
    return pages


class StoredSiteContent:
    """
    Full site content from stored webscrape; iterate over each page as (url, content).

    Usage:
        content = StoredSiteContent("https://example.com", worker_url=worker_url)
        content.load()
        for url, text in content:
            ...
    Or from an existing combined_text block:
        content = StoredSiteContent.from_combined_text(combined_text)
        for url, text in content:
            ...
    """

    def __init__(
        self,
        base_url: str,
        worker_url: str | None = None,
        *,
        registry_url: str | None = None,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self._worker_url = (worker_url or "").strip().rstrip("/") if worker_url else None
        self._registry_url = (registry_url or DEFAULT_REGISTRY_URL).rstrip("/")
        self._value: dict[str, Any] | None = None
        self._pages: list[tuple[str, str]] | None = None  # used when built from combined_text

    @classmethod
    def from_combined_text(cls, combined_text: str) -> StoredSiteContent:
        """Build from an existing combined_text block (e.g. from stored skill with max_chars)."""
        inst = cls.__new__(cls)
        inst.base_url = ""
        inst._worker_url = None
        inst._registry_url = DEFAULT_REGISTRY_URL.rstrip("/")
        inst._value = None
        inst._pages = _parse_combined_text(combined_text or "")
        return inst

    def _get_worker_url(self) -> str:
        if self._worker_url:
            return self._worker_url
        url = _find_live_worker(self._registry_url)
        if not url:
            raise RuntimeError("No live worker found in registry")
        return url.rstrip("/")

    def _ensure_skill_loaded(self, worker_url: str) -> None:
        """POST to worker to load stored_webscrape_skill so /skills/.../stored is available."""
        with httpx.Client(timeout=LOAD_SKILL_TIMEOUT) as client:
            r = client.post(
                f"{worker_url}/worker/skills/{STORED_SKILL_NAME}/load",
            )
            r.raise_for_status()

    def load(self) -> None:
        """Fetch stored scrape for base_url from the worker. Idempotent."""
        if self._pages is not None:
            return  # from_combined_text; nothing to load
        worker_url = self._get_worker_url()
        self._ensure_skill_loaded(worker_url)
        with httpx.Client(timeout=FETCH_TIMEOUT) as client:
            r = client.post(
                f"{worker_url}/skills/{STORED_SKILL_NAME}/stored",
                json={"base_url": self.base_url},
            )
            if r.status_code == 404:
                # Retry once after loading: worker may have multiple processes and load hit another process
                self._ensure_skill_loaded(worker_url)
                r = client.post(
                    f"{worker_url}/skills/{STORED_SKILL_NAME}/stored",
                    json={"base_url": self.base_url},
                )
            if r.status_code == 404:
                raise ValueError(
                    f"No stored scrape found for {self.base_url!r}. "
                    "Scrape the site first (e.g. stored_webscrape_skill /scrape or website_marketing_analysis.py)."
                )
            r.raise_for_status()
            data = r.json()
        value = data.get("value")
        if not isinstance(value, dict):
            raise ValueError("Stored scrape response has no value")
        self._value = value

    def _content_by_url(self) -> dict[str, str]:
        if self._pages is not None:
            return dict(self._pages)
        if self._value is None:
            self.load()
        assert self._value is not None
        return self._value.get("content_by_url") or {}

    def __iter__(self) -> Iterator[tuple[str, str]]:
        """Yield (url, content) for each page."""
        if self._pages is not None:
            yield from self._pages
            return
        for url, content in self._content_by_url().items():
            yield (url, content or "")

    def __len__(self) -> int:
        """Number of pages."""
        if self._pages is not None:
            return len(self._pages)
        return len(self._content_by_url())

    @property
    def value(self) -> dict[str, Any] | None:
        """Raw stored value (base_url, scraped_at, urls, content_by_url); None if from_combined_text."""
        if self._value is None and self._pages is None:
            return None
        if self._value is not None:
            return self._value
        # Build a minimal value-like dict from _pages
        urls = [u for u, _ in self._pages or []]
        content_by_url = dict(self._pages or [])
        return {"urls": urls, "content_by_url": content_by_url}
