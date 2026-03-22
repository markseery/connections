"""
License: MIT
Description: Workflow server routes — submit, poll, and list jobs.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .executor import WorkflowExecutor, WorkflowStepError
from .models import WorkflowJobState, WorkflowJobStatus, WorkflowStepProgress

router = APIRouter(prefix="/workflows", tags=["workflows"])


class _JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, WorkflowJobState] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> WorkflowJobState | None:
        return self._jobs.get(job_id)

    def put(self, job: WorkflowJobState) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                for k, v in fields.items():
                    if hasattr(job, k):
                        setattr(job, k, v)
                job.updated_at = datetime.now(timezone.utc)

    def list_recent(self, limit: int = 20) -> list[WorkflowJobState]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]


_store = _JobStore()

REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")


class SubmitRequest(BaseModel):
    config: str = Field(..., description="Config file name or path (e.g. cloud_services_notify_multistep.yaml)")
    vars: dict[str, str] = Field(default_factory=dict, description="Variable overrides")
    skill_timeout: float = Field(default=300, ge=1)
    ai_timeout: float = Field(default=300, ge=1)
    max_context_chars: int = Field(default=150_000, ge=0)


@router.post("/submit")
def submit(body: SubmitRequest) -> dict[str, Any]:
    """Submit a workflow for background execution. Returns job_id for polling."""
    executor = WorkflowExecutor(
        registry_url=REGISTRY_URL,
        ai_timeout=body.ai_timeout,
        skill_timeout=body.skill_timeout,
        max_context_chars=body.max_context_chars,
    )

    try:
        config_path = executor.resolve_config_path(body.config)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Config error: {e}") from e

    try:
        cfg = executor.load_config(config_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Config load error: {e}") from e

    steps = executor.normalize_steps(cfg)
    if not steps:
        raise HTTPException(status_code=400, detail="Config has no steps")

    if body.vars:
        if "vars" not in cfg or not isinstance(cfg["vars"], dict):
            cfg["vars"] = {}
        cfg["vars"].update(body.vars)

    job_id = str(uuid.uuid4())
    step_progress = []
    for i, s in enumerate(steps):
        sid = str(s.get("id") or i + 1).strip()
        stype = (s.get("type") or "ai").strip().lower()
        skill = (s.get("skill") or "").strip()
        step_progress.append(WorkflowStepProgress(
            step_num=i + 1, step_id=sid, step_type=stype, skill_name=skill,
        ))

    job = WorkflowJobState(
        job_id=job_id,
        config_name=config_path.name,
        status=WorkflowJobStatus.pending,
        message="Queued",
        total_steps=len(steps),
        step_progress=step_progress,
        vars=body.vars,
    )
    _store.put(job)

    def _run() -> None:
        _store.update(job_id, status=WorkflowJobStatus.running, message="Running")

        def _on_progress(step_num: int, step_id: str, status: str, error: str | None) -> None:
            now = datetime.now(timezone.utc)
            j = _store.get(job_id)
            if not j:
                return
            for sp in j.step_progress:
                if sp.step_num == step_num:
                    sp.status = status
                    if status == "running" and sp.started_at is None:
                        sp.started_at = now
                    if status in ("completed", "failed", "skipped"):
                        sp.finished_at = now
                        if sp.started_at:
                            sp.elapsed_ms = (now - sp.started_at).total_seconds() * 1000
                    sp.error = error
                    if status == "skipped":
                        sp.skipped_reason = error
                    break
            j.completed_steps = sum(
                1 for sp in j.step_progress if sp.status in ("completed", "failed", "skipped")
            )
            running = [sp for sp in j.step_progress if sp.status == "running"]
            if running:
                j.message = f"Step {running[0].step_num}/{j.total_steps} — {running[0].step_id}"
                if running[0].skill_name:
                    j.message += f" ({running[0].skill_name})"
            j.updated_at = now

        try:
            result = executor.run(config_path, var_overrides=body.vars, on_step_progress=_on_progress)
            _store.update(
                job_id,
                status=WorkflowJobStatus.completed,
                message="Completed",
                final_output=result.final_output,
                report_path=result.report_path,
            )
        except WorkflowStepError as e:
            _store.update(
                job_id,
                status=WorkflowJobStatus.failed,
                message=str(e),
                error=str(e),
            )
        except Exception as e:
            _store.update(
                job_id,
                status=WorkflowJobStatus.failed,
                message=f"Workflow failed: {e}",
                error=str(e),
            )

    thread = threading.Thread(target=_run, daemon=True, name=f"workflow-{job_id[:8]}")
    thread.start()

    return {
        "job_id": job_id,
        "status": "pending",
        "poll_url": f"/workflows/jobs/{job_id}",
        "config": config_path.name,
        "total_steps": len(steps),
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    """Poll job status with per-step progress."""
    job = _store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.model_dump(mode="json")


@router.get("/jobs")
def list_jobs(limit: int = 20) -> dict[str, Any]:
    """List recent jobs (newest first)."""
    jobs = _store.list_recent(limit)
    return {
        "jobs": [
            {
                "job_id": j.job_id,
                "config_name": j.config_name,
                "status": j.status,
                "message": j.message,
                "created_at": j.created_at.isoformat(),
                "total_steps": j.total_steps,
                "completed_steps": j.completed_steps,
            }
            for j in jobs
        ]
    }


@router.get("/configs")
def list_configs() -> dict[str, Any]:
    """List available workflow configuration files."""
    from .executor import CONFIG_DIR
    configs = sorted(CONFIG_DIR.glob("*.yaml"))
    return {
        "configs": [
            {"name": c.name, "path": str(c)}
            for c in configs
        ]
    }
