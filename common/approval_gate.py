"""
Approval gate for autonomous agents.

Checks whether a planned step requires human approval based on the
configured policy and the risk level of the skill route. Approval
requests are persisted to the storage server and logged to JSONL.

Usage:
    from common.approval_gate import ApprovalGate, ApprovalPolicy

    gate = ApprovalGate(
        policy=ApprovalPolicy.APPROVE_IRREVERSIBLE,
        storage_url="http://127.0.0.1:7010",
    )
    if gate.requires_approval(step):
        req = gate.request_approval(step, goal_id="g-123")
        # poll gate.get_approval(req.approval_id) until decided
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from common.agent_config import AgentConfigLoader
from common.agent_logger import AgentLogger
from common.http_client import http_client
from common.skill_config import SkillConfig

_conf = AgentConfigLoader("supervisor")
_logger = AgentLogger(
    "approval_gate",
    log_file=_conf.get("logging.approvals_file", "approvals.jsonl"),
)


class ApprovalPolicy(str, Enum):
    AUTO = "auto"
    APPROVE_ALL = "approve_all"
    APPROVE_IRREVERSIBLE = "approve_irreversible"


class ApprovalRequest(BaseModel):
    approval_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    goal_id: str = ""
    subagent_id: str | None = None
    step_skill_name: str = ""
    step_method: str = ""
    step_route: str = ""
    step_reason: str = ""
    risk_level: str = "reversible"
    action_description: str = ""
    status: Literal["pending", "approved", "denied", "expired"] = "pending"
    requested_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    decided_at: str | None = None
    decided_by: str | None = None
    ttl_seconds: int = Field(
        default_factory=lambda: _conf.get("approval.ttl_seconds", 3600),
    )


def _classify_route_risk(skill_name: str, method: str, route: str) -> str:
    """Look up risk level from the skill's config YAML route_risk mapping."""
    try:
        skill_cfg = SkillConfig(skill_name)
        route_risk = skill_cfg.raw().get("route_risk")
        if isinstance(route_risk, dict):
            route_key = f"{method.upper()} {route}"
            if route_key in route_risk:
                return str(route_risk[route_key])
            for pattern, risk in route_risk.items():
                if pattern.endswith("*"):
                    prefix = pattern.rsplit("*", 1)[0].strip()
                    method_part = prefix.split(" ", 1)[0] if " " in prefix else ""
                    if method_part and method.upper() == method_part.upper():
                        return str(risk)
                    if not method_part and prefix.rstrip() in route_key:
                        return str(risk)
    except Exception:
        pass

    upper = method.upper()
    if upper == "GET":
        return "safe"
    if upper in ("POST", "PUT", "PATCH"):
        return "reversible"
    if upper == "DELETE":
        return "irreversible"
    return "reversible"


class ApprovalGate:
    def __init__(
        self,
        policy: ApprovalPolicy | str,
        storage_url: str,
    ) -> None:
        if isinstance(policy, str):
            policy = ApprovalPolicy(policy)
        self.policy = policy
        self._storage_url = storage_url.rstrip("/")
        self._namespace = _conf.get(
            "approval.storage_namespace", "agent_approvals",
        )

    def requires_approval(
        self,
        *,
        skill_name: str,
        method: str,
        route: str,
    ) -> bool:
        if self.policy == ApprovalPolicy.AUTO:
            return False
        if self.policy == ApprovalPolicy.APPROVE_ALL:
            return True

        risk = _classify_route_risk(skill_name, method, route)
        return risk == "irreversible"

    def request_approval(
        self,
        *,
        skill_name: str,
        method: str,
        route: str,
        reason: str = "",
        goal_id: str = "",
        subagent_id: str | None = None,
    ) -> ApprovalRequest:
        risk = _classify_route_risk(skill_name, method, route)
        req = ApprovalRequest(
            goal_id=goal_id,
            subagent_id=subagent_id,
            step_skill_name=skill_name,
            step_method=method,
            step_route=route,
            step_reason=reason,
            risk_level=risk,
            action_description=(
                f"{method.upper()} {route} via {skill_name}"
                + (f" — {reason}" if reason else "")
            ),
        )
        self._persist(req)
        _logger.log(
            "approval_requested",
            goal_id=goal_id,
            subagent_id=subagent_id,
            approval_id=req.approval_id,
            action=req.action_description,
            risk_level=risk,
        )
        return req

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        url = (
            f"{self._storage_url}/namespaces/{self._namespace}"
            f"/records/{approval_id}"
        )
        try:
            with http_client("storage") as client:
                r = client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    value = data.get("value") if isinstance(data, dict) else data
                    if isinstance(value, dict):
                        req = ApprovalRequest(**value)
                        self._check_expiry(req)
                        return req
        except Exception:
            pass
        return None

    def list_pending(self) -> list[ApprovalRequest]:
        url = (
            f"{self._storage_url}/namespaces/{self._namespace}/records"
        )
        pending: list[ApprovalRequest] = []
        try:
            with http_client("storage") as client:
                r = client.get(url)
                if r.status_code != 200:
                    return pending
                keys = r.json().get("keys") or []
            for key in keys:
                req = self.get_approval(key)
                if req and req.status == "pending":
                    pending.append(req)
        except Exception:
            pass
        return pending

    def approve(
        self, approval_id: str, *, decided_by: str = "human",
    ) -> ApprovalRequest | None:
        req = self.get_approval(approval_id)
        if not req or req.status != "pending":
            return req
        req.status = "approved"
        req.decided_at = datetime.now(timezone.utc).isoformat()
        req.decided_by = decided_by
        self._persist(req)
        _logger.log(
            "approval_granted",
            goal_id=req.goal_id,
            subagent_id=req.subagent_id,
            approval_id=approval_id,
            decided_by=decided_by,
        )
        return req

    def deny(
        self, approval_id: str, *, decided_by: str = "human",
    ) -> ApprovalRequest | None:
        req = self.get_approval(approval_id)
        if not req or req.status != "pending":
            return req
        req.status = "denied"
        req.decided_at = datetime.now(timezone.utc).isoformat()
        req.decided_by = decided_by
        self._persist(req)
        _logger.log(
            "approval_denied",
            goal_id=req.goal_id,
            subagent_id=req.subagent_id,
            approval_id=approval_id,
            decided_by=decided_by,
        )
        return req

    def _persist(self, req: ApprovalRequest) -> None:
        url = (
            f"{self._storage_url}/namespaces/{self._namespace}"
            f"/records/{req.approval_id}"
        )
        try:
            with http_client("storage") as client:
                client.put(url, json={"value": req.model_dump()})
        except Exception:
            pass

    def _check_expiry(self, req: ApprovalRequest) -> None:
        if req.status != "pending":
            return
        try:
            requested = datetime.fromisoformat(req.requested_at)
            elapsed = (
                datetime.now(timezone.utc) - requested
            ).total_seconds()
            if elapsed >= req.ttl_seconds:
                req.status = "expired"
                req.decided_at = datetime.now(timezone.utc).isoformat()
                req.decided_by = "system:ttl_expired"
                self._persist(req)
                _logger.log(
                    "approval_expired",
                    goal_id=req.goal_id,
                    approval_id=req.approval_id,
                    elapsed_seconds=elapsed,
                )
        except Exception:
            pass
