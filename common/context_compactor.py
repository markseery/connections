"""
Context compaction for autonomous agents.

When working memory exceeds a token threshold, older conversation entries
are summarised via AI and replaced with a compact summary. Recent steps
are preserved verbatim.

Usage:
    from common.context_compactor import ContextCompactor

    compactor = ContextCompactor(aiserver_url="http://127.0.0.1:7012")
    compactor.compact_if_needed(memory_manager)
"""

from __future__ import annotations

from typing import Any

from common.agent_config import AgentConfigLoader
from common.agent_logger import AgentLogger
from common.http_client import http_client

_conf = AgentConfigLoader("supervisor")
_logger = AgentLogger("context_compactor")


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


class ContextCompactor:
    def __init__(self, aiserver_url: str) -> None:
        self._aiserver_url = aiserver_url.rstrip("/")

    def compact_if_needed(
        self,
        working_memory: Any,
        *,
        goal_id: str | None = None,
    ) -> bool:
        """Compact working memory conversation if over threshold.

        Returns True if compaction was performed.
        """
        threshold = _conf.get("memory.compaction_threshold", 20000)
        if working_memory.token_count <= threshold:
            return False

        recent_keep = _conf.get("compaction.recent_steps_to_keep", 3)
        conversation = working_memory.conversation
        if len(conversation) <= recent_keep:
            return False

        old_tokens = working_memory.token_count
        recent_steps = conversation[-recent_keep:]
        older_steps = conversation[:-recent_keep]

        summary = self._summarise_via_ai(older_steps)
        compacted_entry = {
            "role": "system",
            "content": f"[Compacted context summary]\n{summary}",
        }

        working_memory.conversation = [compacted_entry] + recent_steps
        import json
        working_memory.token_count = _estimate_tokens(
            json.dumps(working_memory.conversation, default=str),
        )

        _logger.log(
            "memory_compacted",
            goal_id=goal_id,
            old_tokens=old_tokens,
            new_tokens=working_memory.token_count,
            older_entries_summarised=len(older_steps),
        )
        return True

    def _summarise_via_ai(self, entries: list[dict[str, str]]) -> str:
        text = "\n".join(
            f"{e.get('role', 'unknown')}: {e.get('content', '')}"
            for e in entries
        )
        max_chars = _conf.get("compaction.summarise_max_chars", 30000)
        text = text[:max_chars]

        ai_profile = _conf.get("compaction.ai_profile", "fast")
        prompt = (
            "Summarise the following agent execution context into a concise "
            "paragraph. Preserve key facts, decisions, results, and any "
            "values that downstream steps may need. Be terse.\n\n"
            f"{text}"
        )

        try:
            with http_client("ai_generate") as client:
                r = client.post(
                    f"{self._aiserver_url}/generate",
                    json={"prompt": prompt, "profile": ai_profile},
                )
                r.raise_for_status()
                data = r.json() or {}
            output = data.get("output")
            if isinstance(output, dict):
                return output.get("text", "") or ""
            return str(output) if output else ""
        except Exception as exc:
            _logger.log(
                "compaction_ai_failed",
                level="error",
                error=str(exc),
            )
            return "(Compaction failed — original context truncated)"

    def create_episode_summary(
        self,
        working_memory: Any,
    ) -> str:
        """Create a one-paragraph summary of the full run for episodic storage."""
        entries = working_memory.conversation
        if not entries:
            return ""
        return self._summarise_via_ai(entries)
