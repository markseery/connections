"""Tool registry for upcoming-distributions intent agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from common.compound.aiserver_discovery import get_aiserver_base_url
from common.compound.aiserver_generate_client import AiserverGenerateClient
from common.compound.finance_pipeline.distribution_pattern_engine import (
    analyze_symbol_distribution,
    collect_symbol_dividend_history,
    infer_pattern,
    normalize_projected_dates_for_frequency,
    parse_any_date,
)
from common.compound.finance_pipeline.distribution_pattern_llm import PatternLlmConfig, infer_pattern_via_llm
from common.compound.finance_pipeline.stockanalysis_dividends import StockAnalysisDividendExtractor

# Re-export for unit tests and backward compatibility
_analyze_symbol_distribution = analyze_symbol_distribution
_normalize_projected_dates_for_frequency = normalize_projected_dates_for_frequency
_parse_any_date = parse_any_date


@dataclass
class ToolContext:
    repo_root: Path
    positions: list[tuple[str, int]]
    as_of_date: date
    horizon_days: int
    profile: str
    provider: str | None
    web_research_provider: str | None
    timeout_sec: float
    explicit_url: str | None
    registry_url: str | None
    progress: Callable[[str], None]
    aiserver_base_url: str = ""
    """Fraction of each distribution reinvested at spot (0..1). Compounds shares across the horizon."""
    drip_rate: float = 0.0
    get_tool_specs: Callable[[], list[dict[str, Any]]] | None = None


class BuiltInTool(Protocol):
    name: str

    def spec(self) -> dict[str, Any]:
        ...

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        ...


def _pattern_tool_llm_config(context: ToolContext) -> PatternLlmConfig | None:
    base = (context.aiserver_base_url or "").strip()
    if not base:
        return None
    # Keep pattern LLM calls bounded so the planner does not block the run indefinitely.
    cap = min(float(context.timeout_sec or 60.0), 45.0)
    return PatternLlmConfig(
        base_url=base,
        profile=context.profile,
        provider=context.provider,
        timeout_sec=cap,
        progress=context.progress,
    )


class ListAvailableToolsTool:
    name = "list_available_tools"

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": "Return built-in tool catalog.", "args": {}}

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        built_in: list[dict[str, Any]] = []
        if context.get_tool_specs is not None:
            built_in = list(context.get_tool_specs())
        return {
            "built_in_tools": built_in,
            "summary": {
                "positions_count": len(context.positions),
                "as_of_date": context.as_of_date.isoformat(),
                "horizon_days": context.horizon_days,
                "drip_rate": float(context.drip_rate or 0.0),
            },
        }


class ListSymbolsTool:
    name = "list_symbols"

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "List symbols loaded from positions config.",
            "args": {},
        }

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        items = [{"symbol": sym, "shares": shares} for sym, shares in context.positions]
        return {"count": len(items), "items": items}


class AnalyzeSymbolTool:
    name = "analyze_symbol_distribution"

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Analyze one symbol for upcoming distribution projections.",
            "args": {"symbol": "ticker", "shares": "optional int override"},
        }

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        symbol = str(args.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("analyze_symbol_distribution requires symbol")
        shares_override = args.get("shares")
        shares = None
        if shares_override is not None:
            shares = max(0, int(shares_override))
        if shares is None:
            share_map = {sym: sh for sym, sh in context.positions}
            shares = int(share_map.get(symbol, 0))
        extractor = StockAnalysisDividendExtractor()
        context.progress(f"Analyzing upcoming distributions for {symbol}...")
        dr = max(0.0, min(1.0, float(context.drip_rate or 0.0)))
        spot: float | None = None
        if dr > 0:
            from common.compound.finance_pipeline.drip_forecast import get_current_price

            spot = get_current_price(symbol)
            if spot is None or spot <= 0:
                context.progress(
                    f"{symbol}: DRIP rate {dr:.2f} but spot price unavailable; using flat payouts (no DRIP)"
                )
                dr = 0.0
        result = analyze_symbol_distribution(
            symbol=symbol,
            shares=shares,
            as_of_date=context.as_of_date,
            horizon_days=context.horizon_days,
            extractor=extractor,
            drip_rate=dr,
            spot_price=spot,
        )
        context.progress(
            "Result "
            f"{symbol}: next_date={result.get('next_projected_distribution_date')}, "
            f"amount={result.get('next_projected_distribution_amount')}, "
            f"frequency={result.get('payout_frequency')}, "
            f"confidence={(result.get('signal') or {}).get('confidence_score')}, "
            f"source={result.get('next_distribution_source')}"
        )
        return result


class GetNextDistributionTool:
    name = "get_next_distribution"

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Get the next distribution date/amount estimate for one symbol.",
            "args": {"symbol": "ticker", "shares": "optional int override"},
        }

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        result = AnalyzeSymbolTool().run(args, context)
        return {
            "symbol": result.get("symbol"),
            "shares": result.get("shares"),
            "next_projected_distribution_date": result.get("next_projected_distribution_date"),
            "next_projected_distribution_amount": result.get("next_projected_distribution_amount"),
            "cadence_days": result.get("cadence_days"),
            "payout_frequency": result.get("payout_frequency"),
            "confidence_score": (result.get("signal") or {}).get("confidence_score"),
            "next_distribution_source": result.get("next_distribution_source"),
            "events_observed": result.get("events_observed"),
        }


class SuggestDistributionPatternTool:
    name = "suggest_distribution_pattern"

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Infer how ex-dividend dates recur (cadence or calendar rule) from history. "
                "method=heuristic (default) is fast, built-in rules. method=llm uses aiserver (slow; "
                "use when the planner needs a model interpretation). method=both compares both."
            ),
            "args": {
                "symbol": "ticker (required)",
                "method": 'optional: "heuristic" | "llm" | "both" (default: heuristic)',
            },
        }

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        symbol = str(args.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("suggest_distribution_pattern requires symbol")
        method = str(args.get("method") or "heuristic").strip().lower()
        if method not in ("heuristic", "llm", "both"):
            raise ValueError("method must be heuristic, llm, or both")
        extractor = StockAnalysisDividendExtractor()
        context.progress(f"Tool suggest_distribution_pattern: {symbol} (method={method})")
        bundle = collect_symbol_dividend_history(symbol=symbol, extractor=extractor)
        history_dates: list[date] = list(bundle.get("history_dates") or [])
        pay_freq: str | None = bundle.get("payout_frequency")
        if isinstance(pay_freq, str) and not pay_freq.strip():
            pay_freq = None
        out: dict[str, Any] = {
            "symbol": bundle.get("symbol"),
            "history_source": bundle.get("history_source"),
            "ex_dividend_dates": [d.isoformat() for d in history_dates],
            "payout_frequency": pay_freq,
            "events_observed": bundle.get("events_observed"),
            "heuristic_pattern": infer_pattern(history_dates, pay_freq),
        }
        if method in ("llm", "both"):
            cfg = _pattern_tool_llm_config(context)
            if cfg is None:
                out["llm_pattern"] = None
                out["llm_error"] = "no aiserver base URL; cannot run method=llm or both"
            else:
                out["llm_pattern"] = infer_pattern_via_llm(
                    symbol=symbol,
                    history_dates=history_dates,
                    payout_frequency=pay_freq,
                    config=cfg,
                )
        return out


class AnalyzePortfolioTool:
    name = "analyze_portfolio_upcoming_distributions"

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Analyze all configured symbols for upcoming distribution projections.",
            "args": {
                "top_n": "optional int (default 10)",
                "window_days": "optional; near-term table window, defaults to horizon",
            },
        }

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        top_n = max(1, min(100, int(args.get("top_n") or 10)))
        window_days = max(1, min(int(args.get("window_days") or context.horizon_days), context.horizon_days))
        symbol_tool = AnalyzeSymbolTool()
        rows: list[dict[str, Any]] = []
        for symbol, shares in context.positions:
            try:
                rows.append(symbol_tool.run({"symbol": symbol, "shares": shares}, context))
            except Exception as exc:
                context.progress(f"Result {symbol}: error={exc}")
                rows.append(
                    {
                        "symbol": symbol,
                        "shares": shares,
                        "error": str(exc),
                        "next_projected_distribution_date": None,
                        "next_projected_distribution_amount": 0.0,
                        "projected_distribution_total_horizon": 0.0,
                    }
                )
        valid = [r for r in rows if not r.get("error")]
        by_total = sorted(
            valid,
            key=lambda row: float(row.get("projected_distribution_total_horizon") or 0.0),
            reverse=True,
        )
        by_next_date = sorted(
            valid,
            key=lambda row: str(row.get("next_projected_distribution_date") or "9999-12-31"),
        )
        near_term_end = context.as_of_date + timedelta(days=window_days)
        near_term_rows: list[dict[str, Any]] = []
        for row in valid:
            confidence = float(
                row.get("confidence_score")
                or (row.get("signal") or {}).get("confidence_score")
                or 0.0
            )
            per_payout_amount = float(row.get("next_projected_distribution_amount") or 0.0)
            schedule = row.get("forward_payout_schedule")
            if isinstance(schedule, list) and schedule:
                for item in schedule:
                    if not isinstance(item, dict):
                        continue
                    payout_date = parse_any_date(str(item.get("date") or ""))
                    if payout_date is None:
                        continue
                    if context.as_of_date <= payout_date <= near_term_end:
                        amt = float(item.get("amount") or 0.0)
                        near_term_rows.append(
                            {
                                "date": payout_date.isoformat(),
                                "symbol": row.get("symbol"),
                                "next_projected_distribution_amount": round(amt, 2),
                                "payout_frequency": row.get("payout_frequency"),
                                "confidence_score": round(confidence, 2),
                                "shares": row.get("shares"),
                                "next_distribution_source": row.get("next_distribution_source"),
                            }
                        )
                continue
            projected_dates = [parse_any_date(str(d)) for d in (row.get("forward_projection_sequence") or [])]
            projected_dates = [d for d in projected_dates if d is not None]
            for payout_date in projected_dates:
                if context.as_of_date <= payout_date <= near_term_end:
                    near_term_rows.append(
                        {
                            "date": payout_date.isoformat(),
                            "symbol": row.get("symbol"),
                            "next_projected_distribution_amount": round(per_payout_amount, 2),
                            "payout_frequency": row.get("payout_frequency"),
                            "confidence_score": round(confidence, 2),
                            "shares": row.get("shares"),
                            "next_distribution_source": row.get("next_distribution_source"),
                        }
                    )
        near_term_rows.sort(
            key=lambda item: (str(item.get("date") or "9999-12-31"), str(item.get("symbol") or ""))
        )
        return {
            "as_of_date": context.as_of_date.isoformat(),
            "horizon_days": context.horizon_days,
            "drip_rate": float(context.drip_rate or 0.0),
            "near_term_window_days": window_days,
            "symbol_count": len(context.positions),
            "results": rows,
            "top_by_projected_total": by_total[:top_n],
            "next_upcoming": by_next_date[:top_n],
            "near_term_rows": near_term_rows,
            "near_term_next_10_days": near_term_rows,
            "total_projected_distributions_horizon": round(
                sum(float(r.get("projected_distribution_total_horizon") or 0.0) for r in valid),
                2,
            ),
        }


class WebResearchTool:
    name = "web_research"

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Run targeted research using aiserver search profile.",
            "args": {"query": "search question", "provider": "optional"},
        }

    def run(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("web_research requires query")
        provider_from_action = args.get("provider")
        provider_from_action = str(provider_from_action).strip() if provider_from_action else None
        forced_provider = context.web_research_provider or provider_from_action or context.provider
        prompt = (
            "Perform concise research for this upcoming-distributions portfolio question:\n"
            f"{query}\n\n"
            "Return short practical bullet points."
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
                attempts.append((context.profile, "ollama", "configured_profile_with_ollama_fallback"))

        last_error: str | None = None
        for profile_name, provider_name, strategy in attempts:
            try:
                context.progress(
                    f"web_research strategy={strategy} profile={profile_name} "
                    f"provider={provider_name or 'default'}"
                )
                refreshed_base = get_aiserver_base_url(
                    explicit=context.explicit_url,
                    registry_override=context.registry_url,
                )
                client = AiserverGenerateClient(
                    base_url=refreshed_base,
                    timeout_sec=max(context.timeout_sec, 240.0),
                )
                payload = client.generate(prompt=prompt, profile=profile_name, provider=provider_name)
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


class UpcomingDistributionToolRegistry:
    def __init__(self, context: ToolContext) -> None:
        self._context = context
        self._tools: dict[str, BuiltInTool] = {
            ListAvailableToolsTool.name: ListAvailableToolsTool(),
            ListSymbolsTool.name: ListSymbolsTool(),
            AnalyzeSymbolTool.name: AnalyzeSymbolTool(),
            GetNextDistributionTool.name: GetNextDistributionTool(),
            SuggestDistributionPatternTool.name: SuggestDistributionPatternTool(),
            AnalyzePortfolioTool.name: AnalyzePortfolioTool(),
            WebResearchTool.name: WebResearchTool(),
        }
        self._context.get_tool_specs = self._specs

    def _specs(self) -> list[dict[str, Any]]:
        return [tool.spec() for tool in self._tools.values()]

    def catalog(self) -> dict[str, Any]:
        built_in = [tool.spec() for tool in self._tools.values()]
        return {
            "built_in_tools": built_in,
            "positions_count": len(self._context.positions),
            "as_of_date": self._context.as_of_date.isoformat(),
            "horizon_days": self._context.horizon_days,
            "drip_rate": float(self._context.drip_rate or 0.0),
        }

    def run(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        self._context.progress(f"Running tool: {tool_name}")
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        return tool.run(args, self._context)
