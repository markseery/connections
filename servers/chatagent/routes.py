"""
License: MIT
Description: ChatAgent routes: skill-first, else AI fallback.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException

from .config import get_aiserver_url
from common.skill_lifecycle import SkillLifecycle, route_exists
from servers.agent.context import AgentContext
from servers.agent.executor import execute_plan
from servers.agent.models import AgentExecutionRequest, AgentPlan, PlannedStep, StepResult
from servers.agent.planner import create_plan


router = APIRouter(prefix="/chat", tags=["chatagent"])


def _format_step_failures(step_results: list[StepResult]) -> str:
    """Format failed step results for display when no step succeeded."""
    lines = ["**Skill execution failed** — no step returned success.\n"]
    for sr in step_results:
        err = sr.error or str(sr.status_code)
        lines.append(f"- **Step {sr.step_id}** ({sr.skill_name}): {sr.status_code} — {err[:300]}")
    return "\n".join(lines)


def _default_namespace() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"guest_{ts}"


@router.post("")
def chat(body: dict[str, Any]) -> dict[str, Any]:
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    profile = body.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        profile = "fast"
    namespace = body.get("namespace")
    if not isinstance(namespace, str) or not namespace.strip():
        namespace = _default_namespace()

    # Full skill lifecycle: find worker → load skills → register config.
    lifecycle = SkillLifecycle()
    try:
        skills = lifecycle.prepare()
    except Exception as exc:
        print(f"[chatagent] skill lifecycle FAILED: {exc}", flush=True)
        skills = []

    # Ask the planner whether any skill should be used.
    if skills:
        req = AgentExecutionRequest(prompt=prompt.strip())
        ctx = AgentContext(request_id=str(uuid4()))
        try:
            plan = create_plan(request=req, available_skills=skills, context=ctx)
        except Exception as exc:
            print(f"[chatagent] planner FAILED: {exc}", flush=True)
            plan = None

        if plan and plan.steps:
            print(
                f"[chatagent] plan: {[(s.skill_name, s.method, s.route_path_template) for s in plan.steps]}",
                flush=True,
            )
            try:
                plan = _sanitize_plan(plan, skills)
                if not plan.steps:
                    print("[chatagent] plan empty after sanitize — falling back to AI", flush=True)
                else:
                    for step in plan.steps:
                        if step.arguments is None:
                            step.arguments = {}
                        step.arguments["prompt"] = prompt.strip()
                    step_results = execute_plan(
                        plan=plan, context=ctx, skills=skills, timeout_seconds=60.0
                    )
                    for sr in step_results:
                        print(
                            f"[chatagent] step result: skill={sr.skill_name} "
                            f"path={sr.path} status={sr.status_code} error={sr.error}",
                            flush=True,
                        )
                    ok = [r for r in step_results if not r.error and r.status_code == 200]
                    if ok:
                        primary = ok[0]
                        out = primary.response_data
                        return {
                            "namespace": namespace,
                            "profile": profile,
                            "prompt": prompt,
                            "output": {"text": _skill_to_text(primary.skill_name, out)},
                            "used": {
                                "type": "skill",
                                "skill_name": primary.skill_name,
                                "path": primary.path,
                                "method": primary.method,
                            },
                            "raw": out,
                        }
                    else:
                        primary = step_results[0]
                        return {
                            "namespace": namespace,
                            "profile": profile,
                            "prompt": prompt,
                            "output": {"text": _format_step_failures(step_results)},
                            "used": {
                                "type": "skill",
                                "skill_name": primary.skill_name,
                                "path": primary.path,
                                "method": primary.method,
                            },
                            "raw": {
                                "success": False,
                                "step_results": [sr.model_dump(mode="json") for sr in step_results],
                            },
                        }
            except Exception as exc:
                print(f"[chatagent] plan execution FAILED: {exc}", flush=True)
                return {
                    "namespace": namespace,
                    "profile": profile,
                    "prompt": prompt,
                    "output": {"text": f"**Skill execution failed**\n\n{exc!s}"},
                    "used": {"type": "skill"},
                    "raw": {"success": False, "error": str(exc)},
                }
        else:
            print("[chatagent] planner returned no steps — falling back to AI", flush=True)

    # AI fallback — only when no skill was selected or no steps were executed
    base = get_aiserver_url()
    payload: dict[str, Any] = {"prompt": prompt.strip(), "profile": profile.strip()}
    try:
        with httpx.Client(timeout=120.0) as client:
            r = client.post(f"{base}/generate", json=payload)
            r.raise_for_status()
            ai = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return {
        "namespace": namespace,
        "profile": profile,
        "prompt": prompt,
        **ai,
        "used": {"type": "ai"},
    }


def _sanitize_plan(plan: AgentPlan, skills: list[Any]) -> AgentPlan:
    """Validate steps against discovered routes.  When the planner
    hallucinated an invalid route for a valid skill, try to repair it
    by matching the trailing path segment to an actual route template."""
    import re as _re

    skills_by_name = {getattr(s, "skill_name"): s for s in skills}

    kept: list[PlannedStep] = []
    for step in plan.steps:
        skill = skills_by_name.get(step.skill_name)
        if not skill:
            continue

        path = step.route_path_template.lower()
        if "/test" in path:
            continue
        if step.method.upper() == "GET" and any(
            x in path for x in ["/stats", "/config", "/notifications"]
        ):
            continue

        if route_exists(skill, step.method, step.route_path_template):
            kept.append(step)
            continue

        fixed = _try_fix_route(step, skill)
        if fixed:
            print(
                f"[chatagent] route fix: {step.route_path_template} → {fixed.route_path_template}",
                flush=True,
            )
            kept.append(fixed)

    kept_ids = {s.step_id for s in kept}
    cleaned: list[PlannedStep] = []
    for s in kept:
        deps = [d for d in (s.depends_on or []) if d in kept_ids]
        cleaned.append(s.model_copy(update={"depends_on": deps}))

    return plan.model_copy(update={"steps": cleaned})


def _try_fix_route(step: PlannedStep, skill: Any) -> PlannedStep | None:
    """Try to match a hallucinated route to a real one on the same skill.
    E.g. /skills/news_skill/news/CRWV → /skills/news_skill/stock/CRWV"""
    import re as _re

    hallucinated = step.route_path_template
    parts = hallucinated.rstrip("/").split("/")
    tail_value = parts[-1] if parts else ""

    for route in skill.routes:
        if route.method.upper() != step.method.upper():
            continue
        tpl = route.path
        param_names = _re.findall(r"\{(\w+)\}", tpl)
        if len(param_names) == 1 and tail_value:
            fixed_path = _re.sub(r"\{" + param_names[0] + r"\}", tail_value, tpl)
            return step.model_copy(update={"route_path_template": fixed_path})
        if not param_names:
            return step.model_copy(update={"route_path_template": tpl})

    return None


def _skill_to_text(skill_name: str, payload: Any) -> str:
    if isinstance(payload, dict):
        if skill_name == "statistics":
            for k in ["mean", "median", "stddev", "average"]:
                if k in payload:
                    return f"{k}: {payload[k]}"
        if "status" in payload and "notification_id" in payload:
            return f"notification sent: {payload.get('notification_id')}"
        if skill_name == "help_skill":
            return _format_help(payload)
        if skill_name == "stock_skill":
            return _format_stock(payload)
        if skill_name == "news_skill":
            return _format_news(payload)
        if skill_name == "workflow_skill":
            return _format_workflow(payload)
        if skill_name == "stored_webscrape_skill":
            return _format_stored_webscrape(payload)
        return str(payload)
    return str(payload)


def _format_help(data: dict[str, Any]) -> str:
    """Render help_skill responses as readable text."""
    # /skills endpoint — list of skills
    if "skills" in data and isinstance(data["skills"], list):
        lines = ["**Available Skills**\n"]
        for sk in data["skills"]:
            name = sk.get("skill_name", "?")
            desc = sk.get("description", "")
            lines.append(f"- **{name}**: {desc}")
            for ex in sk.get("examples", []):
                lines.append(f"    - _{ex}_")
        return "\n".join(lines)

    # /examples endpoint
    if "examples" in data and isinstance(data["examples"], dict):
        lines = ["**Example Prompts**\n"]
        for name, exs in data["examples"].items():
            lines.append(f"**{name}**")
            for ex in exs:
                lines.append(f"  - _{ex}_")
        return "\n".join(lines)

    # /about endpoint
    if "description" in data and "tips" in data:
        lines = [data["description"], ""]
        for tip in data["tips"]:
            lines.append(f"- {tip}")
        return "\n".join(lines)

    # /skills/{name} single detail
    if "skill_name" in data and "examples" in data:
        lines = [f"**{data['skill_name']}**: {data.get('description', '')}"]
        for ex in data.get("examples", []):
            lines.append(f"  - _{ex}_")
        for route, desc in (data.get("routes") or {}).items():
            lines.append(f"  `{route}` — {desc}")
        return "\n".join(lines)

    return str(data)


def _format_stock(data: dict[str, Any]) -> str:
    """Render stock_skill responses as readable text."""
    symbol = data.get("symbol", "")

    # Quote
    if "price" in data:
        lines = [f"**{symbol}** Quote"]
        price = data.get("price")
        change = data.get("change")
        pct = data.get("change_pct")
        if price is not None:
            price_str = f"**${price}**"
            if change is not None and pct is not None:
                sign = "+" if change >= 0 else ""
                price_str += f" ({sign}{change} / {sign}{pct}%)"
            lines.append(price_str)
        if data.get("volume"):
            lines.append(f"Volume: {data['volume']:,}")
        if data.get("market_cap"):
            lines.append(f"Market Cap: ${data['market_cap']:,}")
        return "\n".join(lines)

    # Fundamentals
    if "company" in data and isinstance(data["company"], dict):
        co = data["company"]
        name = co.get("name") or symbol
        lines = [f"**{name}** ({symbol}) Fundamentals"]
        for section_key in ["valuation", "growth", "profitability"]:
            section = data.get(section_key)
            if isinstance(section, dict) and any(v is not None for v in section.values()):
                lines.append(f"\n**{section_key.title()}**")
                for k, v in section.items():
                    if v is not None:
                        lines.append(f"- {k.replace('_', ' ').title()}: {v}")
        return "\n".join(lines)

    return str(data)


def _format_news(data: dict[str, Any]) -> str:
    """Render news_skill responses as readable text."""
    query = data.get("query", "")
    symbol = data.get("symbol") or ""
    articles = data.get("articles") or []
    sources = data.get("sources") or {}

    if not articles:
        return f"No news found for **{query}**."

    heading = f"**News for {symbol}**" if symbol else f"**News: {query}**"
    in_results = sources.get("in_results") or {}
    yf_shown = in_results.get("yfinance", 0)
    web_shown = in_results.get("web", 0)
    parts = []
    if yf_shown:
        parts.append(f"{yf_shown} from yfinance")
    if web_shown:
        parts.append(f"{web_shown} from web search")

    lines = [heading]
    if parts:
        lines.append(f"_{' + '.join(parts)}_\n")

    summary = data.get("summary") or ""
    if summary:
        lines.append("### Summary\n")
        lines.append(summary)
        lines.append("\n### Articles\n")

    for article in articles:
        if not isinstance(article, dict):
            continue
        title = article.get("title", "Untitled")
        publisher = article.get("publisher", "")
        link = article.get("link", "")
        published = article.get("published", "")
        summary = article.get("summary", "")
        source = article.get("source", "")

        entry = f"- **{title}**"
        meta = []
        if publisher:
            meta.append(publisher)
        if published:
            meta.append(str(published))
        if source:
            meta.append(f"via {source}")
        if meta:
            entry += f"  \n  _{' · '.join(meta)}_"
        if summary:
            short = summary[:300].replace("\n", " ").strip()
            if len(summary) > 300:
                short += "..."
            entry += f"  \n  {short}"
        if link:
            entry += f"  \n  [Read more]({link})"
        lines.append(entry)

    return "\n".join(lines)


def _format_workflow(data: dict[str, Any]) -> str:
    """Render workflow_skill responses as readable text.
    When the workflow is scrape_and_summarize or the last step has a summary
    field, show the full summary at the top; step list below for reference.
    """
    name = data.get("name", "unnamed")
    status = data.get("status", "unknown")
    total = data.get("steps_total", 0)
    completed = data.get("steps_completed", 0)
    failed = data.get("steps_failed", 0)
    step_results = data.get("step_results") or []

    status_icon = "completed" if status == "completed" else "failed"
    lines: list[str] = []

    # Extract full summary from last step (or any step with summary) when applicable
    summary_text: str | None = None
    if "scrape_and_summarize" in name or any(
        isinstance(sr.get("data"), dict) and sr.get("data", {}).get("summary") is not None
        for sr in step_results
    ):
        for sr in reversed(step_results):
            d = sr.get("data") if isinstance(sr.get("data"), dict) else None
            if d and d.get("summary") is not None:
                summary_text = d.get("summary")
                if isinstance(summary_text, str) and summary_text.strip():
                    break

    if summary_text:
        lines.append("### Summary")
        lines.append("")
        lines.append(summary_text)
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(f"**Workflow: {name}** — {status_icon}")
    lines.append(
        f"_{completed}/{total} steps completed"
        + (f", {failed} failed" if failed else "")
        + "_"
    )
    lines.append("")

    for sr in step_results:
        sid = sr.get("step_id", "?")
        skill = sr.get("skill", "")
        ok = sr.get("success", False)
        ms = sr.get("duration_ms", 0)
        mark = "OK" if ok else "FAIL"
        line = f"- **Step {sid}** ({skill}): {mark} ({ms:.0f}ms)"
        if not ok and sr.get("error"):
            line += f"  \n  _{sr['error'][:200]}_"
        if ok and sr.get("data"):
            d = sr["data"]
            if isinstance(d, dict) and d.get("summary") is not None:
                n = len(str(d.get("summary", "")))
                line += f"  \n  _summary ({n} chars, shown above)_"
            else:
                preview = str(d)
                if len(preview) > 200:
                    preview = preview[:200] + "..."
                line += f"  \n  `{preview}`"
        lines.append(line)

    if data.get("error"):
        lines.append("")
        lines.append(f"**Error:** {data['error']}")

    return "\n".join(lines)


def _format_stored_webscrape(data: dict[str, Any]) -> str:
    """Render stored_webscrape_skill responses as readable text."""
    if data.get("stored") is True:
        n = data.get("url_count", 0)
        base = data.get("base_url", "?")
        return f"Stored **{n}** URLs for `{base}`. Retrieve with GET /stored?base_url=..."
    if "value" in data:
        val = data["value"]
        if isinstance(val, dict):
            urls = val.get("urls") or []
            base = val.get("base_url", data.get("key", "?"))
            return f"Retrieved scrape for `{base}`: **{len(urls)}** URLs (content in `value.content_by_url`)."
    if "keys" in data:
        keys = data["keys"] if isinstance(data["keys"], list) else []
        count = data.get("count", len(keys))
        lines = [f"Stored scrapes: **{count}** keys"]
        for k in keys[:20]:
            lines.append(f"- `{k}`")
        if len(keys) > 20:
            lines.append(f"- ... and {len(keys) - 20} more")
        return "\n".join(lines)
    return str(data)
