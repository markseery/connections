"""
License: MIT
Description: `@monitor` decorator for monitoring methods/functions.

Logs start/finish timing and duration, and records exceptions with full stack traces
to `./logs/monitor.log`. Can decorate a class (wraps all methods) or a single callable.
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
import traceback
from pathlib import Path
from typing import Any, Callable, TypeVar, overload


_LOG_DIR = Path("./logs")
_LOG_FILE = _LOG_DIR / "monitor.log"


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("monitor")
    if logger.handlers:
        return logger

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s pid=%(process)d %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


F = TypeVar("F", bound=Callable[..., Any])
T = TypeVar("T", bound=type)


def _qualname(fn: Callable[..., Any]) -> str:
    mod = getattr(fn, "__module__", None) or "<unknown>"
    qn = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", "<anonymous>")
    return f"{mod}.{qn}"


def _wrap_callable(fn: F) -> F:
    logger = _get_logger()
    name = _qualname(fn)

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def _async_wrapped(*args: Any, **kwargs: Any) -> Any:
            start_wall = time.time()
            start = time.perf_counter()
            logger.info("start name=%s ts=%s", name, start_wall)
            try:
                result = await fn(*args, **kwargs)
                return result
            except BaseException as e:  # noqa: BLE001 - intentionally catch all
                logger.error(
                    "exception name=%s type=%s msg=%s\n%s",
                    name,
                    type(e).__name__,
                    str(e),
                    "".join(traceback.format_exception(type(e), e, e.__traceback__)),
                )
                raise
            finally:
                finish_wall = time.time()
                dur_ms = (time.perf_counter() - start) * 1000.0
                logger.info(
                    "finish name=%s ts=%s duration_ms=%.3f",
                    name,
                    finish_wall,
                    dur_ms,
                )

        return _async_wrapped  # type: ignore[return-value]

    @functools.wraps(fn)
    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        start_wall = time.time()
        start = time.perf_counter()
        logger.info("start name=%s ts=%s", name, start_wall)
        try:
            return fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 - intentionally catch all
            logger.error(
                "exception name=%s type=%s msg=%s\n%s",
                name,
                type(e).__name__,
                str(e),
                "".join(traceback.format_exception(type(e), e, e.__traceback__)),
            )
            raise
        finally:
            finish_wall = time.time()
            dur_ms = (time.perf_counter() - start) * 1000.0
            logger.info(
                "finish name=%s ts=%s duration_ms=%.3f",
                name,
                finish_wall,
                dur_ms,
            )

    return _wrapped  # type: ignore[return-value]


def _decorate_class(cls: T) -> T:
    # Wrap instance methods + @staticmethod/@classmethod, skip dunders.
    for attr_name, attr_value in list(cls.__dict__.items()):
        if attr_name.startswith("__") and attr_name.endswith("__"):
            continue

        if isinstance(attr_value, staticmethod):
            fn = attr_value.__func__
            setattr(cls, attr_name, staticmethod(_wrap_callable(fn)))
            continue

        if isinstance(attr_value, classmethod):
            fn = attr_value.__func__
            setattr(cls, attr_name, classmethod(_wrap_callable(fn)))
            continue

        if inspect.isfunction(attr_value):
            setattr(cls, attr_name, _wrap_callable(attr_value))
            continue

    return cls


@overload
def monitor(obj: T) -> T: ...


@overload
def monitor(obj: F) -> F: ...


def monitor(obj: Any) -> Any:
    """
    Usage:
      - Apply to a class to monitor *all* methods:
            @monitor
            class Service: ...

      - Apply to a single method/function:
            @monitor
            def do_work(...): ...
    """
    if inspect.isclass(obj):
        return _decorate_class(obj)
    if callable(obj):
        return _wrap_callable(obj)
    raise TypeError("@monitor can only decorate a class or callable")


def monitor_fastapi_app(app: Any) -> None:
    """
    Wrap all FastAPI route endpoints with the monitor wrapper.

    This applies the same monitoring behavior as @monitor without needing to
    decorate every route function manually.
    """
    try:
        from fastapi.routing import APIRoute  # type: ignore
    except Exception:
        APIRoute = None  # type: ignore

    for route in getattr(app, "router", getattr(app, "routes", None)).routes if getattr(app, "router", None) else []:
        endpoint = getattr(route, "endpoint", None)
        if not callable(endpoint):
            continue
        # Avoid double-wrapping.
        if getattr(endpoint, "__wrapped__", None) is not None:
            continue
        # Only wrap FastAPI API routes when we can identify them.
        if APIRoute is not None and not isinstance(route, APIRoute):
            continue
        route.endpoint = _wrap_callable(endpoint)

