"""
License: MIT
Description: Top-level orchestration: discover skills, plan (or cache), execute, replan on failure, synthesize answer.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import get_aiserver_url, get_config_server_url, get_registry_url
from .context import AgentContext
from .executor import execute_plan
from .models import (
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentJobState,
    AgentPlan,
    JobStatus,
    StepResult,
)
from .plan_cache import PlanCache
from .planner import create_plan
from .router import use_skills_high_confidence
from .skill_discovery import discover_skills


def _trace_enabled() -> bool:
    v = os.environ.get("AGENT_TRACE", "1").strip()
    return v not in {"0", "false", "False", "no", "NO"}


def _trace(request_id: str, stage: str, **fields: Any) -> None:
    if not _trace_enabled():
        return
    payload = {"request_id": request_id, "stage": stage, **fields}
    try:
        print(f"[agent] {json.dumps(payload, default=str)}", flush=True)
    except Exception as exc:
        print(f"[agent] request_id={request_id} stage={stage} (json encode failed: {exc})", flush=True)


class JobStore:
    """In-memory job state."""

    def __init__(self) -> None:
        self._jobs: dict[str, AgentJobState] = {}

    def get(self, request_id: str) -> AgentJobState | None:
        return self._jobs.get(request_id)

    def put(self, request_id: str, state: AgentJobState) -> None:
        self._jobs[request_id] = state

    def update(self, request_id: str, **fields: Any) -> None:
        job = self._jobs.get(request_id)
        if job:
            for k, v in fields.items():
                if hasattr(job, k):
                    setattr(job, k, v)
            job.updated_at = datetime.now(timezone.utc)


class AgentService:
    def __init__(
        self,
        plan_cache: PlanCache | None = None,
        job_store: JobStore | None = None,
        execution_timeout_seconds: float = 120.0,
        max_replan_attempts: int = 2,
    ) -> None:
        self.plan_cache = plan_cache or PlanCache(ttl_seconds=300)
        self.job_store = job_store or JobStore()
        self.execution_timeout_seconds = execution_timeout_seconds
        self.max_replan_attempts = max_replan_attempts

    def execute(self, request: AgentExecutionRequest) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        context = AgentContext(request_id=request_id)
        start = time.monotonic()

        job = AgentJobState(request_id=request_id, status=JobStatus.running, message="Running")
        self.job_store.put(request_id, job)

        try:
            _trace(request_id, "request_received", prompt=request.prompt, timeout_seconds=request.timeout_seconds)
            skills = discover_skills()
            if not skills:
                try:
                    from common.skill_lifecycle import SkillLifecycle
                    lifecycle = SkillLifecycle(
                        registry_url=get_registry_url(),
                        config_url=get_config_server_url(),
                    )
                    lifecycle.prepare()
                    skills = discover_skills()
                    if skills:
                        _trace(request_id, "skill_bootstrap", skill_count=len(skills))
                except Exception as e:
                    _trace(request_id, "skill_bootstrap_failed", error=str(e))
            skill_names = [s.skill_name for s in skills]
            _trace(
                request_id,
                "skills_discovered",
                skill_count=len(skills),
                skills=[
                    {
                        "skill_name": s.skill_name,
                        "base_url": s.base_url,
                        "routes": [{"method": r.method, "path": r.path} for r in s.routes],
                    }
                    for s in skills
                ],
            )
            # Router: only invoke planner when high-confidence skill match. Otherwise direct to AI with memory.
            if skills and not use_skills_high_confidence(request.prompt, skills):
                _trace(request_id, "router_direct_answer", reason="no high-confidence skill match")
                answer = _direct_answer(request)
                _trace(request_id, "response_synthesis_complete", answer=answer)
                plan = AgentPlan(objective=request.prompt[:80] or "Respond", steps=[])
                result = AgentExecutionResult(
                    success=True,
                    request_id=request_id,
                    prompt=request.prompt,
                    objective=plan.objective,
                    plan=plan,
                    step_results=[],
                    answer=answer,
                    partial=False,
                    plan_cache_hit=False,
                    replan_count=0,
                    started_at=job.created_at,
                    finished_at=datetime.now(timezone.utc),
                )
                self.job_store.update(
                    request_id,
                    status=JobStatus.completed,
                    message="Completed",
                    result=result,
                )
                _trace(request_id, "request_complete", success=True, partial=False, plan_cache_hit=False, replan_count=0)
                return {
                    "request_id": request_id,
                    "result": result.model_dump(mode="json"),
                    "plan_cache_hit": False,
                }
            use_plan_cache = not (request.conversation_context and request.conversation_context.strip())
            cached_plan = self.plan_cache.get(request.prompt, skill_names) if use_plan_cache else None
            plan_cache_hit = cached_plan is not None
            if cached_plan:
                _trace(request_id, "plan_cache_hit")
                plan = cached_plan
            else:
                _trace(request_id, "planning_start")
                plan = create_plan(request, skills, context=context)
                if use_plan_cache:
                    self.plan_cache.put(request.prompt, skill_names, plan)
                _trace(
                    request_id,
                    "planning_complete",
                    objective=plan.objective,
                    steps=[s.model_dump(mode="json") for s in plan.steps],
                )

            step_results: list[StepResult] = []
            replan_count = 0

            if not plan.steps:
                _trace(request_id, "direct_answer", reason="plan has no steps")
                answer = _direct_answer(request)
                _trace(request_id, "response_synthesis_complete", answer=answer)
            else:
                timeout = request.timeout_seconds or self.execution_timeout_seconds
                _trace(request_id, "execution_start", timeout_seconds=timeout)
                step_results = execute_plan(
                    plan,
                    context,
                    skills,
                    timeout_seconds=timeout,
                    trace=lambda stg, f: _trace(request_id, f"execution_{stg}", **f),
                )
                _trace(
                    request_id,
                    "execution_complete",
                    step_results=[r.model_dump(mode="json") for r in step_results],
                )
                retryable_statuses = {500, 503, 504}
                failed_retryable = [r for r in step_results if r.error and r.status_code in retryable_statuses]

                while failed_retryable and replan_count < self.max_replan_attempts:
                    replan_count += 1
                    context.replan_count = replan_count
                    context.partial_results = [r for r in step_results if r.error]
                    self.plan_cache.invalidate(request.prompt, skill_names)
                    _trace(
                        request_id,
                        "replanning_start",
                        replan_count=replan_count,
                        failed_steps=[r.model_dump(mode="json") for r in context.partial_results],
                    )
                    plan = create_plan(request, skills, context=context)
                    self.plan_cache.put(request.prompt, skill_names, plan)
                    _trace(
                        request_id,
                        "replanning_complete",
                        replan_count=replan_count,
                        objective=plan.objective,
                        steps=[s.model_dump(mode="json") for s in plan.steps],
                    )
                    remaining = max(1.0, timeout - (time.monotonic() - start))
                    _trace(request_id, "execution_restart", remaining_timeout_seconds=remaining)
                    step_results = execute_plan(
                        plan,
                        context,
                        skills,
                        timeout_seconds=remaining,
                        trace=lambda stg, f: _trace(request_id, f"execution_{stg}", **f),
                    )
                    failed_retryable = [r for r in step_results if r.error and r.status_code in retryable_statuses]

                any_failure = any(r.error for r in step_results)
                partial = any_failure and any(r.status_code == 200 for r in step_results)
                _trace(request_id, "response_synthesis_start", partial=partial, any_failure=any_failure)
                answer = _build_answer(request.prompt, plan, step_results, partial)
                _trace(request_id, "response_synthesis_complete", answer=answer)

            any_failure = any(r.error for r in step_results)
            partial = any_failure and any(r.status_code == 200 for r in step_results)
            result = AgentExecutionResult(
                success=not any_failure or partial,
                request_id=request_id,
                prompt=request.prompt,
                objective=plan.objective,
                plan=plan,
                step_results=step_results,
                answer=answer,
                partial=partial,
                plan_cache_hit=plan_cache_hit,
                replan_count=replan_count,
                started_at=job.created_at,
                finished_at=datetime.now(timezone.utc),
            )
            self.job_store.update(
                request_id,
                status=JobStatus.completed if result.success else JobStatus.partial,
                message="Completed",
                result=result,
            )
            _trace(
                request_id,
                "request_complete",
                success=result.success,
                partial=result.partial,
                plan_cache_hit=plan_cache_hit,
                replan_count=replan_count,
            )
            return {
                "request_id": request_id,
                "result": result.model_dump(mode="json"),
                "plan_cache_hit": plan_cache_hit,
            }
        except Exception as e:
            print(f"[agent] request {request_id} failed: {e}", flush=True)
            _trace(request_id, "request_failed", error=str(e))
            self.job_store.update(
                request_id,
                status=JobStatus.failed,
                message=str(e),
                result=AgentExecutionResult(
                    success=False,
                    request_id=request_id,
                    prompt=request.prompt,
                    error=str(e),
                    finished_at=datetime.now(timezone.utc),
                ),
            )
            raise

    def plan_only(self, request: AgentExecutionRequest) -> dict[str, Any]:
        """Return plan without executing."""
        request_id = str(uuid.uuid4())
        context = AgentContext(request_id=request_id)
        skills = discover_skills()
        skill_names = [s.skill_name for s in skills]
        cached = self.plan_cache.get(request.prompt, skill_names)
        if cached:
            plan = cached
            plan_cache_hit = True
        else:
            plan = create_plan(request, skills, context=context)
            self.plan_cache.put(request.prompt, skill_names, plan)
            plan_cache_hit = False
        return {
            "request_id": request_id,
            "plan": plan.model_dump(mode="json"),
            "plan_cache_hit": plan_cache_hit,
        }

    def get_job(self, request_id: str) -> AgentJobState | None:
        return self.job_store.get(request_id)


def _direct_answer(request: AgentExecutionRequest) -> str:
    """Call aiserver with prompt and optional conversation context (no skills). Same format as agent_skill."""
    base = get_aiserver_url().rstrip("/")
    prompt_text = request.prompt.strip()
    if request.conversation_context and request.conversation_context.strip():
        prompt_text = (
            "Relevant context:\n\n"
            + request.conversation_context.strip()
            + "\n\nUser prompt:\n\n"
            + prompt_text
        )
    payload = {"prompt": prompt_text, "profile": "agent"}
    with httpx.Client(timeout=120.0) as client:
        r = client.post(f"{base}/generate", json=payload)
        r.raise_for_status()
    data = r.json() or {}
    output = data.get("output")
    if isinstance(output, dict):
        return output.get("text", "") or ""
    return str(output) if output is not None else ""


def _build_answer(
    prompt: str,
    plan: AgentPlan,
    step_results: list[StepResult],
    partial: bool,
) -> str:
    """Build a concise natural-language summary from step results.

    The full structured data is available in AgentExecutionResult.step_results;
    this text is only for human-readable context (e.g. memory, direct display).
    """
    ok = [r for r in step_results if not r.error]
    failed = [r for r in step_results if r.error]
    parts: list[str] = []
    if plan.objective:
        parts.append(plan.objective)
    for r in ok:
        summary = ""
        if isinstance(r.response_data, dict):
            summary = r.response_data.get("summary", "")
        if summary:
            parts.append(f"{r.skill_name}: {summary}")
        else:
            parts.append(f"{r.skill_name} ({r.method} {r.path}): completed.")
    if failed:
        for r in failed:
            parts.append(f"{r.skill_name}: failed — {r.error}")
    if partial:
        parts.append("(Partial result — some steps failed.)")
    return "\n".join(parts)
