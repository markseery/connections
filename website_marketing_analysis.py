"""
License: MIT
Description: Scrape a website via stored_webscrape_skill (max_pages=1000, max_depth=10),
then use the stored content to run multiple AI summaries: products, solutions, services,
positioning, brand identity, value proposition, and implied strategy.

Requires: registry, storage, worker (with stored_webscrape_skill loadable), aiserver.

Usage:
  python website_marketing_analysis.py https://example.com
  python website_marketing_analysis.py https://example.com --out report.md
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx

from common.skill_lifecycle import find_live_worker


REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
MAX_CONTENT_CHARS = 120_000  # trim combined content so prompts stay within context
AI_TIMEOUT = 180.0

ANALYSIS_TOPICS = [
    "Headquarters",
    "Years in business",
    "Company executive names and titles",
    "number of data centers, their locations, GPUs, and power capacity",
    "active and contracted megawatts, terawatts, and other measures of power",
    "installed, active and future number of GPUs",
    "teraflops and exaflops",
    "customer names, locations, industry/type of entity, and other relevant details",
    "products",
    "solutions",
    "services",
    "ideal customer profile",
    "positioning statements",
    "overall brand identity",
    "value proposition statements",
    "the implied strategy for how it believes it will win",
]


def _worker_url() -> str:
    url = find_live_worker(REGISTRY_URL)
    if not url:
        raise RuntimeError("No live worker found in registry")
    return url.rstrip("/")


def _aiserver_url() -> str:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{REGISTRY_URL}/servers/aiserver")
        r.raise_for_status()
        u = (r.json() or {}).get("url")
        if not u:
            raise ValueError("Registry missing url for aiserver")
        return str(u).rstrip("/")


def _ensure_skill_loaded(worker_url: str) -> None:
    with httpx.Client(timeout=10.0) as client:
        r = client.post(f"{worker_url}/worker/skills/stored_webscrape_skill/load")
        r.raise_for_status()


def scrape_and_store(worker_url: str, url: str, max_pages: int, max_depth: int) -> dict[str, Any]:
    """Start stored_webscrape_skill scrape job; return job start payload."""
    _ensure_skill_loaded(worker_url)
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{worker_url}/skills/stored_webscrape_skill/scrape",
            json={"url": url, "max_pages": max_pages, "max_depth": max_depth},
        )
        r.raise_for_status()
    return r.json()


def get_scrape_job(worker_url: str, job_id: str) -> dict[str, Any]:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{worker_url}/skills/stored_webscrape_skill/scrape/{job_id}")
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("Job status response is not a JSON object")
        return data


def wait_for_scrape(worker_url: str, job_id: str, timeout_s: float, poll_s: float) -> dict[str, Any]:
    start = time.monotonic()
    last_line = ""
    while True:
        job = get_scrape_job(worker_url, job_id)
        status = str(job.get("status") or "unknown")
        crawled = int(job.get("pages_crawled") or 0)
        skipped = int(job.get("pages_skipped") or 0)
        failed = int(job.get("pages_failed") or 0)
        visited = int(job.get("urls_visited") or 0)
        max_pages = int(job.get("max_pages") or 0)
        elapsed = time.monotonic() - start

        line = (
            f"  progress: status={status} crawled={crawled}/{max_pages} "
            f"visited={visited} skipped={skipped} failed={failed} elapsed={elapsed:.0f}s"
        )
        if line != last_line:
            print(line, flush=True)
            last_line = line

        if status in {"completed", "failed"}:
            return job
        if elapsed > timeout_s:
            raise TimeoutError(f"Scrape job timed out after {timeout_s:.0f}s (job_id={job_id})")
        time.sleep(poll_s)


def get_stored_content(worker_url: str, base_url: str) -> dict[str, Any]:
    """Fetch stored scrape for base_url from skill (reads from storage)."""
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{worker_url}/skills/stored_webscrape_skill/stored",
            params={"base_url": base_url},
        )
        r.raise_for_status()
    data = r.json()
    value = data.get("value")
    if not isinstance(value, dict):
        raise ValueError("Stored scrape has no value")
    return value


def combined_text(value: dict[str, Any], max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Build one text block from content_by_url, optionally truncated."""
    content_by_url = value.get("content_by_url") or {}
    parts = []
    total = 0
    for u, text in content_by_url.items():
        if total >= max_chars:
            break
        chunk = (text or "")[: (max_chars - total)]
        if chunk:
            parts.append(chunk)
            total += len(chunk)
    return "\n\n---\n\n".join(parts) if parts else ""


def summarize_via_ai(aiserver_url: str, topic: str, content: str) -> str:
    """One AI call: summarize content with focus on topic. Profile fast."""
    prompt = (
        f"Summarize the following website content with focus on: **{topic}**. "
        "Be concise; use bullet points where helpful. If the site does not clearly address this topic, say so.\n\n"
        f"{content}"
    )
    with httpx.Client(timeout=AI_TIMEOUT) as client:
        r = client.post(
            f"{aiserver_url}/generate",
            json={"prompt": prompt, "profile": "fast"},
        )
        r.raise_for_status()
    out = r.json()
    output = out.get("output") if isinstance(out.get("output"), dict) else out
    if isinstance(output, dict) and "text" in output:
        return str(output["text"]).strip()
    return str(output).strip() if output else ""


def run_analyses(aiserver_url: str, content: str) -> dict[str, str]:
    """Run all 7 marketing analyses; return topic -> summary."""
    results: dict[str, str] = {}
    for topic in ANALYSIS_TOPICS:
        print(f"  Summarizing: {topic} ...", flush=True)
        try:
            results[topic] = summarize_via_ai(aiserver_url, topic, content)
        except Exception as e:
            results[topic] = f"[Error: {e}]"
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape a website, store content, then run marketing analysis summaries via AI."
    )
    parser.add_argument("url", help="Website URL to analyze (e.g. https://example.com)")
    parser.add_argument(
        "--out",
        default="",
        help="Optional path to write markdown report (default: print to stdout)",
    )
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Skip scrape; use already-stored content for base URL",
    )
    parser.add_argument(
        "--scrape-timeout",
        type=float,
        default=3600.0,
        help="Max seconds to wait for scraping job to complete (default: 3600)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between scrape progress polls (default: 2.0)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1000,
        help="Maximum pages to crawl (default: 1000, must be between 1 and 2000).",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=10,
        help="Maximum crawl depth (default: 10, must be between 1 and 15).",
    )
    args = parser.parse_args()
    url = args.url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    try:
        worker_url = _worker_url()
        aiserver_url = _aiserver_url()
    except Exception as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    if not args.skip_scrape:
        max_pages = max(1, min(2000, int(args.max_pages)))
        max_depth = max(1, min(15, int(args.max_depth)))
        print(f"Scraping site (max_pages={max_pages}, max_depth={max_depth}) ...", flush=True)
        try:
            started = scrape_and_store(worker_url, url, max_pages=max_pages, max_depth=max_depth)
            job_id = str(started.get("job_id") or "")
            if not job_id:
                raise RuntimeError(f"Missing job_id in response: {started!r}")
            print(f"  job_id={job_id} (polling for progress)", flush=True)
            final = wait_for_scrape(
                worker_url=worker_url,
                job_id=job_id,
                timeout_s=float(args.scrape_timeout),
                poll_s=float(args.poll_interval),
            )
            if str(final.get("status")) != "completed":
                raise RuntimeError(str(final.get("error") or "scrape failed"))
            print(
                f"  Stored {final.get('url_count', 0)} pages at key {final.get('url', '')}",
                flush=True,
            )
        except Exception as e:
            print(f"Scrape failed: {e}", file=sys.stderr)
            return 1
    else:
        print("Skipping scrape (using existing stored content).", flush=True)

    print("Loading stored content ...", flush=True)
    try:
        value = get_stored_content(worker_url, url)
    except Exception as e:
        print(f"Failed to load stored content: {e}", file=sys.stderr)
        return 1

    content = combined_text(value)
    if not content:
        print("No content in stored scrape.", file=sys.stderr)
        return 1
    print(f"  Using {len(content)} chars from {len(value.get('content_by_url') or {})} pages.", flush=True)

    print("Running AI marketing analyses (profile=fast) ...", flush=True)
    try:
        analyses = run_analyses(aiserver_url, content)
    except Exception as e:
        print(f"AI analysis failed: {e}", file=sys.stderr)
        return 1

    lines = [
        f"# Marketing analysis: {url}",
        "",
        f"*Based on stored scrape ({len(value.get('urls') or [])} URLs).*",
        "",
        "---",
        "",
    ]
    for topic in ANALYSIS_TOPICS:
        lines.append(f"## {topic.title()}")
        lines.append("")
        lines.append(analyses.get(topic, ""))
        lines.append("")
        lines.append("---")
        lines.append("")

    report = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report written to {args.out}", flush=True)
    else:
        print(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
