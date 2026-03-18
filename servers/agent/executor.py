"""
License: MIT
Description: Execute a plan: resolve dependency waves, call skills over HTTP, store results in context.
"""

from __future__ import annotations

import re
import time
from typing import Any
from collections.abc import Callable

import httpx

from .context import AgentContext
from .models import AgentPlan, PlannedStep, StepResult
from .skill_discovery import SkillDefinition, SkillRoute


def execute_plan(
    plan: AgentPlan,
    context: AgentContext,
    skills: list[SkillDefinition],
    timeout_seconds: float = 120.0,
    trace: Callable[[str, dict[str, Any]], None] | None = None,
) -> list[StepResult]:
    """Execute plan steps in dependency order; return list of StepResult."""
    skills_by_name = {s.skill_name: s for s in skills}
    waves = _resolve_waves(plan.steps)
    results: list[StepResult] = []
    deadline = time.monotonic() + timeout_seconds

    for wave in waves:
        if time.monotonic() >= deadline:
            break
        for step in wave:
            remaining = max(1.0, deadline - time.monotonic())
            if trace:
                trace(
                    "step_start",
                    {
                        "step_id": step.step_id,
                        "skill_name": step.skill_name,
                        "method": step.method,
                        "path_template": step.route_path_template,
                        "arguments": step.arguments,
                        "depends_on": step.depends_on,
                    },
                )
            result = _execute_step(step, context, skills_by_name, remaining)
            results.append(result)
            if trace:
                trace(
                    "step_complete",
                    result.model_dump(mode="json"),
                )
            if result.error and result.status_code in {500, 503, 504}:
                for _ in range(2):
                    time.sleep(0.3)
                    if trace:
                        trace(
                            "step_retry",
                            {"step_id": step.step_id, "status_code": result.status_code, "error": result.error},
                        )
                    result = _execute_step(step, context, skills_by_name, remaining)
                    results[-1] = result
                    if trace:
                        trace("step_complete", result.model_dump(mode="json"))
                    if not result.error:
                        break
    return results


def _execute_step(
    step: PlannedStep,
    context: AgentContext,
    skills_by_name: dict[str, SkillDefinition],
    timeout: float,
) -> StepResult:
    skill = skills_by_name.get(step.skill_name)
    if not skill:
        return StepResult(
            step_id=step.step_id,
            skill_name=step.skill_name,
            method=step.method,
            path=step.route_path_template,
            status_code=404,
            error=f"Skill '{step.skill_name}' not found",
            duration_ms=0,
        )

    route = _find_route(skill, step.method, step.route_path_template)
    if not route:
        return StepResult(
            step_id=step.step_id,
            skill_name=step.skill_name,
            method=step.method,
            path=step.route_path_template,
            status_code=404,
            error=f"Route {step.method} {step.route_path_template} not found",
            duration_ms=0,
        )

    path = step.route_path_template
    if "{" in path:
        path = _render_path(path, step.arguments)
    args = context.resolve_references(step.arguments)
    payload = None
    params = None
    if step.method.upper() == "GET":
        params = args
    else:
        payload = args

    start = time.monotonic()
    base = skill.base_url.rstrip("/")
    url = f"{base}{path}"

    def _ensure_skill_loaded(client: httpx.Client) -> None:
        """Check worker's loaded list; load skill if not already loaded (same as scripts)."""
        try:
            r = client.get(f"{base}/worker/skills", timeout=min(10.0, timeout))
            if r.status_code != 200:
                return
            data = r.json() or {}
            loaded = data.get("loaded") or []
            if any(str(s.get("skill_name")) == step.skill_name for s in loaded):
                return
            load_r = client.post(
                f"{base}/worker/skills/{step.skill_name}/load",
                timeout=min(15.0, timeout),
            )
            if load_r.status_code >= 400:
                print(f"[executor] load {step.skill_name} returned {load_r.status_code}", flush=True)
        except Exception as exc:
            print(f"[executor] ensure_skill_loaded {step.skill_name} failed: {exc}", flush=True)

    def _do_request(client: httpx.Client) -> tuple[int, Any, str]:
        r = client.request(
            step.method.upper(),
            url,
            json=payload,
            params=params,
        )
        try:
            data = r.json()
        except Exception:
            data = r.text
        return r.status_code, data, r.text or str(r.status_code)

    try:
        with httpx.Client(timeout=timeout) as client:
            _ensure_skill_loaded(client)
            status_code, response_data, error_text = _do_request(client)

        duration_ms = (time.monotonic() - start) * 1000
        if isinstance(response_data, str) and status_code != 200:
            response_data = None

        if status_code == 200:
            context.store_step_result(step.step_id, response_data)

        return StepResult(
            step_id=step.step_id,
            skill_name=step.skill_name,
            method=step.method,
            path=path,
            status_code=status_code,
            response_data=response_data if status_code == 200 else None,
            error=None if status_code == 200 else error_text,
            duration_ms=duration_ms,
        )
    except Exception as e:
        print(f"[executor] step {step.step_id} ({step.skill_name}) failed: {e}", flush=True)
        return StepResult(
            step_id=step.step_id,
            skill_name=step.skill_name,
            method=step.method,
            path=path,
            status_code=500,
            error=str(e),
            duration_ms=(time.monotonic() - start) * 1000,
        )


def _find_route(skill: SkillDefinition, method: str, path_template: str) -> SkillRoute | None:
    for r in skill.routes:
        if r.method.upper() != method.upper():
            continue
        if r.path == path_template:
            return r
        pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", r.path)
        if re.fullmatch(pattern, path_template):
            return r
    return None


def _render_path(template: str, arguments: dict[str, Any]) -> str:
    path = template
    for param in re.findall(r"\{(\w+)\}", path):
        value = arguments.get(param)
        if value is None:
            value = ""
        path = path.replace(f"{{{param}}}", str(value))
    return path


def _resolve_waves(steps: list[PlannedStep]) -> list[list[PlannedStep]]:
    completed: set[int] = set()
    waves: list[list[PlannedStep]] = []
    remaining = list(steps)
    while remaining:
        wave = [s for s in remaining if all(d in completed for d in s.depends_on)]
        if not wave:
            wave = remaining
            remaining = []
        else:
            remaining = [s for s in remaining if s not in wave]
        waves.append(wave)
        for s in wave:
            completed.add(s.step_id)
    return waves
