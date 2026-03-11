"""
License: MIT
Description: In-memory plan cache by prompt + skill names; TTL-based expiration.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from .models import AgentPlan


class PlanCache:
    """Thread-safe plan cache with TTL."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[AgentPlan, datetime]] = {}
        self._hit_count = 0
        self._miss_count = 0

    @staticmethod
    def _make_key(prompt: str, skill_names: list[str]) -> str:
        normalized = re.sub(r"\s+", " ", prompt.strip())
        key_input = normalized + "|" + ",".join(sorted(skill_names))
        return hashlib.sha256(key_input.encode("utf-8")).hexdigest()

    def get(self, prompt: str, skill_names: list[str]) -> AgentPlan | None:
        key = self._make_key(prompt, skill_names)
        entry = self._cache.get(key)
        if entry is None:
            self._miss_count += 1
            return None
        plan, created = entry
        age = (datetime.now(timezone.utc) - created).total_seconds()
        if age >= self.ttl_seconds:
            del self._cache[key]
            self._miss_count += 1
            return None
        self._hit_count += 1
        return plan

    def put(self, prompt: str, skill_names: list[str], plan: AgentPlan) -> None:
        key = self._make_key(prompt, skill_names)
        self._cache[key] = (plan, datetime.now(timezone.utc))

    def invalidate(self, prompt: str, skill_names: list[str]) -> None:
        key = self._make_key(prompt, skill_names)
        self._cache.pop(key, None)

    def stats(self) -> dict[str, int]:
        return {
            "total_entries": len(self._cache),
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
        }
