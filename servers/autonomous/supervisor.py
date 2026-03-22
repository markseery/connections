"""
Supervisor agent: receives goals, decomposes into subgoals, spawns subagents,
monitors progress, handles approval gates, aggregates results, and manages memory.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from common.agent_config import AgentConfigLoader
from common.agent_logger import AgentLogger
from common.agent_memory import Episode, MemoryManager
from common.approval_gate import ApprovalGate, ApprovalPolicy
from common.context_compactor import ContextCompactor
from common.http_client import http_client
from common.json_repair import parse_llm_json
from servers.agent.config import get_aiserver_url
from servers.agent.skill_discovery import SkillDefinition, discover_skills

from .models import (
    AgentConfig,
    GoalState,
    GoalStatus,
    Subgoal,
    SubagentState,
    SubagentStatus,
    SupervisorPlan,
)
from .subagent import SubagentRunner

_conf = AgentConfigLoader("supervisor")
_logger = AgentLogger("supervisor")

GOAL_DECOMPOSITION_PROMPT = """You are a supervisor agent. Decompose the user's goal into subgoals.

IMPORTANT: You may ONLY use these exact agent types:
{agent_types}

Do NOT invent agent type names. If a type is not listed above, it DOES NOT EXIST.
If none of the types can handle a part of the goal, omit that subgoal.

Memory context (if any):
{memory_context}

Return ONLY valid JSON:
{{"objective":"<goal summary>","subgoals":[{{"subgoal_id":1,"description":"<what to do>","agent_type":"<type from list>","reason":"<why>","depends_on":[],"arguments":{{}}}}]}}

RULES:
- agent_type MUST be copied exactly from the list above. No variations, no abbreviations.
- Keep subgoals to the MINIMUM needed. One subgoal is often enough.
- If the goal needs information the agent doesn't have (e.g. "which companies are X"),
  include that research in the subgoal description so the agent can look it up.
- Do NOT split into separate "search" and "act" subgoals when one agent can do both.
  Example: "find neocloud stocks and get prices" = ONE research_subagent subgoal,
  NOT a separate search step + price step.
- Do NOT add actions the user did not ask for (no emails, no notifications unless asked).
- Only use notify_subagent if the user EXPLICITLY asks for a notification or email.
- Return ONLY JSON, no markdown.

Goal: {goal}
"""


class GoalStore:
    """Thread-safe in-memory goal store."""

    def __init__(self) -> None:
        self._goals: dict[str, GoalState] = {}
        self._lock = threading.Lock()

    def get(self, goal_id: str) -> GoalState | None:
        with self._lock:
            return self._goals.get(goal_id)

    def put(self, goal: GoalState) -> None:
        with self._lock:
            self._goals[goal.goal_id] = goal

    def update(self, goal_id: str, **fields: Any) -> None:
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal:
                for k, v in fields.items():
                    if hasattr(goal, k):
                        setattr(goal, k, v)

    def list_all(self) -> list[GoalState]:
        with self._lock:
            return list(self._goals.values())


def _get_storage_url() -> str:
    from common.registry_client import get_server_url
    return get_server_url("storage")


def _load_agent_types() -> dict[str, dict[str, Any]]:
    """Load all subagent type configs from config/agents/."""
    from pathlib import Path
    import yaml

    config_dir = Path(__file__).resolve().parent.parent.parent / "config" / "agents"
    types: dict[str, dict[str, Any]] = {}
    if not config_dir.is_dir():
        return types
    for f in config_dir.iterdir():
        if f.suffix == ".yaml" and f.stem != "supervisor":
            try:
                with open(f) as fh:
                    raw = yaml.safe_load(fh)
                if isinstance(raw, dict):
                    types[f.stem] = raw
            except Exception:
                pass
    return types


class SupervisorAgent:
    def __init__(
        self,
        goal_store: GoalStore | None = None,
    ) -> None:
        self.goal_store = goal_store or GoalStore()

    def submit_goal(
        self,
        goal: str,
        config: AgentConfig | None = None,
    ) -> GoalState:
        goal_id = f"g-{uuid.uuid4().hex[:12]}"
        cfg = config or AgentConfig()
        state = GoalState(goal_id=goal_id, goal=goal, config=cfg)
        self.goal_store.put(state)

        _logger.log(
            "goal_received",
            goal_id=goal_id,
            goal=goal,
            policy=cfg.approval_policy.value,
            timeout=cfg.timeout,
        )

        thread = threading.Thread(
            target=self._run_goal,
            args=(goal_id,),
            daemon=True,
        )
        thread.start()

        return state

    def get_goal(self, goal_id: str) -> GoalState | None:
        return self.goal_store.get(goal_id)

    def list_goals(self) -> list[GoalState]:
        return self.goal_store.list_all()

    def cancel_goal(self, goal_id: str) -> GoalState | None:
        goal = self.goal_store.get(goal_id)
        if not goal:
            return None
        if goal.status in (GoalStatus.completed, GoalStatus.failed):
            return goal
        self.goal_store.update(
            goal_id,
            status=GoalStatus.cancelled,
            completed_at=_now_iso(),
        )
        _logger.log("goal_cancelled", goal_id=goal_id)
        return self.goal_store.get(goal_id)

    def _run_goal(self, goal_id: str) -> None:
        goal = self.goal_store.get(goal_id)
        if not goal:
            return

        start_time = time.monotonic()
        storage_url = _get_storage_url()
        memory = MemoryManager(agent_id="supervisor", storage_url=storage_url)
        memory.init_working(goal_id=goal_id, goal=goal.goal)
        compactor = ContextCompactor(aiserver_url=get_aiserver_url())
        gate = ApprovalGate(
            policy=goal.config.approval_policy,
            storage_url=storage_url,
        )

        try:
            self.goal_store.update(goal_id, status=GoalStatus.planning)
            context_text = memory.context_window()

            plan = self._decompose_goal(goal.goal, context_text)
            self.goal_store.update(goal_id, plan=plan)

            _logger.log(
                "plan_created",
                goal_id=goal_id,
                objective=plan.objective,
                subgoal_count=len(plan.subgoals),
            )

            if not plan.subgoals:
                answer = self._direct_ai_answer(goal.goal, context_text)
                self.goal_store.update(
                    goal_id,
                    status=GoalStatus.completed,
                    completed_at=_now_iso(),
                    answer=answer,
                )
                _logger.log("goal_completed", goal_id=goal_id, reason="direct_answer")
                self._store_episode(memory, compactor, goal, answer, start_time)
                return

            valid_types = set(_load_agent_types().keys())
            plan.subgoals = [
                sg for sg in plan.subgoals if sg.agent_type in valid_types
            ]
            if not plan.subgoals:
                answer = self._direct_ai_answer(goal.goal, context_text)
                self.goal_store.update(
                    goal_id,
                    status=GoalStatus.completed,
                    completed_at=_now_iso(),
                    answer=answer,
                )
                _logger.log(
                    "goal_completed", goal_id=goal_id,
                    reason="no_valid_subgoals_direct_answer",
                )
                self._store_episode(memory, compactor, goal, answer, start_time)
                return

            max_subagents = goal.config.max_subagents
            if len(plan.subgoals) > max_subagents:
                plan.subgoals = plan.subgoals[:max_subagents]
                self.goal_store.update(goal_id, plan=plan)

            self.goal_store.update(goal_id, status=GoalStatus.running)
            shared_context: dict[str, Any] = {}
            subagent_states: list[SubagentState] = []
            waves = self._resolve_waves(plan.subgoals)

            for wave in waves:
                if goal.config.timeout > 0:
                    elapsed = time.monotonic() - start_time
                    if elapsed >= goal.config.timeout:
                        self.goal_store.update(
                            goal_id,
                            status=GoalStatus.failed,
                            error="Goal timeout exceeded",
                            completed_at=_now_iso(),
                        )
                        _logger.log(
                            "goal_failed", goal_id=goal_id,
                            reason="timeout", elapsed=elapsed,
                        )
                        return

                wave_states: list[SubagentState] = []
                for subgoal in wave:
                    runner = SubagentRunner(
                        agent_type=subgoal.agent_type,
                        approval_gate=gate,
                        shared_context=shared_context,
                    )
                    sa_state = runner.run(subgoal, goal_id=goal_id)
                    wave_states.append(sa_state)
                    subagent_states.append(sa_state)
                    self.goal_store.update(goal_id, subagents=subagent_states)

                    if sa_state.result:
                        key = f"subagent_{subgoal.subgoal_id}"
                        shared_context[key] = sa_state.result.get("answer", "")
                        memory.append_conversation(
                            "subagent",
                            f"[{subgoal.agent_type}] {sa_state.result.get('answer', '')}",
                        )

                compactor.compact_if_needed(
                    memory.working, goal_id=goal_id,
                )

            all_failed = all(
                s.status == SubagentStatus.failed for s in subagent_states
            )
            any_failed = any(
                s.status == SubagentStatus.failed for s in subagent_states
            )

            if all_failed:
                errors = [s.error or "unknown" for s in subagent_states]
                self.goal_store.update(
                    goal_id,
                    status=GoalStatus.failed,
                    error="; ".join(errors),
                    completed_at=_now_iso(),
                )
                _logger.log(
                    "goal_failed", goal_id=goal_id,
                    reason="all_subagents_failed",
                )
                self._store_episode(
                    memory, compactor, goal, None, start_time,
                    outcome="failed",
                )
                return

            answer = self._aggregate_results(
                goal.goal, subagent_states, shared_context,
            )

            status = GoalStatus.completed
            self.goal_store.update(
                goal_id,
                status=status,
                answer=answer,
                completed_at=_now_iso(),
            )

            _logger.log(
                "goal_completed",
                goal_id=goal_id,
                subagent_count=len(subagent_states),
                any_partial=any_failed,
            )
            outcome = "partial" if any_failed else "completed"
            self._store_episode(
                memory, compactor, goal, answer, start_time, outcome=outcome,
            )

        except Exception as exc:
            self.goal_store.update(
                goal_id,
                status=GoalStatus.failed,
                error=str(exc),
                completed_at=_now_iso(),
            )
            _logger.log(
                "goal_failed",
                level="error",
                goal_id=goal_id,
                error=str(exc),
            )

    def _decompose_goal(
        self, goal: str, memory_context: str,
    ) -> SupervisorPlan:
        agent_types = _load_agent_types()
        type_lines: list[str] = []
        for name, cfg in agent_types.items():
            desc = cfg.get("description", name)
            skills_list = cfg.get("skills", [])
            skills_note = f" (skills: {', '.join(skills_list)})" if skills_list else " (all skills)"
            type_lines.append(f"- \"{name}\": {desc}{skills_note}")

        prompt = GOAL_DECOMPOSITION_PROMPT.format(
            agent_types="\n".join(type_lines) or "(none)",
            memory_context=memory_context or "(none)",
            goal=goal,
        )

        aiserver_url = get_aiserver_url().rstrip("/")
        ai_profile = _conf.get("compaction.ai_profile", "fast")
        try:
            with http_client("ai_generate") as client:
                r = client.post(
                    f"{aiserver_url}/generate",
                    json={"prompt": prompt, "profile": ai_profile},
                )
                r.raise_for_status()
                data = r.json() or {}
        except Exception as exc:
            _logger.log(
                "decomposition_failed", level="error", error=str(exc),
            )
            return SupervisorPlan(objective=goal, subgoals=[])

        output = data.get("output")
        text = ""
        if isinstance(output, dict):
            text = output.get("text", "") or ""
        elif output:
            text = str(output)

        if not text:
            return SupervisorPlan(objective=goal, subgoals=[])

        raw = parse_llm_json(text)
        subgoals: list[Subgoal] = []
        for sg in raw.get("subgoals") or []:
            if isinstance(sg, dict):
                deps = sg.get("depends_on") or []
                sg["depends_on"] = [
                    int(x) for x in deps if isinstance(x, (int, float))
                ]
                try:
                    subgoals.append(Subgoal(**sg))
                except Exception:
                    pass

        return SupervisorPlan(
            objective=raw.get("objective", goal),
            subgoals=subgoals,
        )

    def _direct_ai_answer(self, goal: str, context: str) -> str:
        aiserver_url = get_aiserver_url().rstrip("/")
        prompt = (
            "Answer the following goal directly and thoroughly. "
            "Provide key facts, names, numbers, and any relevant details.\n\n"
        )
        if context:
            prompt += f"Context:\n{context}\n\n"
        prompt += f"Goal: {goal}"
        ai_profile = _conf.get("direct_answer.ai_profile", "default")
        try:
            with http_client("ai_generate") as client:
                r = client.post(
                    f"{aiserver_url}/generate",
                    json={"prompt": prompt, "profile": ai_profile},
                )
                r.raise_for_status()
                data = r.json() or {}
            output = data.get("output")
            if isinstance(output, dict):
                return output.get("text", "") or ""
            return str(output) if output else ""
        except Exception:
            return ""

    def _aggregate_results(
        self,
        goal: str,
        subagent_states: list[SubagentState],
        shared_context: dict[str, Any],
    ) -> str:
        _empty_phrases = {"no steps needed.", "no steps needed", ""}
        raw_parts: list[str] = []
        for sa in subagent_states:
            if sa.result:
                answer = sa.result.get("answer", "")
                if answer and answer.strip().lower() not in _empty_phrases:
                    raw_parts.append(answer)
            elif sa.error:
                raw_parts.append(f"[{sa.agent_type}] FAILED: {sa.error}")

        if not raw_parts:
            return self._direct_ai_answer(goal, "")

        combined = "\n\n".join(raw_parts)
        max_chars = _conf.get("compaction.summarise_max_chars", 30000)
        if len(combined) < 200:
            return combined

        return self._synthesise_answer(goal, combined[:max_chars])

    def _synthesise_answer(self, goal: str, raw_content: str) -> str:
        """Run a final AI pass to produce a coherent answer from subagent outputs."""
        ai_profile = _conf.get("compaction.ai_profile", "fast")
        aiserver_url = get_aiserver_url().rstrip("/")
        prompt = (
            "You are an assistant synthesising results from multiple agents. "
            "The user's original goal was:\n\n"
            f"{goal}\n\n"
            "Below are the raw findings from the agents. Produce a clear, "
            "well-structured summary that directly addresses the goal. "
            "Include key facts, names, numbers, and sources. Be thorough "
            "but concise.\n\n"
            f"{raw_content}"
        )

        try:
            with http_client("ai_generate") as client:
                r = client.post(
                    f"{aiserver_url}/generate",
                    json={"prompt": prompt, "profile": ai_profile},
                )
                r.raise_for_status()
                data = r.json() or {}
            output = data.get("output")
            if isinstance(output, dict):
                return output.get("text", "") or raw_content
            return str(output) if output else raw_content
        except Exception as exc:
            _logger.log(
                "synthesis_failed", level="warning", error=str(exc),
            )
            return raw_content

    def _store_episode(
        self,
        memory: MemoryManager,
        compactor: ContextCompactor,
        goal: GoalState,
        answer: str | None,
        start_time: float,
        *,
        outcome: str = "completed",
    ) -> None:
        duration = time.monotonic() - start_time
        skills_used: list[str] = []
        findings: list[str] = []

        for sa in goal.subagents:
            if sa.result:
                res_answer = sa.result.get("answer", "")
                if res_answer:
                    findings.append(res_answer[:200])
                step_results = sa.result.get("step_results") or []
                for sr in step_results:
                    if isinstance(sr, dict):
                        sn = sr.get("skill_name", "")
                        if sn and sn not in skills_used:
                            skills_used.append(sn)

        episode = Episode(
            goal_id=goal.goal_id,
            goal_summary=goal.goal[:200],
            outcome=outcome,
            key_findings=findings[:10],
            skills_used=skills_used,
            duration_seconds=round(duration, 1),
        )
        memory.store_episode(episode)
        memory.persist_working()

    def _resolve_waves(
        self, subgoals: list[Subgoal],
    ) -> list[list[Subgoal]]:
        completed: set[int] = set()
        waves: list[list[Subgoal]] = []
        remaining = list(subgoals)
        while remaining:
            wave = [
                s for s in remaining
                if all(d in completed for d in s.depends_on)
            ]
            if not wave:
                wave = remaining
                remaining = []
            else:
                remaining = [s for s in remaining if s not in wave]
            waves.append(wave)
            for s in wave:
                completed.add(s.subgoal_id)
        return waves


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
