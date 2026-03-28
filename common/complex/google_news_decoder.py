"""
License: MIT
Google News RSS article URL decoder.

Decodes news.google.com/rss/articles/CBMi... URLs to the real article URL using:
- Legacy base64 decode for old-style URLs that embed the URL directly.
- 3-arg batchexecute flow (post-Aug 2024): GET article page for signature/timestamp, POST batchexecute.
"""

from __future__ import annotations

import base64
import json
import ssl
import time
from typing import Callable
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


def _default_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class GoogleNewsDecoder:
    """
    Decode Google News RSS article URLs to real article URLs.
    Uses legacy base64 when the payload is a direct URL; otherwise 3-arg batchexecute only.
    """

    ARTICLES_BASE = "https://news.google.com/articles/"
    BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je"

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        user_agent: str = "ConnectionsGoogleNewsDecoder/1.0",
        ssl_verify: ssl.SSLContext | bool = True,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.ssl_verify = ssl_verify if isinstance(ssl_verify, ssl.SSLContext) else _default_ssl_context()
        self._log = log or (lambda _: None)

    def is_google_news_article_url(self, url: str) -> bool:
        """Return True if url is a news.google.com/rss/articles/ URL."""
        if not (url or "").strip().startswith(("http://", "https://")):
            return False
        try:
            parsed = urlparse(url)
            if (parsed.netloc or "").lower() not in ("news.google.com", "www.news.google.com"):
                return False
            path_parts = (parsed.path or "").strip("/").split("/")
            return len(path_parts) >= 2 and path_parts[-2] == "articles"
        except Exception:
            return False

    def decode(self, source_url: str) -> str | None:
        """
        If source_url is a news.google.com/rss/articles/ URL, return the real article URL.
        Otherwise return None. Uses legacy base64 decode when possible, else 3-arg batchexecute.
        """
        if not self.is_google_news_article_url(source_url):
            return None
        base64_str = self._extract_base64_id(source_url)
        if not base64_str or not base64_str.startswith("CBMi"):
            return None

        self._log(f"decode_google_news: base64_id len={len(base64_str)}")

        # Legacy base64 decode (old-style URLs that embed the URL directly)
        decoded_url = self._legacy_base64_decode(base64_str)
        if decoded_url is not None:
            return decoded_url

        # New-style: 3-arg batchexecute only
        self._log("decode_google_news: new-style or not URL, using batchexecute")
        return self._decode_via_batchexecute(base64_str)

    def _extract_base64_id(self, source_url: str) -> str | None:
        try:
            parsed = urlparse(source_url)
            path_parts = (parsed.path or "").strip("/").split("/")
            if len(path_parts) < 2 or path_parts[-2] != "articles":
                return None
            return path_parts[-1].split("?")[0]
        except Exception:
            return None

    def _legacy_base64_decode(self, base64_str: str) -> str | None:
        """Return decoded URL if payload is old-style (direct URL), else None."""
        try:
            pad = (4 - len(base64_str) % 4) % 4
            padded = base64_str + "=" * pad
            decoded_bytes = base64.urlsafe_b64decode(padded)
            decoded_str = decoded_bytes.decode("latin-1")
        except Exception as e:
            self._log(f"decode_google_news: base64 decode failed: {e}")
            return None

        prefix = bytes((0x08, 0x13, 0x22)).decode("latin-1")
        if decoded_str.startswith(prefix):
            decoded_str = decoded_str[len(prefix) :]
        suffix = bytes((0xD2, 0x01, 0x00)).decode("latin-1")
        if decoded_str.endswith(suffix):
            decoded_str = decoded_str[: -len(suffix)]

        try:
            b = bytearray(decoded_str.encode("latin-1"))
            length = b[0]
            if length >= 0x80 and len(b) >= 2:
                decoded_str = decoded_str[2 : length + 2]
            else:
                decoded_str = decoded_str[1 : length + 1]
        except (IndexError, ValueError):
            self._log("decode_google_news: length byte parse failed, trying batchexecute")
            return None

        if decoded_str.startswith("AU_yqL") or not decoded_str.startswith(("http://", "https://")):
            return None
        self._log(f"decode_google_news: legacy decoded url={decoded_str[:80]}...")
        return decoded_str

    def _get_article_params(self, base64_id: str) -> dict[str, str] | None:
        """GET article page and parse data-n-a-sg, data-n-a-ts. Return None on failure. Retries on 429."""
        url = f"{self.ARTICLES_BASE}{base64_id}"
        max_attempts = 3
        retry_delay = 5.0
        try:
            for attempt in range(max_attempts):
                with httpx.Client(
                    timeout=self.timeout,
                    follow_redirects=True,
                    verify=self.ssl_verify,
                    headers={"User-Agent": self.user_agent, "Referer": "https://news.google.com/"},
                ) as client:
                    r = client.get(url)
                if r.status_code == 429 and attempt < max_attempts - 1:
                    self._log(f"get_article_params: GET {url} status=429 (rate limited), retry in {retry_delay}s")
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, 15.0)
                    continue
                if not r.is_success:
                    self._log(f"get_article_params: GET {url} status={r.status_code}")
                    return None
                break
            soup = BeautifulSoup(r.text, "lxml")
            div = soup.select_one("c-wiz > div")
            if not div:
                self._log("get_article_params: c-wiz > div not found")
                return None
            signature = (div.get("data-n-a-sg") or "").strip()
            timestamp = (div.get("data-n-a-ts") or "").strip()
            if not signature or not timestamp:
                self._log("get_article_params: missing data-n-a-sg or data-n-a-ts")
                return None
            self._log(f"get_article_params: got signature len={len(signature)}, timestamp={timestamp}")
            return {"signature": signature, "timestamp": timestamp}
        except Exception as e:
            self._log(f"get_article_params: exception {type(e).__name__}: {e}")
            return None

    def _batchexecute(self, base64_id: str, timestamp: str, signature: str) -> str | None:
        """POST 3-arg garturlreq and return decoded URL or None."""
        self._log(f"batchexecute: resolving id len={len(base64_id)}")
        inner = (
            f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
            f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{base64_id}",{timestamp},"{signature}"]'
        )
        req_body = {"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]])}
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "Referer": "https://news.google.com/",
            "User-Agent": self.user_agent,
        }
        try:
            with httpx.Client(
                timeout=self.timeout,
                verify=self.ssl_verify,
                headers=headers,
            ) as client:
                r = client.post(self.BATCHEXECUTE_URL, data=req_body)
            self._log(f"batchexecute: status={r.status_code} (3-arg)")
            if not r.is_success:
                self._log(f"batchexecute: failed {r.text[:200]}")
                return None
            decoded = self._parse_response(r.text)
            if decoded:
                self._log(f"batchexecute: decoded url={decoded[:80]}...")
            return decoded
        except Exception as e:
            self._log(f"batchexecute: exception {type(e).__name__}: {e}")
            return None

    def _parse_response(self, text: str) -> str | None:
        """Parse batchexecute response body; return article URL or None."""
        rest = text.strip()
        if rest.startswith(")]}'\n"):
            rest = rest[5:].lstrip("\n")
        elif rest.startswith(")]}'\r\n"):
            rest = rest[6:].lstrip("\r\n")
        parts = rest.split("\n\n", 1)
        payload = parts[1] if len(parts) > 1 else rest
        if not payload:
            return None
        if payload and payload[0].isdigit():
            idx = payload.find("\n")
            if idx != -1:
                payload = payload[idx + 1 :]
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(data, list) or len(data) < 1:
            return None
        first = data[0]
        if not isinstance(first, list) or len(first) < 3:
            return None
        nested_str = first[2]
        if not isinstance(nested_str, str):
            return None
        try:
            nested = json.loads(nested_str)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(nested, list) or len(nested) < 2:
            return None
        url_val = nested[1]
        if isinstance(url_val, str) and url_val.startswith(("http://", "https://")):
            return url_val
        return None

    def _decode_via_batchexecute(self, base64_id: str) -> str | None:
        """Get params from article page then batchexecute; return URL or None."""
        time.sleep(0.5)  # Throttle to reduce 429 when processing many items
        params = self._get_article_params(base64_id)
        if not params:
            self._log("batchexecute: no article params (signature/timestamp)")
            return None
        return self._batchexecute(base64_id, params["timestamp"], params["signature"])
