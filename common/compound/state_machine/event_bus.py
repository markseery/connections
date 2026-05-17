from __future__ import annotations

import logging
from typing import Any, Callable

from .config_loader import OrchestratorConfig
from .models import StateChangeEvent
from .subscribers import dispatch_subscribers

_logger = logging.getLogger(__name__)

Handler = Callable[[StateChangeEvent], None]


class EventBus:
    def __init__(self, config: OrchestratorConfig) -> None:
        self._config = config
        self._handlers: list[Handler] = []

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def publish(self, event: StateChangeEvent) -> None:
        _logger.info(
            "state.changed %s %s %s -> %s",
            event.machine_id,
            event.dimension,
            event.old_state_id,
            event.new_state_id,
        )
        for h in self._handlers:
            try:
                h(event)
            except Exception as exc:
                _logger.exception("state handler failed: %s", exc)
        dispatch_subscribers(self._config, event)
