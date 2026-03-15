#!/usr/bin/env python3
"""
Standalone test: decode a Google News RSS article URL and fetch article content
using the logic in skills.rss_new_skill. Run from repo root:
  python3 scripts/test_google_news_content.py
"""
import os
import sys

# Project root on path so "skills" and "common" resolve
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

os.environ["RSS_NEW_SKILL_DEBUG"] = "1"

# Sample Google News RSS article URL (Maize n Brew / Michigan vs Purdue)
TEST_URL = (
    "https://news.google.com/rss/articles/CBMiygFBVV95cUxNemZPc2lvejFpUnBPOWUyOFEtRzlPOXZPNDcxaHpnZ3dwSUt5eEZuV0xLNlJVaWtnakpDLXVRWE8zNnNpXzYxTzI5TkZFUjN1NklLcWI5VEhRMURsQXZtS19obWhMeHRVcWYtOTJ4THVING5YLS1YY09xVXZJNVp3WWRjSXMtM0hqcmdLQUhtbmRZNjBjN1JlM1RINmhCM2FMUHREMjk4Wk05QWRfTkpVb0Y2ZGZXbHptMHE2cG1sTDNyU3NobktMNUVn?oc=5"
)


def main() -> None:
    from skills.rss_new_skill import _fetch_page_content

    url = os.environ.get("TEST_GOOGLE_NEWS_URL", TEST_URL).strip()
    print(f"Input URL: {url[:100]}...", flush=True)

    # Decode via common class (same logic the skill uses internally)
    from common.google_news_decoder import GoogleNewsDecoder
    decoder = GoogleNewsDecoder(log=lambda m: print(f"[decoder] {m}", file=sys.stderr, flush=True))
    decoded = decoder.decode(url)
    print(f"Decoded URL: {decoded or '(none)'}", flush=True)
    if not decoded:
        print("FAIL: could not decode Google News URL", flush=True)
        sys.exit(1)

    content = _fetch_page_content(url)
    print(f"Content length: {len(content)}", flush=True)
    print(f"Preview (first 400 chars): {repr(content[:400])}", flush=True)

    if len(content) < 100:
        print("FAIL: content too short (expected article text)", flush=True)
        sys.exit(1)
    if "Google News" in content and len(content) < 300:
        print("FAIL: still got stub 'Google News' page", flush=True)
        sys.exit(1)

    print("OK: decoded URL and got article content", flush=True)


if __name__ == "__main__":
    main()
