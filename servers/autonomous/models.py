"""
Data models for the autonomous agent system.

Defines supervisor goals, subagent state, and the structured types that
flow between the supervisor, subagents, approval gate, and memory manager.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from common.compound.agent_config import AgentConfigLoader
from common.complex.approval_gate import ApprovalPolicy

_conf = AgentConfigLoader("supervisor")


class GoalStatus(str, Enum):
    planning = "planning"
    awaiting_approval = "awaiting_approval"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class SubagentStatus(str, Enum):
    created = "created"
    planning = "planning"
    awaiting_approval = "awaiting_approval"
    executing = "executing"
    completed = "completed"
    failed = "failed"


class AgentConfig(BaseModel):
    """Per-goal configuration. Defaults from config/agents/supervisor.yaml."""

    max_subagents: int = Field(
        default_factory=lambda: _conf.get("max_subagents", 5),
    )
    max_steps_per_subagent: int = Field(
        default_factory=lambda: _conf.get("max_steps_per_subagent", 10),
    )
    max_replan_attempts: int = Field(
        default_factory=lambda: _conf.get("max_replan_attempts", 3),
    )
    approval_policy: ApprovalPolicy = Field(
        default_factory=lambda: ApprovalPolicy(
            _conf.get("approval_policy", "approve_irreversible"),
        ),
    )
    timeout: float = Field(
        default_factory=lambda: _conf.get("timeout", 3600.0),
    )
    context_window_limit: int = Field(
        default_factory=lambda: _conf.get("context_window_limit", 32000),
    )


class Subgoal(BaseModel):
    """One decomposed subgoal produced by the supervisor planner."""

    subgoal_id: int
    description: str
    agent_type: str
    reason: str = ""
    depends_on: list[int] = Field(default_factory=list)
    arguments: dict[str, Any] = Field(default_factory=dict)


class SupervisorPlan(BaseModel):
    """The result of goal decomposition."""

    objective: str
    subgoals: list[Subgoal] = Field(default_factory=list)


class SubagentState(BaseModel):
    """Tracks the lifecycle of a single subagent."""

    subagent_id: str
    subgoal: Subgoal
    agent_type: str
    status: SubagentStatus = SubagentStatus.created
    agent_job_id: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float = 0.0
    approval_ids: list[str] = Field(default_factory=list)


class GoalState(BaseModel):
    """Full state for a supervisor goal."""

    goal_id: str
    goal: str
    config: AgentConfig = Field(default_factory=AgentConfig)
    status: GoalStatus = GoalStatus.planning
    plan: SupervisorPlan | None = None
    subagents: list[SubagentState] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    completed_at: str | None = None
    error: str | None = None
    answer: str | None = None


class GoalSubmitRequest(BaseModel):
    """API request to submit a new goal."""

    goal: str
    config: AgentConfig | None = None


class GoalSubmitResponse(BaseModel):
    """API response after goal submission."""

    goal_id: str
    status: str
    message: str = ""
