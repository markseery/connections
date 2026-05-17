"""Tool registry for the portfolio intent agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from common.compound.aiserver_discovery import get_aiserver_base_url
from common.compound.aiserver_generate_client import AiserverGenerateClient
from common.compound.finance_pipeline.intent_agent_workbook import (
    inspect_monte_carlo,
    resolve_workbook_path,
    sheet_rows,
)
from servers.agent.skill_discovery import discover_skills


@dataclass
class ToolContext:
    repo_root: Path
    default_workbook: Path | None
    aiserver_base_url: str
    profile: str
    provider: str | None
    web_research_provider: str | None
    timeout_sec: float
    explicit_url: str | None
    registry_url: str | None
    progress: Callable[[str], None]


class BuiltInTool(Protocol):
    name: str

    def spec(self) -> dict[str, Any]:
        ...

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        ...


class ListAvailableToolsTool:
    name = "list_available_tools"

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Return built-in tool catalog and discovered skill routes.",
            "args": {},
        }

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {"built_in_tools": [], "discovered_skill_tools": []}


class InspectMonteCarloTool:
    name = "inspect_monte_carlo"

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Read Monte Carlo summary tab and key scenario/decile sheets.",
            "args": {"workbook_path": "optional path to .cash_forecast.xlsx"},
        }

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = resolve_workbook_path(args.get("workbook_path"), context.default_workbook)
        context.progress(f"Tool inspect_monte_carlo using workbook: {path}")
        return inspect_monte_carlo(path, progress=context.progress)


class InspectSheetTool:
    name = "inspect_sheet"

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Read a specific workbook sheet preview.",
            "args": {"workbook_path": "path", "sheet_name": "str", "max_rows": "int<=200"},
        }

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = resolve_workbook_path(args.get("workbook_path"), context.default_workbook)
        sheet_name = str(args.get("sheet_name") or "").strip()
        if not sheet_name:
            raise ValueError("inspect_sheet requires sheet_name")
        max_rows = int(args.get("max_rows") or 120)
        max_rows = max(1, min(max_rows, 300))
        return {
            "workbook_path": str(path),
            "sheet_name": sheet_name,
            "rows": sheet_rows(path, sheet_name, max_rows=max_rows, progress=context.progress),
        }


class WebResearchTool:
    name = "web_research"

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Run targeted external research using aiserver search profile.",
            "args": {"query": "search question", "provider": "optional"},
        }

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("web_research requires query")
        context.progress(f"Tool web_research query: {query}")

        provider_from_action = args.get("provider")
        provider_from_action = str(provider_from_action).strip() if provider_from_action else None
        forced_provider = context.web_research_provider or provider_from_action or context.provider

        prompt = (
            "Perform concise risk-focused research for this portfolio-modeling question:\n"
            f"{query}\n\n"
            "Return bullet points with practical implications."
        )

        attempts: list[tuple[str, str | None, str]]
        if context.web_research_provider:
            attempts = [("search", context.web_research_provider, "forced_web_research_provider")]
        else:
            attempts = [("search", forced_provider, "primary_search_profile")]
            if forced_provider is None:
                attempts.append(("search", "ollama", "search_profile_with_ollama_fallback"))
            attempts.append((context.profile, context.provider, "configured_profile_fallback"))
            if context.provider is None:
                attempts.append(
                    (context.profile, "ollama", "configured_profile_with_ollama_fallback")
                )

        last_error: str | None = None
        for profile_name, provider_name, strategy in attempts:
            try:
                context.progress(
                    f"web_research strategy={strategy} profile={profile_name} provider={provider_name or 'default'}"
                )
                refreshed_base = get_aiserver_base_url(
                    explicit=context.explicit_url,
                    registry_override=context.registry_url,
                )
                client = AiserverGenerateClient(
                    base_url=refreshed_base,
                    timeout_sec=max(context.timeout_sec, 240.0),
                )
                payload = client.generate(
                    prompt=prompt,
                    profile=profile_name,
                    provider=provider_name,
                )
                text = AiserverGenerateClient.output_text(payload)
                return {
                    "query": query,
                    "result": text,
                    "research_strategy_used": strategy,
                    "profile_used": profile_name,
                    "provider_used": provider_name or "default",
                }
            except Exception as exc:
                last_error = str(exc)
                context.progress(f"web_research strategy failed ({strategy}): {exc}")
                continue
        raise RuntimeError(f"web_research failed after retries: {last_error}")


class PortfolioIntentToolRegistry:
    def __init__(self, context: ToolContext) -> None:
        self._context = context
        self._tools: dict[str, BuiltInTool] = {
            ListAvailableToolsTool.name: ListAvailableToolsTool(),
            InspectMonteCarloTool.name: InspectMonteCarloTool(),
            InspectSheetTool.name: InspectSheetTool(),
            WebResearchTool.name: WebResearchTool(),
        }

    def catalog(self) -> dict[str, Any]:
        discovered: list[dict[str, Any]] = []
        try:
            self._context.progress("Discovering skill tools from config/registry...")
            skills = discover_skills()
            self._context.progress(f"Discovered {len(skills)} skill definition(s).")
            for skill in skills:
                discovered.append(
                    {
                        "skill_name": skill.skill_name,
                        "base_url": skill.base_url,
                        "routes": [
                            {"method": route.method, "path": route.path, "description": route.description}
                            for route in skill.routes
                        ],
                    }
                )
        except Exception as exc:
            self._context.progress(f"Skill discovery failed: {exc}")
            discovered = [{"error": f"skill discovery failed: {exc}"}]

        built_in = [tool.spec() for tool in self._tools.values()]
        return {"built_in_tools": built_in, "discovered_skill_tools": discovered}

    def run(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        self._context.progress(f"Running tool: {tool_name}")
        if tool_name == ListAvailableToolsTool.name:
            return self.catalog()

        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        return tool.run(args, self._context)

