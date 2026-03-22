"""
Layered memory manager for autonomous agents.

Three layers backed by the storage server:
  - Working: current goal context (scratchpad, conversation)
  - Episodic: past run summaries
  - Semantic: persistent facts, preferences, learned patterns

Usage:
    from common.agent_memory import MemoryManager

    mm = MemoryManager(agent_id="supervisor", storage_url="http://127.0.0.1:7010")
    mm.init_working(goal_id="g-123", goal="Monitor news")
    context = mm.context_window()
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from common.agent_config import AgentConfigLoader
from common.http_client import http_client

_conf = AgentConfigLoader("supervisor")

NS_WORKING = "agent_memory_working"
NS_EPISODIC = "agent_memory_episodic"
NS_SEMANTIC = "agent_memory_semantic"


class WorkingMemory(BaseModel):
    goal_id: str = ""
    goal: str = ""
    plan_objective: str = ""
    scratchpad: dict[str, Any] = Field(default_factory=dict)
    conversation: list[dict[str, str]] = Field(default_factory=list)
    token_count: int = 0


class Episode(BaseModel):
    goal_id: str
    goal_summary: str
    outcome: str
    key_findings: list[str] = Field(default_factory=list)
    skills_used: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )


class SemanticEntry(BaseModel):
    key: str
    value: str
    source_goal_id: str = ""
    confidence: float = 1.0
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


class MemoryManager:
    """Manages all three memory layers for one agent."""

    def __init__(self, agent_id: str, storage_url: str) -> None:
        self.agent_id = agent_id
        self._storage_url = storage_url.rstrip("/")
        self.working = WorkingMemory()

    def _storage_get(self, namespace: str, key: str) -> dict[str, Any] | None:
        url = f"{self._storage_url}/namespaces/{namespace}/records/{key}"
        try:
            with http_client("storage") as client:
                r = client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    return data.get("value") if isinstance(data, dict) else data
        except Exception:
            pass
        return None

    def _storage_put(self, namespace: str, key: str, value: Any) -> bool:
        url = f"{self._storage_url}/namespaces/{namespace}/records/{key}"
        try:
            with http_client("storage") as client:
                r = client.put(url, json={"value": value})
                return r.status_code in {200, 201}
        except Exception:
            return False

    def _storage_list_keys(self, namespace: str) -> list[str]:
        url = f"{self._storage_url}/namespaces/{namespace}/records"
        try:
            with http_client("storage") as client:
                r = client.get(url)
                if r.status_code == 200:
                    return r.json().get("keys") or []
        except Exception:
            pass
        return []

    def _storage_delete(self, namespace: str, key: str) -> bool:
        url = f"{self._storage_url}/namespaces/{namespace}/records/{key}"
        try:
            with http_client("storage") as client:
                r = client.delete(url)
                return r.status_code in {200, 204}
        except Exception:
            return False

    # -- Working Memory --

    def init_working(self, goal_id: str, goal: str) -> None:
        self.working = WorkingMemory(goal_id=goal_id, goal=goal)

    def append_conversation(self, role: str, content: str) -> None:
        self.working.conversation.append({"role": role, "content": content})
        self.working.token_count = _estimate_tokens(
            json.dumps(self.working.conversation, default=str),
        )

    def update_scratchpad(self, key: str, value: Any) -> None:
        self.working.scratchpad[key] = value

    def persist_working(self) -> None:
        key = f"{self.agent_id}:{self.working.goal_id}"
        self._storage_put(NS_WORKING, key, self.working.model_dump())

    def load_working(self, goal_id: str) -> WorkingMemory | None:
        key = f"{self.agent_id}:{goal_id}"
        data = self._storage_get(NS_WORKING, key)
        if data and isinstance(data, dict):
            self.working = WorkingMemory(**data)
            return self.working
        return None

    # -- Episodic Memory --

    def store_episode(self, episode: Episode) -> None:
        key = f"{self.agent_id}:{episode.goal_id}"
        self._storage_put(NS_EPISODIC, key, episode.model_dump())
        max_entries = _conf.get("memory.episodic_max_entries", 100)
        self._trim_episodic(max_entries)

    def get_recent_episodes(self, limit: int | None = None) -> list[Episode]:
        limit = limit or _conf.get("memory.recent_episodes_limit", 5)
        keys = self._storage_list_keys(NS_EPISODIC)
        agent_keys = [
            k for k in keys if k.startswith(f"{self.agent_id}:")
        ]
        episodes: list[Episode] = []
        for key in agent_keys:
            data = self._storage_get(NS_EPISODIC, key)
            if data and isinstance(data, dict):
                try:
                    episodes.append(Episode(**data))
                except Exception:
                    pass
        episodes.sort(key=lambda e: e.timestamp, reverse=True)
        return episodes[:limit]

    def _trim_episodic(self, max_entries: int) -> None:
        keys = self._storage_list_keys(NS_EPISODIC)
        agent_keys = [
            k for k in keys if k.startswith(f"{self.agent_id}:")
        ]
        if len(agent_keys) <= max_entries:
            return
        episodes_with_keys: list[tuple[str, str]] = []
        for key in agent_keys:
            data = self._storage_get(NS_EPISODIC, key)
            ts = ""
            if data and isinstance(data, dict):
                ts = data.get("timestamp", "")
            episodes_with_keys.append((key, ts))
        episodes_with_keys.sort(key=lambda x: x[1])
        to_remove = len(episodes_with_keys) - max_entries
        for key, _ in episodes_with_keys[:to_remove]:
            self._storage_delete(NS_EPISODIC, key)

    # -- Semantic Memory --

    def store_semantic(self, entry: SemanticEntry) -> None:
        key = f"{self.agent_id}:{entry.key}"
        self._storage_put(NS_SEMANTIC, key, entry.model_dump())

    def get_semantic(self, key: str) -> SemanticEntry | None:
        full_key = f"{self.agent_id}:{key}"
        data = self._storage_get(NS_SEMANTIC, full_key)
        if data and isinstance(data, dict):
            try:
                return SemanticEntry(**data)
            except Exception:
                pass
        return None

    def get_all_semantic(
        self, limit: int | None = None,
    ) -> list[SemanticEntry]:
        limit = limit or _conf.get("memory.semantic_max_entries", 500)
        keys = self._storage_list_keys(NS_SEMANTIC)
        agent_keys = [
            k for k in keys if k.startswith(f"{self.agent_id}:")
        ]
        entries: list[SemanticEntry] = []
        for key in agent_keys[:limit]:
            data = self._storage_get(NS_SEMANTIC, key)
            if data and isinstance(data, dict):
                try:
                    entries.append(SemanticEntry(**data))
                except Exception:
                    pass
        return entries

    def clear_all(self) -> int:
        """Delete all memory for this agent. Returns count of deleted records."""
        deleted = 0
        for ns in (NS_WORKING, NS_EPISODIC, NS_SEMANTIC):
            keys = self._storage_list_keys(ns)
            agent_keys = [
                k for k in keys if k.startswith(f"{self.agent_id}:")
            ]
            for key in agent_keys:
                if self._storage_delete(ns, key):
                    deleted += 1
        return deleted

    # -- Context Window Assembly --

    def context_window(self) -> str:
        """Build a context string from all memory layers for planning prompts."""
        parts: list[str] = []

        semantic_limit = _conf.get("memory.semantic_relevance_limit", 10)
        semantic_entries = self.get_all_semantic(limit=semantic_limit)
        if semantic_entries:
            lines = [f"- {s.value}" for s in semantic_entries]
            parts.append("## Known Facts\n" + "\n".join(lines))

        episodes_limit = _conf.get("memory.recent_episodes_limit", 5)
        episodes = self.get_recent_episodes(limit=episodes_limit)
        if episodes:
            lines = []
            for e in episodes:
                findings = "; ".join(e.key_findings[:3])
                lines.append(f"- [{e.outcome}] {e.goal_summary}: {findings}")
            parts.append("## Recent Experience\n" + "\n".join(lines))

        if self.working.conversation:
            working_text = "\n".join(
                f"{c['role']}: {c['content']}"
                for c in self.working.conversation
            )
            parts.append("## Current Context\n" + working_text)

        return "\n\n".join(parts)
