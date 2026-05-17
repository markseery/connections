"""Reusable CLI command for portfolio intent agent analysis."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from common.compound.command_base import BaseCommand, UsageError
from common.compound.aiserver_discovery import get_aiserver_base_url
from common.compound.aiserver_generate_client import AiserverGenerateClient
from common.compound.finance_pipeline.intent_agent_runner import (
    IntentLoopConfig,
    PortfolioIntentLoopRunner,
)
from common.compound.finance_pipeline.intent_agent_tools import PortfolioIntentToolRegistry, ToolContext
from common.compound.finance_pipeline.intent_agent_workbook import (
    find_latest_valid_cash_forecast_xlsx,
    is_valid_xlsx,
)

DEFAULT_INTENT = (
    "Analyze the portfolio Monte Carlo scenarios and income deciles. "
    "Assess concentration risk, model support/weaknesses, and practical "
    "threshold tuning guidance using available tools and targeted research."
)


@dataclass
class PortfolioIntentArgs:
    intent: str = DEFAULT_INTENT
    cash_forecast_xlsx: str | None = None
    max_steps: int = 8
    profile: str = "reason"
    provider: str | None = None
    web_research_provider: str | None = None
    url: str | None = None
    registry_url: str | None = None
    report_out: str | None = None


@dataclass
class AgentConfig:
    profile: str = "reason"
    provider: str | None = None
    web_research_provider: str | None = None
    max_steps: int = 8
    timeout_sec: float = 180.0
    explicit_url: str | None = None
    registry_url: str | None = None


def _progress(message: str) -> None:
    print(f"[intent-agent] {message}", flush=True)


def _check_aiserver_health(base_url: str, timeout_sec: float = 5.0) -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.get(f"{base_url.rstrip('/')}/health")
            if response.status_code == 200:
                return True, "ok"
            return False, f"status={response.status_code}"
    except Exception as exc:
        return False, str(exc)


def _call_generate(
    *,
    prompt: str,
    profile: str,
    provider: str | None,
    base_url: str,
    timeout_sec: float,
) -> str:
    _progress(
        f"aiserver /generate profile={profile} provider={provider or 'default'} "
        f"timeout={timeout_sec:.0f}s prompt_chars={len(prompt)}"
    )
    client = AiserverGenerateClient(base_url=base_url, timeout_sec=timeout_sec)
    payload = client.generate(prompt=prompt, profile=profile, provider=provider)
    return AiserverGenerateClient.output_text(payload)


class PortfolioIntentAgentCommand(BaseCommand[PortfolioIntentArgs]):
    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Intent-driven portfolio analysis agent loop.")
        parser.add_argument("--intent", default=DEFAULT_INTENT, help="Top-level intent for the agent.")
        parser.add_argument(
            "--cash-forecast-xlsx",
            default=None,
            help="Optional path to .cash_forecast.xlsx (default: latest found under robinhood/*).",
        )
        parser.add_argument("--max-steps", type=int, default=8, help="Max loop steps (default: 8).")
        parser.add_argument("--profile", default="reason", help="Aiserver profile for planner calls.")
        parser.add_argument("--provider", default=None, help="Optional provider override.")
        parser.add_argument(
            "--web-research-provider",
            default=None,
            help="Force provider for web_research tool calls (overrides --provider for research steps).",
        )
        parser.add_argument("--url", default=None, help="Optional explicit aiserver base URL.")
        parser.add_argument(
            "--registry-url",
            default=os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002"),
            help="Registry URL used by aiserver discovery.",
        )
        parser.add_argument(
            "--report-out",
            default=None,
            help="Optional output file path for final report markdown.",
        )
        return parser

    @classmethod
    def from_namespace(cls, raw: argparse.Namespace) -> PortfolioIntentArgs:
        return PortfolioIntentArgs(
            intent=str(raw.intent),
            cash_forecast_xlsx=raw.cash_forecast_xlsx,
            max_steps=int(raw.max_steps),
            profile=str(raw.profile),
            provider=(str(raw.provider).strip() if raw.provider else None),
            web_research_provider=(
                str(raw.web_research_provider).strip() if raw.web_research_provider else None
            ),
            url=(str(raw.url).strip() if raw.url else None),
            registry_url=(str(raw.registry_url).strip() if raw.registry_url else None),
            report_out=raw.report_out,
        )

    @classmethod
    def run(cls, args: PortfolioIntentArgs) -> int:
        _progress("Starting portfolio intent agent.")

        repo_root = Path(__file__).resolve().parent.parent
        cfg = AgentConfig(
            profile=args.profile.strip() or "reason",
            provider=args.provider,
            web_research_provider=args.web_research_provider,
            max_steps=max(1, int(args.max_steps)),
            explicit_url=args.url,
            registry_url=args.registry_url,
        )

        workbook_path: Path | None = None
        if args.cash_forecast_xlsx:
            workbook_path = Path(args.cash_forecast_xlsx).expanduser().resolve()
            if not workbook_path.is_file():
                raise UsageError(f"workbook not found: {workbook_path}")
            ok, reason = is_valid_xlsx(workbook_path)
            if not ok:
                raise UsageError(f"invalid workbook '{workbook_path}': {reason}")
            _progress(f"Using workbook from --cash-forecast-xlsx: {workbook_path}")
        else:
            workbook_path, invalid_notes = find_latest_valid_cash_forecast_xlsx(repo_root)
            if workbook_path:
                _progress(f"Auto-selected workbook: {workbook_path}")
            else:
                _progress("No valid default cash forecast workbook found.")
                if invalid_notes:
                    _progress("Invalid workbook candidates:")
                    for note in invalid_notes[:8]:
                        _progress(f"  - {note}")

        try:
            aiserver_base_url = get_aiserver_base_url(
                explicit=cfg.explicit_url,
                registry_override=cfg.registry_url,
            )
            _progress(f"Using aiserver base URL: {aiserver_base_url}")
            ok, health_msg = _check_aiserver_health(aiserver_base_url)
            if ok:
                _progress("Aiserver health check: ok")
            else:
                _progress(f"Aiserver health check warning: {health_msg}")
        except Exception as exc:
            raise UsageError(f"discovering aiserver URL failed: {exc}") from exc

        tool_context = ToolContext(
            repo_root=repo_root,
            default_workbook=workbook_path,
            aiserver_base_url=aiserver_base_url,
            profile=cfg.profile,
            provider=cfg.provider,
            web_research_provider=cfg.web_research_provider,
            timeout_sec=cfg.timeout_sec,
            explicit_url=cfg.explicit_url,
            registry_url=cfg.registry_url,
            progress=_progress,
        )
        tool_registry = PortfolioIntentToolRegistry(tool_context)

        observations: list[dict[str, object]] = []
        if workbook_path is not None:
            observations.append({"type": "context", "default_workbook_path": str(workbook_path)})
        else:
            observations.append(
                {
                    "type": "context",
                    "warning": "No default cash-forecast workbook found; provide --cash-forecast-xlsx.",
                }
            )

        runner = PortfolioIntentLoopRunner(
            config=IntentLoopConfig(
                intent=args.intent,
                max_steps=cfg.max_steps,
                profile=cfg.profile,
                provider=cfg.provider,
                aiserver_base_url=aiserver_base_url,
                timeout_sec=cfg.timeout_sec,
            ),
            tool_registry=tool_registry,
            progress=_progress,
            call_generate=_call_generate,
        )
        loop_result = runner.run(initial_observations=[dict(row) for row in observations])
        final_report = loop_result.final_report

        if args.report_out:
            out_path = Path(args.report_out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(final_report or "", encoding="utf-8")
            _progress(f"Saved report: {out_path}")

        _progress("Done.")
        print(final_report or "")
        return 0

