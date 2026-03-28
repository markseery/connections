"""
Instrumented HTTP client factories for sync and async usage.

Every request logs: start timestamp, method, URL, timeout category,
elapsed time, status code, and a truncated summary of the request body.
Logs are written as JSON lines to ./logs/http_calls.jsonl for analytics
(mean/peak latency by endpoint, timeout category, time window, etc.).

Usage (sync):
    from common.http_client import http_client
    with http_client("ai_generate") as client:
        r = client.post(url, json=payload)

Usage (async):
    from common.http_client import async_http_client
    async with async_http_client("content_fetch") as client:
        r = await client.get(url)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from common.simple.timeouts import get as _timeout
from common.simple.user_dir import resolve_logs

_LOG_DIR = Path(os.environ.get("HTTP_LOG_DIR", str(resolve_logs())))
_LOG_FILE = _LOG_DIR / "http_calls.jsonl"
_MAX_BODY_LOG = 256

_lock = threading.Lock()
_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("http_calls")
    if not logger.handlers:
        handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.propagate = False

    _logger = logger
    return _logger


def _truncate_body(body: Any) -> str | None:
    if body is None:
        return None
    if isinstance(body, (bytes, memoryview)):
        return None
    try:
        text = json.dumps(body, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(body)
    if len(text) > _MAX_BODY_LOG:
        return text[:_MAX_BODY_LOG] + "…"
    return text


def _log_entry(
    *,
    category: str,
    method: str,
    url: str,
    status: int | None,
    elapsed_ms: float,
    error: str | None = None,
    body_summary: str | None = None,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "method": method,
        "url": url,
        "status": status,
        "elapsed_ms": round(elapsed_ms, 2),
    }
    if error:
        entry["error"] = error
    if body_summary:
        entry["body"] = body_summary
    try:
        _get_logger().info(json.dumps(entry, default=str, ensure_ascii=False))
    except Exception:
        pass


def _ensure_extensions(request: httpx.Request) -> dict:
    if request.extensions is None:
        request.extensions = {}
    return request.extensions


def _on_request(request: httpx.Request) -> None:
    _ensure_extensions(request)["_start"] = time.perf_counter()


def _on_response(response: httpx.Response) -> None:
    ext = response.request.extensions or {}
    start = ext.get("_start")
    if start is not None:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _log_entry(
            category=ext.get("_category", ""),
            method=response.request.method,
            url=str(response.request.url),
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            body_summary=ext.get("_body_summary"),
        )


async def _on_request_async(request: httpx.Request) -> None:
    _ensure_extensions(request)["_start"] = time.perf_counter()


async def _on_response_async(response: httpx.Response) -> None:
    ext = response.request.extensions or {}
    start = ext.get("_start")
    if start is not None:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _log_entry(
            category=ext.get("_category", ""),
            method=response.request.method,
            url=str(response.request.url),
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            body_summary=ext.get("_body_summary"),
        )


class _InstrumentedClient(httpx.Client):
    """Sync client that tags every request with its timeout category and body summary."""

    def __init__(self, category: str, **kwargs: Any) -> None:
        self._category = category
        super().__init__(**kwargs)

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        ext = _ensure_extensions(request)
        ext["_category"] = self._category
        if "_body_summary" not in ext:
            ext["_body_summary"] = _truncate_body(ext.get("_json_body"))
        t0 = time.perf_counter()
        try:
            response = super().send(request, **kwargs)
            return response
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            _log_entry(
                category=self._category,
                method=request.method,
                url=str(request.url),
                status=None,
                elapsed_ms=elapsed_ms,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def request(self, method: str, url: Any, *, json: Any = None, **kwargs: Any) -> httpx.Response:
        if json is not None:
            ext = kwargs.get("extensions")
            if ext is None:
                ext = {}
                kwargs["extensions"] = ext
            ext["_json_body"] = json
            ext["_body_summary"] = _truncate_body(json)
        return super().request(method, url, json=json, **kwargs)


class _InstrumentedAsyncClient(httpx.AsyncClient):
    """Async client that tags every request with its timeout category and body summary."""

    def __init__(self, category: str, **kwargs: Any) -> None:
        self._category = category
        super().__init__(**kwargs)

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        ext = _ensure_extensions(request)
        ext["_category"] = self._category
        if "_body_summary" not in ext:
            ext["_body_summary"] = _truncate_body(ext.get("_json_body"))
        t0 = time.perf_counter()
        try:
            response = await super().send(request, **kwargs)
            return response
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            _log_entry(
                category=self._category,
                method=request.method,
                url=str(request.url),
                status=None,
                elapsed_ms=elapsed_ms,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    async def request(self, method: str, url: Any, *, json: Any = None, **kwargs: Any) -> httpx.Response:
        if json is not None:
            ext = kwargs.get("extensions")
            if ext is None:
                ext = {}
                kwargs["extensions"] = ext
            ext["_json_body"] = json
            ext["_body_summary"] = _truncate_body(json)
        return await super().request(method, url, json=json, **kwargs)


@contextmanager
def http_client(
    category: str = "inter_service",
    *,
    timeout: float | None = None,
    **kwargs: Any,
):
    """Context manager yielding an instrumented sync httpx.Client.

    ``category`` selects the timeout from app_config.yaml (e.g. "ai_generate",
    "storage", "registry").  Override with ``timeout`` if needed.
    """
    t = timeout if timeout is not None else _timeout(category)
    client = _InstrumentedClient(
        category,
        timeout=t,
        event_hooks={"request": [_on_request], "response": [_on_response]},
        **kwargs,
    )
    try:
        yield client
    finally:
        client.close()


@asynccontextmanager
async def async_http_client(
    category: str = "inter_service",
    *,
    timeout: float | None = None,
    **kwargs: Any,
):
    """Context manager yielding an instrumented async httpx.AsyncClient.

    ``category`` selects the timeout from app_config.yaml.
    """
    t = timeout if timeout is not None else _timeout(category)
    client = _InstrumentedAsyncClient(
        category,
        timeout=t,
        event_hooks={"request": [_on_request_async], "response": [_on_response_async]},
        **kwargs,
    )
    try:
        yield client
    finally:
        await client.aclose()
