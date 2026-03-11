"""
License: MIT
Description: Per-execution context: scratchpad for step results and $step.<id>.<path> resolution.
"""

from __future__ import annotations

import re
from typing import Any


class AgentContext:
    """Isolated context for one agent run; no shared mutable state."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.scratchpad: dict[str, Any] = {}
        self.partial_results: list[Any] = []
        self.replan_count: int = 0

    def store_step_result(self, step_id: int, result: Any) -> None:
        self.scratchpad[str(step_id)] = result

    def resolve_references(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Resolve $step.<id>.<path> from the scratchpad."""
        resolved = {}
        for key, value in arguments.items():
            if isinstance(value, str) and value.startswith("$step."):
                resolved[key] = self._resolve_one(value)
            else:
                resolved[key] = value
        return resolved

    def _resolve_one(self, ref: str) -> Any:
        match = re.match(r"^\$step\.(\d+)\.(.+)$", ref)
        if not match:
            return ref
        step_id, json_path = match.group(1), match.group(2)
        step_data = self.scratchpad.get(step_id)
        if step_data is None:
            return ref
        current = step_data
        for part in json_path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return ref
            if current is None:
                return ref
        return current
