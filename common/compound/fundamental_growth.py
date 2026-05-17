"""YoY and QoQ growth on key metrics from SEC quarterly facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from common.compound.sec_company_fundamentals import SecCompanyFundamentals

_Q_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}

# (display_name, taxonomy, tag, fallback (taxonomy, tag) optional)
_METRIC_SPECS: Tuple[Tuple[str, str, str, Optional[Tuple[str, str]]], ...] = (
    ("revenue", "us-gaap", "Revenues", ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax")),
    ("operating_income", "us-gaap", "OperatingIncomeLoss", None),
    ("nonoperating_income_expense", "us-gaap", "NonoperatingIncomeExpense", ("us-gaap", "OtherNonoperatingIncomeExpense")),
    ("net_income", "us-gaap", "NetIncomeLoss", None),
    ("free_cash_flow", "us-gaap", "FreeCashFlow", None),
)


@dataclass
class MetricGrowth:
    metric: str
    taxonomy: str
    tag: str
    latest_fy: Optional[int]
    latest_fp: Optional[str]
    latest_end: Optional[str]
    latest_value: Optional[float]
    prior_quarter_value: Optional[float]
    prior_year_value: Optional[float]
    qoq_pct: Optional[float]
    yoy_pct: Optional[float]
    note: Optional[str] = None


@dataclass
class GrowthAnalysisResult:
    ticker: str
    entity_name: Optional[str]
    metrics: List[MetricGrowth]


def _dedupe_quarters(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep one row per (fy, fp) preferring latest *filed* date when present."""
    best: Dict[Tuple[int, str], Dict[str, Any]] = {}
    for u in rows:
        fp = u.get("fp")
        fy = u.get("fy")
        if fp not in _Q_ORDER or fy is None:
            continue
        try:
            fy_i = int(fy)
        except (TypeError, ValueError):
            continue
        key = (fy_i, str(fp))
        prev = best.get(key)
        if prev is None:
            best[key] = u
            continue
        f_new = str(u.get("filed") or "")
        f_old = str(prev.get("filed") or "")
        if f_new >= f_old:
            best[key] = u
    out = list(best.values())
    out.sort(key=lambda r: (int(r["fy"]), _Q_ORDER[str(r["fp"])]))
    return out


def _filter_quarterly_usd(units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for u in units:
        form = str(u.get("form") or "")
        fp = str(u.get("fp") or "")
        if fp not in _Q_ORDER:
            continue
        if form not in ("10-Q", "10-K", "20-F", "40-F"):
            continue
        if form == "10-K" and fp != "Q4":
            # annual sometimes tagged oddly; keep Q4 from 10-K only if fp is Q4
            pass
        end = u.get("end")
        val = u.get("val")
        if end is None or val is None:
            continue
        try:
            vf = float(val)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "end": str(end),
                "val": vf,
                "fy": u.get("fy"),
                "fp": fp,
                "form": form,
                "filed": u.get("filed"),
            }
        )
    return _dedupe_quarters(rows)


def _quarterly_series(fund: SecCompanyFundamentals, taxonomy: str, tag: str) -> List[Dict[str, Any]]:
    units = fund.get_fact_units(taxonomy, tag)
    return _filter_quarterly_usd(units)


def _pick_series(fund: SecCompanyFundamentals, tax: str, tag: str, fb: Optional[Tuple[str, str]]) -> Tuple[List[Dict[str, Any]], str, str, Optional[str]]:
    s = _quarterly_series(fund, tax, tag)
    if s:
        return s, tax, tag, None
    if fb:
        s2 = _quarterly_series(fund, fb[0], fb[1])
        if s2:
            return s2, fb[0], fb[1], f"used_fallback_tag:{fb[1]}"
    return [], tax, tag, "no_quarterly_data"


def _pct_change(old: Optional[float], new: Optional[float]) -> Optional[float]:
    if old is None or new is None:
        return None
    if old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)


def _approx_fcf_quarterly(fund: SecCompanyFundamentals) -> List[Dict[str, Any]]:
    """OCF minus capex when ``FreeCashFlow`` is absent; matches quarters on (fy, fp)."""
    ocf = _quarterly_series(fund, "us-gaap", "NetCashProvidedByUsedInOperatingActivities")
    capex = _quarterly_series(fund, "us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment")
    if not ocf or not capex:
        return []
    cap_by: Dict[Tuple[int, str], float] = {}
    for r in capex:
        try:
            cap_by[(int(r["fy"]), str(r["fp"]))] = float(r["val"])
        except (TypeError, ValueError, KeyError):
            continue
    merged: List[Dict[str, Any]] = []
    for r in ocf:
        try:
            key = (int(r["fy"]), str(r["fp"]))
        except (TypeError, ValueError, KeyError):
            continue
        c = cap_by.get(key)
        if c is None:
            continue
        o = float(r["val"])
        # CapEx is usually negative in US-GAAP cash flow tags; if positive, treat as outflow magnitude.
        fcf = o + c if c <= 0 else o - abs(c)
        merged.append(
            {
                "end": r["end"],
                "val": fcf,
                "fy": r["fy"],
                "fp": r["fp"],
                "form": r["form"],
                "filed": r.get("filed"),
            }
        )
    return _dedupe_quarters(merged)


def _prior_year_point(series: List[Dict[str, Any]], latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        fy = int(latest["fy"])
        fp = str(latest["fp"])
    except (TypeError, ValueError, KeyError):
        return None
    for p in reversed(series):
        if int(p["fy"]) == fy - 1 and str(p["fp"]) == fp:
            return p
    return None


class FundamentalGrowth:
    """Compute QoQ / YoY percentage changes on standard GAAP quarterly metrics."""

    def analyze(self, fund: SecCompanyFundamentals) -> GrowthAnalysisResult:
        entity = fund.entity_name()
        out: List[MetricGrowth] = []
        for name, tax, tag, fb in _METRIC_SPECS:
            series, rt, rtag, note = _pick_series(fund, tax, tag, fb)
            if name == "free_cash_flow" and not series:
                approx = _approx_fcf_quarterly(fund)
                if approx:
                    series, rt, rtag, note = approx, "us-gaap", "OCF_minus_Capex_derived", "approx_ocf_minus_capex"
            if len(series) < 1:
                out.append(
                    MetricGrowth(
                        metric=name,
                        taxonomy=rt,
                        tag=rtag,
                        latest_fy=None,
                        latest_fp=None,
                        latest_end=None,
                        latest_value=None,
                        prior_quarter_value=None,
                        prior_year_value=None,
                        qoq_pct=None,
                        yoy_pct=None,
                        note=note or "no_data",
                    )
                )
                continue
            latest = series[-1]
            pq = series[-2] if len(series) >= 2 else None
            py = _prior_year_point(series, latest)
            lv = latest["val"]
            pv_q = pq["val"] if pq else None
            pv_y = py["val"] if py else None
            out.append(
                MetricGrowth(
                    metric=name,
                    taxonomy=rt,
                    tag=rtag,
                    latest_fy=int(latest["fy"]),
                    latest_fp=str(latest["fp"]),
                    latest_end=str(latest["end"]),
                    latest_value=round(lv, 2),
                    prior_quarter_value=round(pv_q, 2) if pv_q is not None else None,
                    prior_year_value=round(pv_y, 2) if pv_y is not None else None,
                    qoq_pct=_pct_change(pv_q, lv),
                    yoy_pct=_pct_change(pv_y, lv),
                    note=note,
                )
            )
        return GrowthAnalysisResult(ticker=fund.ticker, entity_name=entity, metrics=out)
