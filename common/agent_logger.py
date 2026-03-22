"""
Structured JSONL logger for autonomous agents.

Every event is a self-contained JSON object written to an append-only file.
Uses the same JSONL pattern as common/http_client.py for consistency.

Usage:
    from common.agent_logger import AgentLogger

    logger = AgentLogger("supervisor")
    logger.log("goal_received", goal_id="g-123", goal="Monitor news")
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.agent_config import AgentConfigLoader

_conf = AgentConfigLoader("supervisor")
_LOG_DIR = Path("./logs")
_lock = threading.Lock()
_loggers: dict[str, logging.Logger] = {}


def _get_file_logger(log_file: str) -> logging.Logger:
    if log_file in _loggers:
        return _loggers[log_file]
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"agent.{log_file}")
    if not logger.handlers:
        handler = logging.FileHandler(_LOG_DIR / log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.propagate = False
    _loggers[log_file] = logger
    return logger


class AgentLogger:
    """Structured JSONL logger for agent components."""

    def __init__(
        self,
        component: str,
        log_file: str | None = None,
    ) -> None:
        self._component = component
        self._log_file = log_file or _conf.get(
            "logging.file", "agent.jsonl",
        )

    def log(
        self,
        event: str,
        *,
        level: str = "info",
        goal_id: str | None = None,
        subagent_id: str | None = None,
        **data: Any,
    ) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "component": self._component,
            "goal_id": goal_id,
            "subagent_id": subagent_id,
            "event": event,
            "data": data,
        }
        line = json.dumps(entry, default=str)
        with _lock:
            try:
                _get_file_logger(self._log_file).info(line)
            except Exception:
                pass
