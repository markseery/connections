from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .config_loader import OrchestratorConfig
from .engine import StateEngine

_logger = logging.getLogger(__name__)


class StateScheduler:
    def __init__(
        self,
        engine: StateEngine,
        config: OrchestratorConfig,
        *,
        on_tick: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.engine = engine
        self.config = config
        self._on_tick = on_tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def tick_sec(self) -> float:
        return float(self.config.get("scheduler", "tick_sec") or 60)

    @property
    def max_concurrent(self) -> int:
        return max(1, int(self.config.get("scheduler", "max_concurrent_pulls") or 4))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="state-scheduler", daemon=True)
        self._thread.start()
        _logger.info("state scheduler started (tick_sec=%s)", self.tick_sec)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.tick_sec + 5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                refreshed = self.engine.refresh_due(max_machines=self.max_concurrent)
                if self._on_tick:
                    self._on_tick(refreshed)
            except Exception as exc:
                _logger.exception("scheduler tick failed: %s", exc)
            self._stop.wait(self.tick_sec)

    def tick_once(self) -> list[str]:
        return self.engine.refresh_due(max_machines=self.max_concurrent)
