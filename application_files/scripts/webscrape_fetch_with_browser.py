#!/usr/bin/env python3
"""
Browser-based web scraper using Playwright (headless Chromium).

Bypasses Cloudflare and JS-heavy sites that block the httpx-based
webscraper_skill. Crawls pages via a real browser, extracts text,
and stores results in the same storage format as webscraper_skill
so they appear alongside skill-scraped pages.

Requires: pip install playwright && python -m playwright install chromium

Usage:
  python scripts/webscrape_fetch_with_browser.py https://6sense.com --namespace hackathon --max-pages 50 --max-depth 3
  python scripts/webscrape_fetch_with_browser.py https://example.com --headful  # visible browser
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext

_env = Path(__file__).resolve().parents[1] / ".env"
if _env.is_file():
    from dotenv import load_dotenv
    load_dotenv(_env)

DEFAULT_NAMESPACE = "webscrape"
REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
PAGE_KEY_SEP = "\x00"
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "skills" / "webscraper_skill"

MIN_TEXT_LENGTH = 80
MAX_CONTENT_CHARS = 8000
PAGE_TIMEOUT_MS = 60_000
NETWORK_SETTLE_MS = 3_000
CF_CHALLENGE_POLL_MS = 2_000
CF_CHALLENGE_MAX_WAIT_MS = 12_000
CRAWL_DELAY_SEC = 1.5


def _strip_query(url: str) -> str:
    u = (url or "").strip()
    q = u.find("?")
    return u[:q] if q >= 0 else u


def _canonical_sitename(url: str) -> str:
    return _strip_query(url).rstrip("/") or url.strip()


def _page_storage_key(sitename: str, page_url: str) -> str:
    sn = _canonical_sitename(sitename)
    pu = _strip_query(page_url.strip())
    return f"{sn}{PAGE_KEY_SEP}{pu}"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _build_page_value(namespace: str, sitename: str, page_url: str, content: str) -> dict:
    sn = _canonical_sitename(sitename)
    pu = _strip_query(page_url.strip())
    return {
        "namespace": namespace,
        "sitename": sn,
        "url": pu,
        "content": content,
        "contenthash": _content_hash(content),
    }


def _record_url(storage_base: str, namespace: str, storage_key: str) -> str:
    return f"{storage_base}/namespaces/{quote(namespace, safe='')}/records/{quote(storage_key, safe='')}"


def _get_storage_url() -> str:
    env_url = os.environ.get("STORAGE_SERVER_URL", "").strip().rstrip("/")
    if env_url:
        return env_url
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{REGISTRY_URL}/servers/storage")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise RuntimeError("storage server not found in registry")
        return str(url).rstrip("/")


def _extract_text(page: Page) -> str:
    """Extract visible text from the rendered page, stripping nav/footer/boilerplate."""
    try:
        for selector in ["nav", "footer", "header", "[role='navigation']", "[role='banner']",
                         ".cookie-banner", "#cookie-banner", ".cookie-consent"]:
            page.evaluate(f"""
                document.querySelectorAll('{selector}').forEach(el => el.remove());
            """)
    except Exception:
        pass

    try:
        article = page.query_selector("article") or page.query_selector("main") or page.query_selector("body")
        if not article:
            return ""
        text = article.inner_text()
    except Exception:
        try:
            text = page.inner_text("body")
        except Exception:
            return ""

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _extract_title(page: Page) -> str:
    try:
        return page.title() or ""
    except Exception:
        return ""


def _extract_links(page: Page, base_domain: str) -> list[str]:
    """Extract same-domain links from the rendered page."""
    try:
        hrefs = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(a => a.href)",
        )
    except Exception:
        return []

    links: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        if not isinstance(href, str):
            continue
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        clean = _strip_query(href.split("#")[0])
        if not clean or clean in seen:
            continue
        parsed = urlparse(clean)
        if parsed.netloc == base_domain:
            seen.add(clean)
            links.append(clean)
    return links


def _is_cf_challenge(page: Page) -> bool:
    """Detect Cloudflare 'Just a moment...' challenge page."""
    try:
        title = page.title().lower()
        if "just a moment" in title:
            return True
    except Exception:
        pass
    try:
        body = page.inner_text("body")
        if "checking your browser" in body.lower() or "just a moment" in body.lower():
            return True
    except Exception:
        pass
    return False


def _wait_for_cf_clearance(page: Page, verbose: bool) -> bool:
    """Poll until Cloudflare challenge resolves or times out. Returns True if cleared."""
    elapsed = 0
    while elapsed < CF_CHALLENGE_MAX_WAIT_MS:
        page.wait_for_timeout(CF_CHALLENGE_POLL_MS)
        elapsed += CF_CHALLENGE_POLL_MS
        if not _is_cf_challenge(page):
            if verbose:
                print(f" cleared ({elapsed}ms)", flush=True)
            page.wait_for_timeout(NETWORK_SETTLE_MS)
            return True
        if verbose:
            print(".", flush=True, end="")
    if verbose:
        print(f" timeout ({elapsed}ms)", flush=True)
    return False


_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
]

_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
});
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
});
window.chrome = { runtime: {} };
"""


def _launch_stealth(pw, *, headless: bool) -> Browser:
    """Launch Chromium with anti-bot-detection flags."""
    return pw.chromium.launch(
        headless=headless,
        args=_STEALTH_ARGS,
    )


class _CloudflareBlock(Exception):
    """Raised when the first page hits a Cloudflare challenge in headless mode."""


def _new_context(browser: Browser) -> BrowserContext:
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
    )
    ctx.add_init_script(_STEALTH_INIT_SCRIPT)
    return ctx


def _navigate(page: Page, url: str, *, verbose: bool) -> bool:
    """Navigate to *url*, handle CF challenges. Returns True if page is usable."""
    response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    if not response:
        if verbose:
            print(f"  SKIP {url} (no response)", flush=True)
        return False

    if response.status == 403 or _is_cf_challenge(page):
        if verbose:
            print(f"  CF challenge on {url}, waiting…", flush=True, end="")
        if _wait_for_cf_clearance(page, verbose):
            return True
        if verbose:
            print(f"  SKIP {url} (Cloudflare block)", flush=True)
        return False

    if response.status >= 400:
        if verbose:
            print(f"  SKIP {url} (status {response.status})", flush=True)
        return False

    page.wait_for_timeout(NETWORK_SETTLE_MS)
    return True


def _crawl(
    browser: Browser,
    root_url: str,
    *,
    max_pages: int,
    max_depth: int,
    delay: float,
    verbose: bool,
    is_headless: bool = True,
) -> list[dict]:
    """BFS crawl using a real browser. Returns list of page dicts."""
    root = _strip_query(root_url.strip())
    base_domain = urlparse(root).netloc
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(root, 0)]
    pages: list[dict] = []
    cf_failures = 0

    context = _new_context(browser)
    page = context.new_page()

    try:
        while queue and len(pages) < max_pages:
            url, depth = queue.pop(0)
            if url in visited or depth > max_depth:
                continue
            visited.add(url)

            if cf_failures > 5:
                if verbose:
                    print(f"  Too many Cloudflare blocks ({cf_failures}), stopping.", flush=True)
                break

            if is_headless and cf_failures >= 1:
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                    if not response or response.status == 403 or _is_cf_challenge(page):
                        cf_failures += 1
                        if verbose:
                            print(f"  CF block #{cf_failures} {url}", flush=True)
                        if cf_failures >= 3:
                            raise _CloudflareBlock()
                        continue
                except _CloudflareBlock:
                    raise
                except Exception:
                    cf_failures += 1
                    if cf_failures >= 3:
                        raise _CloudflareBlock()
                    continue

            try:
                ok = _navigate(page, url, verbose=verbose)
            except Exception as exc:
                if verbose:
                    print(f"  FAIL {url}: {exc}", flush=True)
                continue

            if not ok:
                cf_failures += 1
                if is_headless and cf_failures >= 3:
                    raise _CloudflareBlock()
                continue

            cf_failures = 0

            text = _extract_text(page)
            if len(text) < MIN_TEXT_LENGTH:
                if verbose:
                    print(f"  SKIP {url} (text too short: {len(text)} chars)", flush=True)
                continue

            title = _extract_title(page) or url
            pages.append({
                "url": url,
                "title": title,
                "content": text[:MAX_CONTENT_CHARS],
                "depth": depth,
            })

            if verbose:
                print(f"  [{len(pages):3d}/{max_pages}] depth={depth} {title[:60]}", flush=True)

            if depth < max_depth:
                for link in _extract_links(page, base_domain):
                    if link not in visited:
                        queue.append((link, depth + 1))

            time.sleep(delay)
    finally:
        context.close()

    return pages


def _build_markdown(root_url: str, pages: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# Website Content: {root_url}",
        "",
        f"*Crawled {len(pages)} pages on {now} UTC (browser-based)*",
        "",
        "---",
        "",
    ]
    for p in pages:
        lines.append(f"## {p.get('title') or p.get('url')}")
        lines.append("")
        lines.append(f"**URL:** {p.get('url')}")
        lines.append("")
        lines.append(str(p.get("content") or "")[:3000])
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def _store_pages(
    pages: list[dict],
    sitename: str,
    namespace: str,
    storage_url: str,
    verbose: bool,
) -> int:
    """Store crawled pages in the storage server. Returns count stored."""
    sn = _canonical_sitename(sitename)
    stored = 0
    with httpx.Client(timeout=60.0) as client:
        for p in pages:
            sk = _page_storage_key(sn, p["url"])
            body = _build_page_value(namespace, sn, p["url"], p.get("content") or "")
            try:
                r = client.put(_record_url(storage_url, namespace, sk), json=body)
                r.raise_for_status()
                stored += 1
            except Exception as exc:
                if verbose:
                    print(f"  STORE FAIL {p['url']}: {exc}", flush=True)
    return stored


def main() -> int:
    p = argparse.ArgumentParser(
        description="Browser-based web scraper (Playwright). Handles Cloudflare and JS-rendered sites.",
    )
    p.add_argument("url", help="Root URL to crawl (e.g. https://6sense.com)")
    p.add_argument("--namespace", default=DEFAULT_NAMESPACE, help="Storage namespace (default: webscrape)")
    p.add_argument("--max-pages", type=int, default=30, dest="max_pages", help="Max pages to crawl")
    p.add_argument("--max-depth", type=int, default=2, dest="max_depth", help="Max link depth")
    p.add_argument("--delay", type=float, default=CRAWL_DELAY_SEC, help="Delay between page loads (seconds)")
    p.add_argument("--headful", action="store_true", help="Force visible browser window")
    p.add_argument("--headless", action="store_true", help="Force headless (skip auto-headful fallback on CF block)")
    p.add_argument("--no-store", action="store_true", dest="no_store", help="Skip storing to storage server")
    p.add_argument("--registry-url", default=REGISTRY_URL, help="Registry base URL")
    p.add_argument("-q", "--quiet", action="store_true", help="Minimal output")

    args = p.parse_args()
    verbose = not args.quiet
    url = args.url.strip()

    if not url.startswith(("http://", "https://")):
        print("Error: URL must start with http:// or https://", file=sys.stderr)
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    use_headless = not args.headful

    if verbose:
        print(f"Crawling {url} (max_pages={args.max_pages}, max_depth={args.max_depth})", flush=True)
        print(f"  namespace={args.namespace}, headless={use_headless}", flush=True)
        print("", flush=True)

    with sync_playwright() as pw:
        browser = _launch_stealth(pw, headless=use_headless)
        try:
            pages = _crawl(
                browser,
                url,
                max_pages=args.max_pages,
                max_depth=args.max_depth,
                delay=args.delay,
                verbose=verbose,
                is_headless=use_headless,
            )
        except _CloudflareBlock:
            browser.close()
            if args.headless:
                print("\nCloudflare is blocking headless mode. "
                      "Remove --headless to allow auto-fallback to visible browser.",
                      file=sys.stderr)
                return 1
            if verbose:
                print("\n  Cloudflare detected — restarting with visible browser…\n", flush=True)
            use_headless = False
            browser = _launch_stealth(pw, headless=False)
            try:
                pages = _crawl(
                    browser,
                    url,
                    max_pages=args.max_pages,
                    max_depth=args.max_depth,
                    delay=args.delay,
                    verbose=verbose,
                    is_headless=False,
                )
            finally:
                browser.close()
        else:
            browser.close()

    if not pages:
        print("\nNo pages crawled.", file=sys.stderr)
        return 1

    if verbose:
        print(f"\nCrawled {len(pages)} pages.", flush=True)

    md = _build_markdown(url, pages)
    sitename = _canonical_sitename(url)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", sitename)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    md_path = DATA_DIR / f"browser_{safe_name}_{ts}.md"
    md_path.write_text(md, encoding="utf-8")
    if verbose:
        print(f"Markdown saved: {md_path}", flush=True)

    if not args.no_store:
        try:
            storage_url = _get_storage_url()
        except Exception as exc:
            print(f"Warning: could not find storage server ({exc}), skipping storage.", file=sys.stderr)
            return 0

        stored = _store_pages(pages, sitename, args.namespace, storage_url, verbose)
        if verbose:
            print(f"Stored {stored}/{len(pages)} pages in namespace={args.namespace!r}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
