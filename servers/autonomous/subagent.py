"""
Scoped subagent: wraps the existing AgentService with skill filtering,
a custom planning prompt addendum, and budget constraints from config.

Each subagent type is defined by config/agents/<type>.yaml.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from common.compound.agent_config import AgentConfigLoader
from common.compound.agent_logger import AgentLogger
from common.complex.approval_gate import ApprovalGate
from common.compound.http_client import http_client
from servers.agent.config import get_aiserver_url, get_config_server_url, get_registry_url
from servers.agent.context import AgentContext
from servers.agent.executor import execute_plan
from servers.agent.models import (
    AgentExecutionRequest,
    AgentPlan,
    PlannedStep,
    StepResult,
)
from servers.agent.planner import create_plan
from servers.agent.skill_discovery import SkillDefinition, discover_skills

from .models import Subgoal, SubagentState, SubagentStatus

_logger = AgentLogger("subagent")


class SubagentRunner:
    """Runs a scoped subagent for a single subgoal."""

    def __init__(
        self,
        agent_type: str,
        approval_gate: ApprovalGate,
        shared_context: dict[str, Any] | None = None,
    ) -> None:
        self._type_conf = AgentConfigLoader(agent_type)
        self._agent_type = agent_type
        self._approval_gate = approval_gate
        self._shared_context = shared_context or {}

    def run(
        self,
        subgoal: Subgoal,
        *,
        goal_id: str,
    ) -> SubagentState:
        subagent_id = str(uuid.uuid4())
        state = SubagentState(
            subagent_id=subagent_id,
            subgoal=subgoal,
            agent_type=self._agent_type,
            status=SubagentStatus.planning,
            started_at=_now_iso(),
        )

        _logger.log(
            "subagent_spawned",
            goal_id=goal_id,
            subagent_id=subagent_id,
            agent_type=self._agent_type,
            description=subgoal.description,
        )

        try:
            allowed_skills = self._type_conf.get("skills", [])
            all_skills = discover_skills()
            if allowed_skills:
                skills = [
                    s for s in all_skills if s.skill_name in allowed_skills
                ]
            else:
                skills = all_skills

            if not skills:
                state.status = SubagentStatus.failed
                state.error = "No matching skills available"
                state.completed_at = _now_iso()
                return state

            addendum = self._type_conf.get("planning_prompt_addendum", "")
            sup_conf = AgentConfigLoader("supervisor")
            ctx_max = sup_conf.get("subagent.shared_context_max_chars", 4000)
            shared_text = ""
            if self._shared_context:
                shared_text = (
                    "\n\nContext from prior subagents:\n"
                    + "\n".join(
                        f"- {k}: {str(v)[:ctx_max]}"
                        for k, v in self._shared_context.items()
                    )
                )

            request = AgentExecutionRequest(
                prompt=subgoal.description,
                system_prompt=addendum or None,
                conversation_context=shared_text or None,
            )

            context = AgentContext(request_id=subagent_id)
            plan = create_plan(request, skills, context=context)

            _logger.log(
                "plan_created",
                goal_id=goal_id,
                subagent_id=subagent_id,
                objective=plan.objective,
                steps=len(plan.steps),
            )

            if not plan.steps:
                state.status = SubagentStatus.completed
                state.result = {"answer": "No steps needed."}
                state.completed_at = _now_iso()
                return state

            for step in plan.steps:
                approval_required = self._type_conf.get(
                    "approval_required", False,
                )
                if approval_required or self._approval_gate.requires_approval(
                    skill_name=step.skill_name,
                    method=step.method,
                    route=step.route_path_template,
                ):
                    state.status = SubagentStatus.awaiting_approval
                    req = self._approval_gate.request_approval(
                        skill_name=step.skill_name,
                        method=step.method,
                        route=step.route_path_template,
                        reason=step.reason,
                        goal_id=goal_id,
                        subagent_id=subagent_id,
                    )
                    state.approval_ids.append(req.approval_id)

                    poll_timeout = self._type_conf.get("timeout", 600.0)
                    approved = self._wait_for_approval(
                        req.approval_id, poll_timeout,
                    )
                    if not approved:
                        state.status = SubagentStatus.failed
                        state.error = (
                            f"Approval denied or expired for "
                            f"{step.method} {step.route_path_template}"
                        )
                        state.completed_at = _now_iso()
                        _logger.log(
                            "subagent_approval_blocked",
                            goal_id=goal_id,
                            subagent_id=subagent_id,
                            step_id=step.step_id,
                            level="warning",
                        )
                        return state

            state.status = SubagentStatus.executing
            max_steps = self._type_conf.get("max_steps", 10)
            if len(plan.steps) > max_steps:
                plan.steps = plan.steps[:max_steps]

            timeout = self._type_conf.get("timeout", 600.0)

            step_results = execute_plan(
                plan, context, skills, timeout_seconds=timeout,
            )

            for r in step_results:
                _logger.log(
                    "step_completed" if not r.error else "step_failed",
                    goal_id=goal_id,
                    subagent_id=subagent_id,
                    step_id=r.step_id,
                    skill_name=r.skill_name,
                    status_code=r.status_code,
                    duration_ms=r.duration_ms,
                    error=r.error,
                )

            any_failure = any(r.error for r in step_results)
            state.result = {
                "plan_objective": plan.objective,
                "step_results": [
                    r.model_dump(mode="json") for r in step_results
                ],
                "answer": _build_subagent_answer(plan, step_results),
            }
            state.status = (
                SubagentStatus.failed if all(r.error for r in step_results)
                else SubagentStatus.completed
            )
            if any_failure and state.status == SubagentStatus.completed:
                state.error = "Some steps failed (partial result)"

        except Exception as exc:
            state.status = SubagentStatus.failed
            state.error = str(exc)
            _logger.log(
                "subagent_error",
                level="error",
                goal_id=goal_id,
                subagent_id=subagent_id,
                error=str(exc),
            )

        started = state.started_at or ""
        state.completed_at = _now_iso()
        if started:
            from datetime import datetime
            try:
                t0 = datetime.fromisoformat(started)
                t1 = datetime.fromisoformat(state.completed_at)
                state.duration_seconds = (t1 - t0).total_seconds()
            except Exception:
                pass

        return state

    def _wait_for_approval(
        self, approval_id: str, timeout: float,
    ) -> bool:
        from common.compound.agent_config import AgentConfigLoader
        sup_conf = AgentConfigLoader("supervisor")
        poll_min = sup_conf.get("polling.min_delay", 1.0)
        poll_max = sup_conf.get("polling.max_delay", 30.0)
        poll_backoff = sup_conf.get("polling.backoff", 1.5)

        delay = poll_min
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            req = self._approval_gate.get_approval(approval_id)
            if req:
                if req.status == "approved":
                    return True
                if req.status in ("denied", "expired"):
                    return False
            time.sleep(delay)
            delay = min(delay * poll_backoff, poll_max)
        return False


def _build_subagent_answer(
    plan: AgentPlan, step_results: list[StepResult],
) -> str:
    parts: list[str] = []
    if plan.objective:
        parts.append(plan.objective)
    for r in step_results:
        if r.error:
            parts.append(f"{r.skill_name}: failed — {r.error}")
        elif isinstance(r.response_data, dict):
            text = r.response_data.get("text", "")
            summary = r.response_data.get("summary", "")
            data = r.response_data.get("data")
            items = r.response_data.get("items")
            if text:
                parts.append(f"{r.skill_name}: {text}")
            elif summary:
                parts.append(f"{r.skill_name}: {summary}")
            elif isinstance(items, list) and items:
                item_summaries = []
                for item in items[:20]:
                    if isinstance(item, dict):
                        title = item.get("title", "")
                        link = item.get("link", "")
                        snippet = item.get("summary", "") or item.get("content", "")
                        if title:
                            line = title
                            if link:
                                line += f" — {link}"
                            if snippet:
                                line += f"\n  {snippet[:200]}"
                            item_summaries.append(line)
                if item_summaries:
                    parts.append(
                        f"{r.skill_name}:\n" + "\n".join(item_summaries),
                    )
                else:
                    parts.append(f"{r.skill_name}: {len(items)} items returned.")
            elif isinstance(data, dict) and data:
                parts.append(f"{r.skill_name}: {json.dumps(data, default=str)[:500]}")
            else:
                parts.append(f"{r.skill_name}: completed.")
        else:
            parts.append(f"{r.skill_name}: completed.")
    return "\n".join(parts)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
