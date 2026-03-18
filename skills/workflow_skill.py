"""
License: MIT
Description: Workflow skill — save reusable multi-step plan templates with
parameters, then execute them from the chat UI by name.

Templates use {{param}} placeholders in routes and arguments that get filled
at runtime.  Steps run in dependency order, can reference earlier results via
$step.<id>.<json_path>, and can poll async endpoints until ready.

Storage: templates are persisted via the storage server so they survive
restarts.  Execution history is kept in-memory.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter()

_STORAGE_NS = "workflows"
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "data" / "workflows"
_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

_executions: dict[str, dict[str, Any]] = {}


# ── Template CRUD ────────────────────────────────────────────────────────


@router.post("/templates")
def save_template(body: dict[str, Any]) -> dict[str, Any]:
    """Save a workflow template. Body: name, steps, params (optional), optional_params (optional).

    Body:
        name:        str  (unique template name, e.g. "scrape_and_summarize")
        description: str  (what this workflow does)
        params:      list[str]  (parameter names, e.g. ["url", "max_pages"])
        steps:       list[step]  (step definitions with {{param}} placeholders)
    """
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    description = body.get("description", "")
    params = body.get("params") or []
    steps = body.get("steps")
    if not steps or not isinstance(steps, list):
        raise HTTPException(status_code=400, detail="steps is required and must be a list")

    template = {
        "name": name,
        "description": description,
        "params": params,
        "steps": steps,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    _save_template_to_disk(name, template)
    print(f"[workflow] template saved: {name} ({len(steps)} steps, params={params})", flush=True)
    return {"summary": f"Saved workflow **{name}**.", "saved": True, "name": name, "params": params, "steps_count": len(steps)}


@router.get("/templates")
def list_templates() -> dict[str, Any]:
    """List saved workflow template names. Use when user asks what workflows exist."""
    templates = _load_all_templates()
    summaries = []
    for t in templates.values():
        summaries.append({
            "name": t["name"],
            "description": t.get("description", ""),
            "params": t.get("params", []),
            "steps_count": len(t.get("steps", [])),
        })
    count = len(summaries)
    items = [{"title": s["name"], "summary": s.get("description", "")} for s in summaries]
    return {"summary": f"**{count}** workflow templates.", "items": items, "templates": summaries, "count": count}


@router.get("/templates/{name}")
def get_template(name: str) -> dict[str, Any]:
    """Get a workflow template by name. Use when user asks for template details."""
    template = _load_template(name)
    if not template:
        raise HTTPException(status_code=404, detail=f"template '{name}' not found")
    template["summary"] = f"Workflow template: **{name}**."
    return template


@router.delete("/templates/{name}")
def delete_template(name: str) -> dict[str, Any]:
    """Delete a saved workflow template by name."""
    path = _TEMPLATE_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"template '{name}' not found")
    path.unlink()
    print(f"[workflow] template deleted: {name}", flush=True)
    return {"summary": f"Deleted workflow **{name}**.", "deleted": True, "name": name}


# ── Execute by template name ────────────────────────────────────────────


def _apply_param_constraints(
    params: dict[str, Any], constraints: dict[str, dict[str, Any]]
) -> None:
    """Apply template-defined min/max to numeric params in place. Generic: any param can have constraints."""
    for key, spec in (constraints or {}).items():
        if key not in params:
            continue
        try:
            n = int(params[key])
        except (TypeError, ValueError):
            continue
        try:
            if "min" in spec:
                n = max(n, int(spec["min"]))
            if "max" in spec:
                n = min(n, int(spec["max"]))
            params[key] = n
        except (TypeError, ValueError):
            pass


@router.post("/run/{name}")
def run_template(name: str, body: dict[str, Any]) -> dict[str, Any]:
    """Run a saved workflow template by name. Replace {name} with template name. Body: template parameters (e.g. url, pages, depth).

    Required params are in template.params; optional defaults in
    template.optional_params; template.param_aliases maps body keys to param
    names; template.param_constraints can set min/max per param (generic).
    All {{param}} in template.steps are replaced.
    """
    template = _load_template(name)
    if not template:
        raise HTTPException(status_code=404, detail=f"template '{name}' not found")

    raw = body or {}
    aliases = template.get("param_aliases") or {}
    body_with_aliases = {aliases.get(k, k): v for k, v in raw.items()}
    optional_defaults = template.get("optional_params") or {}
    params = {**optional_defaults, **body_with_aliases}

    _apply_param_constraints(params, template.get("param_constraints") or {})

    missing = [p for p in template.get("params", []) if p not in params]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"missing required parameters: {missing}",
        )

    steps_json = json.dumps(template["steps"])
    for key, value in params.items():
        if key == "timeout":
            continue
        steps_json = steps_json.replace("{{" + key + "}}", str(value))

    try:
        resolved_steps = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        print(f"[workflow] param substitution produced invalid JSON: {exc}", flush=True)
        raise HTTPException(status_code=400, detail=f"param substitution failed: {exc}")

    return execute_workflow({
        "name": f"{name} ({', '.join(f'{k}={v}' for k, v in params.items() if k != 'timeout')})",
        "steps": resolved_steps,
        "timeout": params.get("timeout", 120),
    })


# ── Execute ad-hoc ───────────────────────────────────────────────────────


@router.post("/execute")
def execute_workflow(body: dict[str, Any]) -> dict[str, Any]:
    """Execute a multi-step workflow. Body: name (optional), steps (list of step dicts), timeout (optional).

    Body:
        name:    optional workflow name
        steps:   list of step dicts
        timeout: optional overall timeout in seconds (default 120)
    """
    steps_raw = body.get("steps")
    if not steps_raw or not isinstance(steps_raw, list):
        raise HTTPException(status_code=400, detail="steps is required and must be a list")

    name = body.get("name", "unnamed")
    timeout = min(float(body.get("timeout", 120)), 300)
    workflow_id = str(uuid4())

    steps = _parse_steps(steps_raw)
    if not steps:
        raise HTTPException(status_code=400, detail="no valid steps found")

    worker_url = _get_worker_url()
    if not worker_url:
        raise HTTPException(status_code=503, detail="no worker available")

    record: dict[str, Any] = {
        "workflow_id": workflow_id,
        "name": name,
        "status": "running",
        "steps_total": len(steps),
        "steps_completed": 0,
        "steps_failed": 0,
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "finished_at": None,
        "step_results": [],
        "error": None,
    }
    _executions[workflow_id] = record

    scratchpad: dict[int, Any] = {}
    waves = _resolve_waves(steps)
    deadline = time.monotonic() + timeout
    all_results: list[dict[str, Any]] = []

    for wave in waves:
        if time.monotonic() >= deadline:
            record["error"] = "workflow timed out"
            record["status"] = "failed"
            break

        for step in wave:
            remaining = max(1.0, deadline - time.monotonic())
            resolved_args = _resolve_refs(step["args"], scratchpad)
            resolved_route = _resolve_route(step["route"], scratchpad)

            print(
                f"[workflow] step {step['step_id']}: {step['method']} {resolved_route} "
                f"args={resolved_args}",
                flush=True,
            )

            result = _call_step(
                worker_url=worker_url,
                method=step["method"],
                route=resolved_route,
                args=resolved_args,
                timeout=remaining,
            )
            result["step_id"] = step["step_id"]
            result["skill"] = step["skill"]
            all_results.append(result)

            if result["success"]:
                if "poll" in step:
                    poll_result = _poll_until_ready(
                        worker_url=worker_url,
                        route_template=step["poll"]["route"],
                        scratchpad={**scratchpad, step["step_id"]: result["data"]},
                        field=step["poll"]["field"],
                        target=step["poll"]["target"],
                        interval=step["poll"]["interval"],
                        max_attempts=step["poll"]["max_attempts"],
                        deadline=deadline,
                    )
                    if poll_result is not None:
                        result["data"] = poll_result
                    else:
                        result["success"] = False
                        result["error"] = "poll timed out waiting for completion"

                scratchpad[step["step_id"]] = result["data"]
                record["steps_completed"] += 1
                print(
                    f"[workflow] step {step['step_id']} OK ({result['duration_ms']:.0f}ms)",
                    flush=True,
                )
            else:
                record["steps_failed"] += 1
                print(
                    f"[workflow] step {step['step_id']} FAILED: {result['error']}",
                    flush=True,
                )
                if not step.get("continue_on_error"):
                    record["error"] = f"step {step['step_id']} failed: {result['error']}"
                    record["status"] = "failed"
                    break

        if record["status"] == "failed":
            break

    if record["status"] != "failed":
        record["status"] = "completed"

    record["finished_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record["step_results"] = all_results
    status = record["status"]
    name = record.get("name", "workflow")
    record["summary"] = f"Workflow **{name}**: **{status}** ({record['steps_completed']}/{record['steps_total']} steps)."
    return record


@router.get("/executions")
def list_executions() -> dict[str, Any]:
    """List workflow execution history. Use when user asks for past runs."""
    summaries = []
    for wf in _executions.values():
        summaries.append({
            "workflow_id": wf["workflow_id"],
            "name": wf["name"],
            "status": wf["status"],
            "steps_total": wf["steps_total"],
            "steps_completed": wf["steps_completed"],
            "steps_failed": wf["steps_failed"],
            "started_at": wf["started_at"],
            "finished_at": wf["finished_at"],
        })
    count = len(summaries)
    items = [{"title": s["workflow_id"], "summary": f"{s['name']} — {s['status']}"} for s in summaries]
    return {"summary": f"**{count}** workflow executions.", "items": items, "executions": summaries, "count": count}


@router.get("/executions/{workflow_id}")
def get_execution(workflow_id: str) -> dict[str, Any]:
    """Get one workflow execution by ID. Use when user asks for details of a specific run."""
    wf = _executions.get(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"execution {workflow_id} not found")
    wf["summary"] = f"Execution **{workflow_id}**: {wf.get('status', '')} — {wf.get('name', '')}"
    return wf


# ── Template persistence ─────────────────────────────────────────────────


def _save_template_to_disk(name: str, template: dict[str, Any]) -> None:
    path = _TEMPLATE_DIR / f"{name}.json"
    path.write_text(json.dumps(template, indent=2), encoding="utf-8")


def _load_template(name: str) -> dict[str, Any] | None:
    path = _TEMPLATE_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[workflow] failed to load template {name}: {exc}", flush=True)
        return None


def _load_all_templates() -> dict[str, dict[str, Any]]:
    templates: dict[str, dict[str, Any]] = {}
    for path in _TEMPLATE_DIR.glob("*.json"):
        try:
            t = json.loads(path.read_text(encoding="utf-8"))
            templates[t["name"]] = t
        except Exception as exc:
            print(f"[workflow] failed to load {path.name}: {exc}", flush=True)
    return templates


# ── Step parsing ─────────────────────────────────────────────────────────


def _parse_steps(raw: list[Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            print(f"[workflow] skipping non-dict step at index {i}", flush=True)
            continue
        step_id = item.get("step_id", i + 1)
        skill = item.get("skill", "")
        method = str(item.get("method", "GET")).upper()
        route = item.get("route", "")
        args = item.get("args") or item.get("arguments") or {}
        depends_on = item.get("depends_on") or []
        continue_on_error = bool(item.get("continue_on_error", False))
        poll = item.get("poll")

        if not route:
            print(f"[workflow] skipping step {step_id}: no route", flush=True)
            continue

        parsed_step: dict[str, Any] = {
            "step_id": int(step_id),
            "skill": skill,
            "method": method,
            "route": route,
            "args": args if isinstance(args, dict) else {},
            "depends_on": [int(d) for d in depends_on if isinstance(d, (int, float))],
            "continue_on_error": continue_on_error,
        }

        if isinstance(poll, dict):
            parsed_step["poll"] = {
                "route": poll.get("route", ""),
                "field": poll.get("field", "status"),
                "target": poll.get("target", "completed"),
                "interval": float(poll.get("interval", 2)),
                "max_attempts": int(poll.get("max_attempts", 60)),
            }

        steps.append(parsed_step)
    return steps


# ── Execution helpers ────────────────────────────────────────────────────


def _poll_until_ready(
    worker_url: str,
    route_template: str,
    scratchpad: dict[int, Any],
    field: str,
    target: str,
    interval: float,
    max_attempts: int,
    deadline: float,
) -> dict[str, Any] | None:
    route = _resolve_route(route_template, scratchpad)
    url = f"{worker_url.rstrip('/')}{route}"
    print(f"[workflow] polling {url} until {field}=={target!r}", flush=True)

    for attempt in range(1, max_attempts + 1):
        if time.monotonic() >= deadline:
            print(f"[workflow] poll deadline exceeded", flush=True)
            return None
        time.sleep(interval)
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(url)
            if r.status_code != 200:
                print(f"[workflow] poll attempt {attempt}: status {r.status_code}", flush=True)
                continue
            data = r.json()
            current = data.get(field, "")
            print(f"[workflow] poll attempt {attempt}: {field}={current!r}", flush=True)
            if str(current) == target:
                return data
            if str(current) == "failed":
                print(f"[workflow] poll: job reported failed", flush=True)
                return None
        except Exception as exc:
            print(f"[workflow] poll attempt {attempt} error: {exc}", flush=True)
            continue

    print(f"[workflow] poll exhausted {max_attempts} attempts", flush=True)
    return None


def _resolve_route(route: str, scratchpad: dict[int, Any]) -> str:
    """Replace $step.<id>.<path> in route; use [\\w.]+ so we stop at / boundaries."""
    def _replacer(m: re.Match[str]) -> str:
        val = _resolve_one(m.group(0), scratchpad)
        return str(val)
    return re.sub(r"\$step\.\d+\.[\w.]+", _replacer, route)


def _resolve_refs(args: dict[str, Any], scratchpad: dict[int, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and value.startswith("$step."):
            resolved[key] = _resolve_one(value, scratchpad)
        elif isinstance(value, list):
            resolved[key] = [
                _resolve_one(v, scratchpad) if isinstance(v, str) and v.startswith("$step.") else v
                for v in value
            ]
        elif isinstance(value, dict):
            resolved[key] = _resolve_refs(value, scratchpad)
        else:
            resolved[key] = value
    return resolved


def _resolve_one(ref: str, scratchpad: dict[int, Any]) -> Any:
    match = re.match(r"^\$step\.(\d+)\.(.+)$", ref)
    if not match:
        return ref
    step_id = int(match.group(1))
    json_path = match.group(2)
    step_data = scratchpad.get(step_id)
    if step_data is None:
        print(f"[workflow] unresolved reference: {ref}", flush=True)
        return ref
    current = step_data
    for part in json_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                print(f"[workflow] ref {ref} index error: {exc}", flush=True)
                return ref
        else:
            return ref
        if current is None:
            return ref
    return current


def _call_step(
    worker_url: str,
    method: str,
    route: str,
    args: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    url = f"{worker_url.rstrip('/')}{route}"
    payload = None
    params = None
    if method == "GET":
        params = args if args else None
    else:
        payload = args if args else None

    start = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.request(method, url, json=payload, params=params)
        duration_ms = (time.monotonic() - start) * 1000

        try:
            data = r.json()
        except Exception as exc:
            print(f"[workflow] JSON decode failed for {route}: {exc}", flush=True)
            data = r.text

        if r.status_code == 200:
            return {
                "success": True,
                "data": data,
                "status_code": r.status_code,
                "duration_ms": duration_ms,
                "error": None,
            }
        else:
            return {
                "success": False,
                "data": None,
                "status_code": r.status_code,
                "duration_ms": duration_ms,
                "error": r.text or str(r.status_code),
            }
    except Exception as exc:
        print(f"[workflow] call to {route} failed: {exc}", flush=True)
        return {
            "success": False,
            "data": None,
            "status_code": 500,
            "duration_ms": (time.monotonic() - start) * 1000,
            "error": str(exc),
        }


def _resolve_waves(steps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    completed: set[int] = set()
    waves: list[list[dict[str, Any]]] = []
    remaining = list(steps)
    while remaining:
        wave = [s for s in remaining if all(d in completed for d in s["depends_on"])]
        if not wave:
            wave = remaining
            remaining = []
        else:
            remaining = [s for s in remaining if s not in wave]
        waves.append(wave)
        for s in wave:
            completed.add(s["step_id"])
    return waves


def _get_worker_url() -> str | None:
    registry = os.environ.get(
        "REGISTRY_SERVER_URL", "http://127.0.0.1:7002"
    ).strip().rstrip("/")
    try:
        with httpx.Client(timeout=2.0) as client:
            for name in ["worker-1", "worker"]:
                try:
                    r = client.get(f"{registry}/servers/{name}")
                    if r.status_code != 200:
                        continue
                    url = ((r.json() or {}).get("url") or "").rstrip("/")
                    if not url:
                        continue
                    h = client.get(f"{url}/health")
                    if h.status_code == 200:
                        return url
                except Exception as exc:
                    print(f"[workflow] worker {name} probe failed: {exc}", flush=True)
                    continue
    except Exception as exc:
        print(f"[workflow] worker discovery failed: {exc}", flush=True)
    return None


def get_router() -> APIRouter:
    return router
