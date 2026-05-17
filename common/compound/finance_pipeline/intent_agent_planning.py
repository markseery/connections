"""Planner prompt and response parsing for intent-driven agent loops."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass
class PlanAction:
    tool_name: str
    args: dict[str, Any]


@dataclass
class PlanDecision:
    done: bool
    final_report: str
    action: PlanAction | None = None


def extract_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def parse_plan_decision(planner_text: str) -> PlanDecision | None:
    plan = extract_json_object(planner_text)
    if not isinstance(plan, dict):
        return None

    done = bool(plan.get("done"))
    final_report = str(plan.get("final_report") or "").strip()
    action_raw = plan.get("action")
    if not isinstance(action_raw, dict):
        return PlanDecision(done=done, final_report=final_report, action=None)

    tool_name = str(action_raw.get("tool") or "").strip()
    if not tool_name:
        return PlanDecision(done=done, final_report=final_report, action=None)
    args = action_raw.get("args") if isinstance(action_raw.get("args"), dict) else {}
    return PlanDecision(
        done=done,
        final_report=final_report,
        action=PlanAction(tool_name=tool_name, args=args),
    )


def _slim_portfolio_analysis(result: dict[str, Any]) -> dict[str, Any]:
    """Drop bulky fields from analyze_portfolio_upcoming_distributions (keeps planning prompts small)."""
    out: dict[str, Any] = {
        "as_of_date": result.get("as_of_date"),
        "horizon_days": result.get("horizon_days"),
        "near_term_window_days": result.get("near_term_window_days"),
        "symbol_count": result.get("symbol_count"),
        "total_projected_distributions_horizon": result.get("total_projected_distributions_horizon"),
    }
    near = result.get("near_term_rows")
    if isinstance(near, list):
        if len(near) > 50:
            out["near_term_rows"] = near[:50]
            out["near_term_rows_omitted"] = len(near) - 50
        else:
            out["near_term_rows"] = near
    top = result.get("top_by_projected_total")
    if isinstance(top, list):
        slim: list[dict[str, Any]] = []
        for row in top[:20]:
            if not isinstance(row, dict):
                continue
            slim.append(
                {
                    "symbol": row.get("symbol"),
                    "next_projected_distribution_date": row.get("next_projected_distribution_date"),
                    "projected_distribution_total_horizon": row.get("projected_distribution_total_horizon"),
                    "payout_frequency": row.get("payout_frequency"),
                    "confidence_score": row.get("confidence_score"),
                }
            )
        out["top_by_projected_total"] = slim
    nup = result.get("next_upcoming")
    if isinstance(nup, list):
        out["next_upcoming"] = [
            {
                "symbol": r.get("symbol"),
                "next_projected_distribution_date": r.get("next_projected_distribution_date"),
                "payout_frequency": r.get("payout_frequency"),
            }
            for r in nup[:20]
            if isinstance(r, dict)
        ]
    full = result.get("results")
    if isinstance(full, list):
        out["per_symbol_summary"] = [
            {
                "symbol": r.get("symbol"),
                "error": r.get("error"),
                "next_date": r.get("next_projected_distribution_date"),
                "amount": r.get("next_projected_distribution_amount"),
                "frequency": r.get("payout_frequency"),
            }
            for r in full
            if isinstance(r, dict)
        ]
    return out


def _slim_symbol_analysis(result: dict[str, Any]) -> dict[str, Any]:
    """Remove heavy nested fields from analyze_symbol_distribution results."""
    keys = (
        "symbol",
        "shares",
        "payout_frequency",
        "cadence_days",
        "next_projected_distribution_date",
        "next_projected_distribution_amount",
        "forward_projection_sequence",
        "projected_distribution_dates",
        "projected_distribution_total_horizon",
        "confidence_score",
        "next_distribution_source",
        "events_observed",
        "error",
    )
    return {k: result.get(k) for k in keys if k in result or k == "error"}


def _slim_tool_result(tool: str, result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    if tool == "analyze_portfolio_upcoming_distributions":
        return _slim_portfolio_analysis(result)
    if tool == "analyze_symbol_distribution":
        return _slim_symbol_analysis(result)
    if tool in ("get_next_distribution", "suggest_distribution_pattern"):
        return result
    if tool == "web_research" and isinstance(result, dict):
        r = dict(result)
        res = r.get("result")
        if isinstance(res, str) and len(res) > 8_000:
            r["result"] = f"{res[:8_000]}… [truncated from {len(res)} chars]"
        return r
    raw = json.dumps(result, ensure_ascii=False)
    if len(raw) <= 24_000:
        return result
    return {
        "_truncated": True,
        "original_chars": len(raw),
        "keys": list(result.keys())[:40],
    }


def compact_observation_for_planner(obs: dict[str, Any]) -> dict[str, Any]:
    """Copy a single observation, shrinking large tool_result payloads for LLM context limits."""
    o = deepcopy(obs)
    if str(o.get("type") or "") == "tool_result" and isinstance(o.get("result"), (dict, list)):
        tool = str(o.get("tool") or "")
        o["result"] = _slim_tool_result(tool, o["result"])
    return o


def compact_observations_for_planner(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [compact_observation_for_planner(o) if isinstance(o, dict) else o for o in observations]


def planning_prompt(
    *,
    intent: str,
    tools: dict[str, Any],
    observations: list[dict[str, Any]],
    step_idx: int,
    max_steps: int,
    allowed_tool_names: list[str] | None = None,
) -> str:
    tail = observations[-8:]
    slim_tail: list[Any] = []
    for x in tail:
        if isinstance(x, dict):
            slim_tail.append(compact_observation_for_planner(x))
        else:
            slim_tail.append(x)
    obs_text = json.dumps(slim_tail, ensure_ascii=False, indent=2)
    tools_text = json.dumps(tools, ensure_ascii=False, indent=2)
    built_in_names = [
        str(row.get("name") or "").strip()
        for row in (tools.get("built_in_tools") or [])
        if isinstance(row, dict)
    ]
    names = [n for n in (allowed_tool_names or built_in_names) if n]
    allowed = "|".join(names) if names else "list_available_tools"
    return (
        "You are an autonomous analysis agent in a loop.\n"
        "Goal: satisfy the user intent with evidence-driven findings.\n\n"
        f"Intent:\n{intent}\n\n"
        f"Step {step_idx} of {max_steps}.\n"
        "Choose ONE action each step. Prefer concrete evidence collection first.\n\n"
        "Available tools (JSON):\n"
        f"{tools_text}\n\n"
        "Recent observations (JSON):\n"
        f"{obs_text}\n\n"
        "Return ONLY JSON object with this schema:\n"
        "{\n"
        '  "thought": "short reason",\n'
        f'  "action": {{"tool": "{allowed}", "args": {{}}}},\n'
        '  "done": false,\n'
        '  "final_report": ""\n'
        "}\n"
        "When done=true, include final_report with sections: key findings, risks, confidence, and next actions."
    )

