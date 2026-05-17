"""LLM-assisted distribution schedule pattern selection (planner tool; not the hot path)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from common.compound.aiserver_generate_client import AiserverGenerateClient
from common.compound.finance_pipeline.intent_agent_planning import extract_json_object


@dataclass(frozen=True)
class PatternLlmConfig:
    base_url: str
    profile: str
    provider: str | None
    timeout_sec: float
    progress: Callable[[str], None] | None = None


def _coerce_pattern(data: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(data.get("kind") or "").strip()
    if kind == "day_cadence":
        days = int(data.get("cadence_days") or 0)
        if days < 1 or days > 400:
            return None
        label = str(data.get("label") or ("weekly" if days <= 10 else "cadence"))
        return {"kind": "day_cadence", "cadence_days": days, "label": label}
    if kind == "month_dom":
        ms = max(1, int(data.get("months_step") or 1))
        dom = int(data.get("day_of_month") or 0)
        if dom < 1 or dom > 31:
            return None
        label = str(data.get("label") or "monthly")
        return {"kind": "month_dom", "months_step": ms, "day_of_month": dom, "label": label}
    if kind == "month_nth_weekday":
        ms = max(1, int(data.get("months_step") or 1))
        wd = int(data.get("weekday") or -1)
        nth = int(data.get("nth") or 0)
        if wd < 0 or wd > 6 or nth < 1 or nth > 5:
            return None
        label = str(data.get("label") or "monthly")
        return {
            "kind": "month_nth_weekday",
            "months_step": ms,
            "weekday": wd,
            "nth": nth,
            "label": label,
        }
    return None


def _pattern_llm_prompt(
    *,
    symbol: str,
    history_dates: list[date],
    payout_frequency: str | None,
) -> str:
    lines = [d.isoformat() for d in sorted(set(history_dates))]
    freq = (payout_frequency or "").strip() or "(unspecified — infer from dates)"
    return (
        "You are a fund distribution calendar analyst. Given past ex-dividend dates, "
        "choose the single best repeating rule for projecting future ex-dates.\n\n"
        f"Symbol: {symbol}\n"
        f"Stated frequency: {freq}\n"
        f"Ex-dividend dates (ISO, sorted): {lines}\n\n"
        "Return ONLY a JSON object (no markdown) with one of these shapes:\n"
        "1) Weekly / bi-weekly / fixed day spacing:\n"
        '   {"kind":"day_cadence","cadence_days":7,"label":"weekly"}\n'
        "2) Same calendar day of month (or quarterly stepping months):\n"
        '   {"kind":"month_dom","months_step":1,"day_of_month":15,"label":"monthly"}\n'
        "3) Nth weekday of month (0=Monday ... 6=Sunday):\n"
        '   {"kind":"month_nth_weekday","months_step":1,"weekday":2,"nth":1,"label":"monthly"}\n'
    )


def infer_pattern_via_llm(
    *,
    symbol: str,
    history_dates: list[date],
    payout_frequency: str | None,
    config: PatternLlmConfig,
) -> dict[str, Any] | None:
    if not history_dates and not (payout_frequency or "").strip():
        return None
    pr = config.progress
    if pr:
        pr(f"suggest_distribution_pattern (LLM) {symbol}...")
    prompt = _pattern_llm_prompt(symbol=symbol, history_dates=history_dates, payout_frequency=payout_frequency)
    try:
        client = AiserverGenerateClient(base_url=config.base_url, timeout_sec=config.timeout_sec)
        payload = client.generate(prompt=prompt, profile=config.profile, provider=config.provider)
        text = AiserverGenerateClient.output_text(payload)
    except Exception as exc:
        if pr:
            pr(f"LLM pattern for {symbol} failed ({exc}); using heuristics.")
        return None
    parsed = extract_json_object(text)
    if not isinstance(parsed, dict):
        if pr:
            pr(f"LLM pattern for {symbol} returned non-JSON; using heuristics.")
        return None
    coerced = _coerce_pattern(parsed)
    if coerced is not None:
        if pr:
            pr(f"LLM pattern for {symbol}: kind={coerced.get('kind')}")
        return coerced
    if pr:
        pr(f"LLM pattern for {symbol} failed validation; using heuristics.")
    return None
