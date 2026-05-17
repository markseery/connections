"""Reusable loop runner for the portfolio intent agent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from common.compound.finance_pipeline.intent_agent_planning import (
    compact_observations_for_planner,
    parse_plan_decision,
    planning_prompt,
)


class ToolRegistryLike(Protocol):
    def catalog(self) -> dict[str, Any]:
        ...

    def run(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        ...


GenerateFn = Callable[..., str]
ProgressFn = Callable[[str], None]


@dataclass
class IntentLoopConfig:
    intent: str
    max_steps: int
    profile: str
    provider: str | None
    aiserver_base_url: str
    timeout_sec: float
    allowed_tool_names: list[str] | None = None
    synthesis_topic: str | None = None
    planner_timeout_sec: float | None = None
    synthesis_timeout_sec: float | None = None


@dataclass
class IntentLoopResult:
    final_report: str
    observations: list[dict[str, Any]]


class PortfolioIntentLoopRunner:
    def __init__(
        self,
        *,
        config: IntentLoopConfig,
        tool_registry: ToolRegistryLike,
        progress: ProgressFn,
        call_generate: GenerateFn,
    ) -> None:
        self._config = config
        self._tool_registry = tool_registry
        self._progress = progress
        self._call_generate = call_generate

    def _planner_timeout(self) -> float:
        if self._config.planner_timeout_sec is not None:
            return float(self._config.planner_timeout_sec)
        return float(self._config.timeout_sec)

    def _synthesis_timeout(self) -> float:
        if self._config.synthesis_timeout_sec is not None:
            return float(self._config.synthesis_timeout_sec)
        return float(self._config.timeout_sec)

    def run(self, *, initial_observations: list[dict[str, Any]] | None = None) -> IntentLoopResult:
        tools = self._tool_registry.catalog()
        observations: list[dict[str, Any]] = list(initial_observations or [])
        final_report: str | None = None

        for step_idx in range(1, self._config.max_steps + 1):
            self._progress(f"Loop step {step_idx}/{self._config.max_steps}: planning next action...")
            prompt = planning_prompt(
                intent=self._config.intent,
                tools=tools,
                observations=observations,
                step_idx=step_idx,
                max_steps=self._config.max_steps,
                allowed_tool_names=self._config.allowed_tool_names,
            )
            try:
                pt = self._planner_timeout()
                planner_text = self._call_generate(
                    prompt=prompt,
                    profile=self._config.profile,
                    provider=self._config.provider,
                    base_url=self._config.aiserver_base_url,
                    timeout_sec=pt,
                )
            except Exception as exc:
                self._progress(f"Planner call failed at step {step_idx}: {exc}")
                observations.append({"type": "error", "where": "planner_call", "error": str(exc)})
                continue

            decision = parse_plan_decision(planner_text)
            if decision is None:
                self._progress(f"Planner response parse failed at step {step_idx}.")
                observations.append(
                    {
                        "type": "error",
                        "where": "planner_parse",
                        "raw": planner_text[:4000],
                    }
                )
                continue

            if decision.done:
                final_report = decision.final_report
                self._progress(f"Planner marked done at step {step_idx}.")
                if final_report:
                    break

            if decision.action is None:
                self._progress(f"Planner returned invalid action at step {step_idx}.")
                observations.append(
                    {
                        "type": "error",
                        "where": "planner_action",
                        "raw": planner_text[:4000],
                    }
                )
                continue

            tool_name = decision.action.tool_name
            tool_args = decision.action.args
            self._progress(f"Step {step_idx}: executing tool '{tool_name}'")
            try:
                result = self._tool_registry.run(tool_name, tool_args)
                self._progress(f"Step {step_idx}: tool '{tool_name}' completed.")
                observations.append(
                    {
                        "type": "tool_result",
                        "tool": tool_name,
                        "args": tool_args,
                        "result": result,
                    }
                )
            except Exception as exc:
                self._progress(f"Step {step_idx}: tool '{tool_name}' failed: {exc}")
                observations.append(
                    {
                        "type": "tool_error",
                        "tool": tool_name,
                        "args": tool_args,
                        "error": str(exc),
                    }
                )

        if not final_report:
            self._progress("No final report from loop; running synthesis pass.")
            topic = self._config.synthesis_topic or (
                "portfolio Monte Carlo risk assessment from the following agent observations"
            )
            synth_obs = compact_observations_for_planner(observations[-12:])
            synthesis_prompt = (
                f"Synthesize a final {topic}. Provide sections: key findings, risks, "
                "confidence, and next actions.\n\n"
                + json.dumps(synth_obs, ensure_ascii=False, indent=2)
            )
            try:
                st = self._synthesis_timeout()
                final_report = self._call_generate(
                    prompt=synthesis_prompt,
                    profile=self._config.profile,
                    provider=self._config.provider,
                    base_url=self._config.aiserver_base_url,
                    timeout_sec=st,
                )
                self._progress("Synthesis completed.")
            except Exception as exc:
                self._progress(f"Synthesis failed: {exc}")
                final_report = f"Failed to synthesize final report: {exc}"

        return IntentLoopResult(final_report=final_report or "", observations=observations)

