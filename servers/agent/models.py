"""
License: MIT
Description: Agent data models — Pydantic models for plans, execution, and job state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    partial = "partial"


class PlannedStep(BaseModel):
    """One step in an execution plan."""

    step_id: int
    skill_name: str
    method: str
    route_path_template: str
    reason: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)


class AgentPlan(BaseModel):
    """Structured plan returned by the planner."""

    objective: str
    steps: list[PlannedStep] = Field(default_factory=list)


class AgentExecutionRequest(BaseModel):
    """Input for execution requests."""

    prompt: str
    timeout_seconds: float | None = None
    system_prompt: str | None = None


class StepResult(BaseModel):
    """Result of executing one step."""

    step_id: int
    skill_name: str
    method: str
    path: str
    status_code: int
    response_data: Any = None
    error: str | None = None
    duration_ms: float = 0


class AgentExecutionResult(BaseModel):
    """Full execution result."""

    success: bool
    request_id: str
    prompt: str
    objective: str = ""
    plan: AgentPlan = Field(default_factory=lambda: AgentPlan(objective=""))
    step_results: list[StepResult] = Field(default_factory=list)
    answer: str | None = None
    partial: bool = False
    plan_cache_hit: bool = False
    replan_count: int = 0
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    error: str | None = None


class AgentJobState(BaseModel):
    """Persisted state for a request."""

    request_id: str
    status: JobStatus = JobStatus.pending
    message: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    result: AgentExecutionResult | None = None
