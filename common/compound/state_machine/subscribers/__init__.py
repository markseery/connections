from __future__ import annotations

import fnmatch
import logging
from typing import Any

from common.compound.http_client import http_client
from common.complex.skill_lifecycle import find_live_worker

from ..config_loader import OrchestratorConfig
from ..models import StateChangeEvent
from ..transitions import raw_json_for_template

_logger = logging.getLogger(__name__)


def _matches(pattern: str, value: str) -> bool:
    if pattern in ("*", ""):
        return True
    return fnmatch.fnmatchcase(value, pattern)


def _render(template: str, event: StateChangeEvent, vars_map: dict[str, Any]) -> str:
    ctx: dict[str, str] = {
        "event.machine_id": event.machine_id,
        "event.dimension": event.dimension,
        "event.old_state_id": str(event.old_state_id or ""),
        "event.new_state_id": str(event.new_state_id or ""),
        "event.changed_at": event.changed_at.isoformat().replace("+00:00", "Z"),
        "event.new_raw_json": raw_json_for_template(event.new_raw),
        "event.entity.symbol": str(event.entity.get("symbol") or ""),
    }
    for k, v in vars_map.items():
        ctx[f"vars.{k}"] = str(v)
    out = template
    for key, val in ctx.items():
        out = out.replace("{" + key + "}", val)
    return out


def dispatch_subscribers(config: OrchestratorConfig, event: StateChangeEvent) -> None:
    vars_map = config.get("vars")
    if not isinstance(vars_map, dict):
        vars_map = {}
    for sub in config.subscribers:
        on = sub.get("on")
        if not isinstance(on, dict):
            continue
        if not _matches(str(on.get("machine_id") or "*"), event.machine_id):
            continue
        if not _matches(str(on.get("dimension") or "*"), event.dimension):
            continue
        when = str(on.get("when") or "state_changed")
        if when == "state_id_changed" and event.old_state_id == event.new_state_id:
            continue
        action = sub.get("action")
        if not isinstance(action, dict):
            continue
        atype = str(action.get("type") or "")
        params = action.get("params")
        if not isinstance(params, dict):
            params = {}
        try:
            if atype == "notification_skill":
                _notify(config, event, params, vars_map)
            elif atype == "storage_append":
                _storage_append(config, event, params)
            else:
                _logger.warning("unknown subscriber action type: %s", atype)
        except Exception as exc:
            _logger.exception("subscriber %s failed: %s", sub.get("id"), exc)


def _worker_url(config: OrchestratorConfig) -> str:
    names = config.get("registry", "worker_names")
    if not isinstance(names, list):
        names = None
    registry_url = __import__("os").environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
    url = find_live_worker(registry_url, names=[str(n) for n in names] if names else None)
    if not url:
        raise RuntimeError("no live worker for notification_skill")
    return url.rstrip("/")


def _notify(
    config: OrchestratorConfig,
    event: StateChangeEvent,
    params: dict[str, Any],
    vars_map: dict[str, Any],
) -> None:
    reg = config.get("registry")
    skill = "notification_skill"
    endpoint = "send"
    if isinstance(reg, dict):
        skill = str(reg.get("notification_skill") or skill)
        endpoint = str(reg.get("notification_endpoint") or endpoint)
    worker = _worker_url(config)
    subject_t = str(params.get("subject") or "State change")
    body_t = str(params.get("body_template") or "{event.new_raw_json}")
    to_raw = str(params.get("to") or "{vars.email_to}")
    to_val = _render(to_raw, event, vars_map)
    recipients = [x.strip() for x in to_val.replace(";", ",").split(",") if x.strip()]
    if not recipients:
        raise ValueError("notification subscriber has no recipients")
    payload = {
        "to": recipients,
        "subject": _render(subject_t, event, vars_map)[:500],
        "body": _render(body_t, event, vars_map),
        "metadata": {
            "state_machine": event.machine_id,
            "dimension": event.dimension,
            "correlation_id": event.correlation_id,
        },
    }
    url = f"{worker}/skills/{skill}/{endpoint}"
    with http_client("skill_call") as client:
        r = client.post(url, json=payload, timeout=60.0)
        r.raise_for_status()


def _storage_append(config: OrchestratorConfig, event: StateChangeEvent, params: dict[str, Any]) -> None:
    from ..storage_client import StateStorageClient

    ns = str(params.get("namespace") or config.get("storage", "events_namespace") or "state_events")
    storage = StateStorageClient()
    max_r = int(config.get("storage", "events_retention_max") or 500)
    storage.append_event(ns, event.to_dict(), max_records=max_r)
