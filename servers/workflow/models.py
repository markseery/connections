"""
License: MIT
Description: Pydantic models for workflow job tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowJobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class WorkflowStepProgress(BaseModel):
    step_num: int
    step_id: str
    step_type: str = "ai"
    skill_name: str = ""
    status: str = "pending"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_ms: float = 0
    output_chars: int = 0
    error: str | None = None
    skipped_reason: str | None = None
    log_tail: list[str] = Field(default_factory=list)
    log_offset: int = 0


class WorkflowJobState(BaseModel):
    job_id: str
    config_name: str = ""
    status: WorkflowJobStatus = WorkflowJobStatus.pending
    message: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_steps: int = 0
    completed_steps: int = 0
    step_progress: list[WorkflowStepProgress] = Field(default_factory=list)
    final_output: str = ""
    report_path: str = ""
    error: str | None = None
    vars: dict[str, str] = Field(default_factory=dict)
