"""
License: MIT
Description: Pull numeric / structured inputs for semiconductor cycle monitoring
(FRED macro proxies, Yahoo Finance peer inventory & price momentum, DRAMeXchange spot table,
optional Vast.ai GPU rental quotes, plus curated industry GPU-pricing URLs such as
SemiAnalysis). Used by ``run_semiconductor_cycle_signals.py``.

Data is best-effort: sources change layout, yfinance fields vary by ticker, and
FRED/Vast read API keys from the environment (``.env`` via ``load_connections_dotenv``).
Vast failures are returned in-json (``available: false``, ``reason``, ``errors``) and never raise.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from common.simple.user_dir import load_connections_dotenv
from common.simple.yfinance_warnings import suppress_utcnow_deprecation_warning

load_connections_dotenv()

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# FRED: https://fred.stlouisfed.org — IP semiconductors; durable goods new orders as capex appetite proxy.
DEFAULT_FRED_SERIES: tuple[tuple[str, str], ...] = (
    ("IPG3344S", "Industrial Production: Manufacturing: Semiconductor and Related Device Manufacturing"),
    ("UMDMNO", "Manufacturers' New Orders: Durable Goods: Nondefense Capital Goods Excluding Aircraft"),
)

# Named semiconductor peers (equities only — no sector ETFs like SOXX).
DEFAULT_INVENTORY_TICKERS: tuple[str, ...] = (
    "ARM",
    "AVGO",
    "MU",
    "SNDK",
    "INTC",
    "AMD",
    "ASML",
    "TSM",
    "NVDA",
    "LRCX",
)

# Curated references + public preview dashboard URL (tables fetched separately).
GPU_PRICING_INDUSTRY_SOURCES: tuple[dict[str, str], ...] = (
    {
        "publisher": "SemiAnalysis",
        "title": "GPU Pricing Index",
        "url": "https://semianalysis.com/gpu-pricing-index/",
        "public_preview_dashboard_url": "https://api.semianalysis.com/dashboards/gpu_spot_pricing_preview/",
        "access": (
            "Public HTML tables on api.semianalysis.com/dashboards/gpu_spot_pricing_preview/; "
            "full logged-in product may add features beyond the preview."
        ),
        "monitoring_role": (
            "Primary industry GPU / contract pricing benchmark tables (preview) + landing page. "
            "Present independent of Vast.ai (gpu_cloud_spot); Vast is optional spot-market color."
        ),
        "note": (
            "semi_analysis_page_snapshot fetches the preview dashboard tables and landing copy. "
            "Compare against gpu_cloud_spot when present. Subscriber GPU Pricing API is separate "
            "(Tools menu on semianalysis.com)."
        ),
    },
)

# Public Next.js preview (HTML tables) linked from the WordPress GPU Pricing Index page.
SEMIANALYSIS_GPU_INDEX_LANDING_URL = "https://semianalysis.com/gpu-pricing-index/"
SEMIANALYSIS_GPU_SPOT_PREVIEW_URL = "https://api.semianalysis.com/dashboards/gpu_spot_pricing_preview/"


def _extract_html_tables(html: str, *, max_rows_per_table: int = 32) -> list[dict[str, Any]]:
    """Parse <table> elements into row matrices."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    for ti, table in enumerate(soup.find_all("table")):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if any(c.strip() for c in cells):
                rows.append(cells)
        if rows:
            out.append(
                {
                    "table_index": ti,
                    "n_rows": len(rows),
                    "rows": rows[:max_rows_per_table],
                }
            )
    return out


def _tables_to_text_excerpt(tables: list[dict[str, Any]], *, max_chars: int = 12_000) -> str:
    """Flatten tables to a compact multi-line string for LLM context."""
    lines: list[str] = []
    for block in tables:
        idx = block.get("table_index", 0)
        lines.append(f"--- table {idx} ---")
        for row in block.get("rows") or []:
            lines.append(" | ".join(str(c) for c in row))
    text = "\n".join(lines)
    return text[:max_chars]


def fetch_semianalysis_gpu_pricing_index_snapshot(
    landing_url: str = SEMIANALYSIS_GPU_INDEX_LANDING_URL,
    preview_url: str = SEMIANALYSIS_GPU_SPOT_PREVIEW_URL,
    *,
    timeout: float = 35.0,
    max_landing_chars: int = 6_000,
) -> dict[str, Any]:
    """SemiAnalysis GPU pricing: public preview **tables** + WordPress landing copy; never raises.

    Chart-style figures are published on the public preview dashboard
    ``api.semianalysis.com/dashboards/gpu_spot_pricing_preview/`` (HTML tables), not only
    in the WordPress shell at semianalysis.com/gpu-pricing-index/.
    """
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*"}
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: dict[str, Any] = {
        "fetched_at_utc": fetched_at,
        "semi_analysis_index_landing_url": landing_url,
        "gpu_spot_pricing_preview_url": preview_url,
        "pricing_tables": [],
        "preview_fetch_error": None,
        "landing_fetch_error": None,
    }

    preview_html = ""
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            pr = client.get(preview_url)
            pr.raise_for_status()
            preview_html = pr.text
    except Exception as exc:
        out["preview_fetch_error"] = str(exc)

    if preview_html:
        tables = _extract_html_tables(preview_html)
        out["pricing_tables"] = tables
        psoup = BeautifulSoup(preview_html, "html.parser")
        out["preview_page_title"] = ((psoup.title and psoup.title.get_text(strip=True)) or "")[:400]
        og = psoup.find("meta", attrs={"property": "og:description"})
        out["preview_meta_description"] = ((og.get("content") or "").strip() if og else "")[:800]

    landing_html = ""
    landing_chunks: list[str] = []
    out["landing_text_excerpt"] = ""
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            lr = client.get(landing_url)
            lr.raise_for_status()
            landing_html = lr.text
    except Exception as exc:
        out["landing_fetch_error"] = str(exc)

    if landing_html:
        lsoup = BeautifulSoup(landing_html, "html.parser")
        out["landing_page_title"] = ((lsoup.title and lsoup.title.get_text(strip=True)) or "")[:400]
        desc_el = lsoup.find("meta", attrs={"name": "description"})
        out["landing_meta_description"] = ((desc_el.get("content") or "").strip() if desc_el else "")[:800]
        root = (
            lsoup.find("article")
            or lsoup.find("main")
            or lsoup.find("div", class_=re.compile(r"entry-content|post-content", re.I))
        )
        if root:
            for el in root.find_all(["p", "li", "h2", "h3"]):
                t = el.get_text(" ", strip=True)
                if len(t) > 35:
                    landing_chunks.append(t)
        else:
            for p in lsoup.find_all("p"):
                t = p.get_text(" ", strip=True)
                if len(t) > 50:
                    landing_chunks.append(t)
        out["landing_text_excerpt"] = "\n\n".join(landing_chunks)[:max_landing_chars]

    tables = out.get("pricing_tables") or []
    table_text = _tables_to_text_excerpt(tables) if tables else ""
    landing_txt = str(out.get("landing_text_excerpt") or "")
    combined = ""
    if out.get("preview_page_title"):
        combined += out["preview_page_title"] + "\n\n"
    if out.get("preview_meta_description"):
        combined += out["preview_meta_description"] + "\n\n"
    if table_text:
        combined += table_text + "\n\n"
    if landing_txt:
        combined += "--- landing page ---\n" + landing_txt
    out["text_excerpt"] = combined.strip()
    out["excerpt_char_count"] = len(out["text_excerpt"])
    out["paragraph_or_heading_blocks"] = len(landing_chunks)
    out["likely_sparse_public_index_content"] = not tables and len(combined.strip()) < 200
    out["note"] = (
        "Numeric GPU / contract columns come from the public preview dashboard HTML tables "
        f"({preview_url}). The WordPress page ({landing_url}) is mostly chrome; charts are in the preview."
    )
    # Back-compat keys used by older prompts / callers
    out["url"] = landing_url
    out["page_title"] = out.get("landing_page_title") or out.get("preview_page_title") or ""
    out["meta_description"] = out.get("landing_meta_description") or out.get("preview_meta_description") or ""
    if out["preview_fetch_error"] and not out["text_excerpt"]:
        out["error"] = out["preview_fetch_error"]
    return out


def build_gpu_pricing_data_strategy() -> dict[str, str]:
    """Stable HARD_DATA block: SemiAnalysis is primary; Vast is supplementary."""
    return {
        "semi_analysis_gpu_pricing_index_url": GPU_PRICING_INDUSTRY_SOURCES[0]["url"],
        "semi_analysis_gpu_spot_pricing_preview_url": SEMIANALYSIS_GPU_SPOT_PREVIEW_URL,
        "primary_path": (
            "SemiAnalysis GPU spot / contract pricing — HTML tables from the public preview "
            "dashboard (semi_analysis_page_snapshot.pricing_tables) plus landing copy; "
            "websearch digests in semi_analysis_public_web_context when present."
        ),
        "supplementary_path": (
            "gpu_cloud_spot (Vast.ai marketplace) — automated spot $/hr when the API succeeds; "
            "may be empty on filter/API issues and does not replace the SemiAnalysis index."
        ),
        "synthesis_instruction": (
            "Parse semi_analysis_page_snapshot.pricing_tables and text_excerpt for SKU rows, "
            "dollar spot index values, on-demand / contract columns, and period labels — cite them "
            "verbatim in signals (especially training_vs_inference). "
            "If preview_fetch_error is set, say so explicitly; do not invent rows. "
            "Vast (gpu_cloud_spot) remains supplementary."
        ),
    }


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _fred_observations(
    series_id: str,
    *,
    api_key: str,
    limit: int = 24,
    timeout: float = 25.0,
) -> dict[str, Any]:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": str(max(1, min(1000, limit))),
    }
    url = "https://api.stlouisfed.org/fred/series/observations"
    with httpx.Client(timeout=timeout) as client:
        r = client.get(url, params=params)
        if r.status_code != 200:
            return {"series_id": series_id, "error": f"HTTP {r.status_code}", "body_preview": (r.text or "")[:400]}
        data = r.json()
    obs = data.get("observations") or []
    out: list[dict[str, str]] = []
    for row in obs:
        if not isinstance(row, dict):
            continue
        d = row.get("date")
        val = row.get("value")
        if val in (".", None, ""):
            continue
        out.append({"date": str(d), "value": str(val)})
    return {"series_id": series_id, "observations": out, "count": len(out)}


def fetch_fred_bundle(
    *,
    api_key: str | None,
    series: tuple[tuple[str, str], ...] = DEFAULT_FRED_SERIES,
    limit_per_series: int = 18,
) -> dict[str, Any]:
    if not api_key or not api_key.strip():
        return {
            "available": False,
            "reason": "Set FRED_API_KEY for St. Louis Fed observations (free at fred.stlouisfed.org).",
            "series": [{"id": sid, "label": lab} for sid, lab in series],
        }
    key = api_key.strip()
    bundle: list[dict[str, Any]] = []
    errors: list[str] = []
    for sid, label in series:
        try:
            block = _fred_observations(sid, api_key=key, limit=limit_per_series)
            block["label"] = label
            bundle.append(block)
            if block.get("error"):
                errors.append(f"{sid}: {block['error']}")
        except Exception as exc:
            errors.append(f"{sid}: {exc}")
            bundle.append({"series_id": sid, "label": label, "error": str(exc), "observations": []})
    return {"available": True, "series": bundle, "errors": errors}


def _find_df_row(df: Any, needles: tuple[str, ...]) -> str | None:
    if df is None or not hasattr(df, "index"):
        return None
    for idx in df.index:
        s = str(idx).lower()
        if all(n in s for n in needles):
            return str(idx)
    for idx in df.index:
        s = str(idx).lower()
        if any(n in s for n in needles):
            return str(idx)
    return None


def _quarter_columns(df: Any, max_cols: int = 6) -> list[Any]:
    if df is None or getattr(df, "empty", True):
        return []
    cols = list(df.columns)
    # yfinance: columns are timestamps, newest first
    return cols[:max_cols]


def fetch_yfinance_inventory_dio(
    tickers: tuple[str, ...] = DEFAULT_INVENTORY_TICKERS,
) -> dict[str, Any]:
    suppress_utcnow_deprecation_warning()
    import yfinance as yf  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for sym in tickers:
        sym = sym.strip().upper()
        if not sym:
            continue
        rec: dict[str, Any] = {"symbol": sym, "quarters": []}
        try:
            t = yf.Ticker(sym)
            bs = getattr(t, "quarterly_balance_sheet", None)
            inc = getattr(t, "quarterly_income_stmt", None)
            if bs is None or getattr(bs, "empty", True):
                rec["error"] = "no quarterly_balance_sheet"
                rows.append(rec)
                continue
            inv_row = _find_df_row(bs, ("inventory",))
            cogs_row = _find_df_row(inc, ("cost of revenue",)) if inc is not None else None
            if not cogs_row and inc is not None and not getattr(inc, "empty", True):
                cogs_row = _find_df_row(inc, ("cost of goods sold",))
            rev_row = _find_df_row(inc, ("total revenue",)) if inc is not None else None
            if not inv_row:
                rec["error"] = "inventory row not found on balance sheet"
                rows.append(rec)
                continue

            any_rev_dio = False
            for col in _quarter_columns(bs, max_cols=5):
                inv = _safe_float(bs.at[inv_row, col]) if inv_row in bs.index else None
                cogs = None
                if inc is not None and not getattr(inc, "empty", True) and cogs_row and cogs_row in inc.index:
                    if col in inc.columns:
                        cogs = _safe_float(inc.at[cogs_row, col])
                rev = None
                if inc is not None and not getattr(inc, "empty", True) and rev_row and rev_row in inc.index:
                    if col in inc.columns:
                        rev = _safe_float(inc.at[rev_row, col])
                dio = None
                if inv is not None and cogs and cogs > 0:
                    dio = round((inv / cogs) * 91.25, 2)  # ~one quarter days
                elif inv is not None and rev and rev > 0:
                    dio = round((inv / rev) * 91.25, 2)
                    any_rev_dio = True
                q_label = col.isoformat()[:10] if hasattr(col, "isoformat") else str(col)[:10]
                rec["quarters"].append(
                    {
                        "period_end": q_label,
                        "inventory_usd": inv,
                        "cost_of_revenue_usd": cogs,
                        "total_revenue_usd": rev,
                        "inventory_days_proxy": dio,
                    }
                )
            if any_rev_dio and rec["quarters"]:
                rec["dio_note"] = (
                    "At least one quarter used inventory/revenue for inventory_days_proxy "
                    "(COGS missing or zero for that quarter). This is a rough proxy, not reported DOI."
                )
        except Exception as exc:
            rec["error"] = str(exc)
            errors.append(f"{sym}: {exc}")
        rows.append(rec)
    return {
        "source": "yfinance",
        "tickers": list(tickers),
        "rows": rows,
        "errors": errors,
        "note": "inventory_days_proxy ≈ 91.25 × inventory / quarterly COGS (or revenue fallback). Not reported company DOI.",
    }


def fetch_yfinance_peer_momentum(
    tickers: tuple[str, ...] = DEFAULT_INVENTORY_TICKERS,
    *,
    periods: tuple[tuple[str, str], ...] = (("1mo", "1 month"), ("3mo", "3 month"), ("6mo", "6 month")),
) -> dict[str, Any]:
    """Recent total-return % by period for each peer symbol (not ETFs)."""
    if not tickers:
        return {
            "source": "yfinance",
            "peer_momentum_pct": [],
            "errors": [],
            "note": "No tickers configured for peer momentum.",
        }
    suppress_utcnow_deprecation_warning()
    import yfinance as yf  # noqa: PLC0415

    out: list[dict[str, Any]] = []
    errors: list[str] = []
    for sym in tickers:
        sym = sym.strip().upper()
        item: dict[str, Any] = {"symbol": sym, "returns": {}}
        try:
            t = yf.Ticker(sym)
            for p_code, p_label in periods:
                hist = t.history(period=p_code, auto_adjust=True)
                if hist is None or hist.empty or len(hist) < 2:
                    item["returns"][p_label] = None
                    continue
                first = float(hist["Close"].iloc[0])
                last = float(hist["Close"].iloc[-1])
                if first and first > 0:
                    item["returns"][p_label] = round((last / first - 1.0) * 100.0, 2)
                else:
                    item["returns"][p_label] = None
        except Exception as exc:
            item["error"] = str(exc)
            errors.append(f"{sym}: {exc}")
        out.append(item)
    return {"source": "yfinance", "peer_momentum_pct": out, "errors": errors}


def fetch_yfinance_etf_momentum(
    tickers: tuple[str, ...] = DEFAULT_INVENTORY_TICKERS,
    *,
    periods: tuple[tuple[str, str], ...] = (("1mo", "1 month"), ("3mo", "3 month"), ("6mo", "6 month")),
) -> dict[str, Any]:
    """Backward-compatible alias for :func:`fetch_yfinance_peer_momentum`."""
    return fetch_yfinance_peer_momentum(tickers, periods=periods)


def fetch_dramexchange_dram_spot(
    url: str = "https://www.dramexchange.com/Price/Dram_Spot",
    *,
    timeout: float = 25.0,
) -> dict[str, Any]:
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            html = r.text
    except Exception as exc:
        return {"source": "dramexchange.com", "url": url, "error": str(exc), "samples": []}

    soup = BeautifulSoup(html, "html.parser")
    samples: list[dict[str, Any]] = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all("td")]
        if len(cells) < 7:
            continue
        name = cells[0]
        if "DDR" not in name and "DDR" not in "".join(cells):
            continue
        avg = cells[5] if len(cells) > 5 else ""
        chg = cells[6] if len(cells) > 6 else ""
        m = re.search(r"([-+]?\d+(?:\.\d+)?)\s*%", chg)
        pct = _safe_float(m.group(1)) if m else None
        avg_num = _safe_float(re.sub(r"[^\d.\-]", "", avg.replace(",", "")))
        samples.append(
            {
                "product": name[:120],
                "session_average_usd": avg_num,
                "session_change_pct": pct,
                "session_average_raw": avg,
                "session_change_raw": chg,
            }
        )
        if len(samples) >= 12:
            break

    return {
        "source": "dramexchange.com",
        "url": url,
        "scraped_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "samples": samples,
        "note": "Public spot table scrape; layout may change. Units are vendor spot averages (USD), not contract.",
    }


def _vast_offer_usd_per_hr(offer: dict[str, Any]) -> float | None:
    """Best-effort hourly USD from offer dict (see Vast OpenAPI offer schema)."""
    for key in (
        "dph_total",
        "discounted_dph_total",
        "dph_total_adj",
        "dph_base",
        "dph",
        "price",
    ):
        v = _safe_float(offer.get(key))
        if v is not None and v > 0:
            return v
    search = offer.get("search")
    if isinstance(search, dict):
        for sk in ("discountedTotalPerHour", "totalHour", "gpuCostPerHour"):
            v = _safe_float(search.get(sk))
            if v is not None and v > 0:
                return v
    return None


def fetch_vast_gpu_rental_summary(
    *,
    api_key: str | None,
    gpu_filters: tuple[str, ...] = ("RTX_4090", "RTX_5090", "H100_SXM", "H100_PCIE"),
    limit: int = 120,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Return Vast marketplace stats or a structured failure; never raises.

    Uses ``POST https://console.vast.ai/api/v0/bundles/`` with a JSON body per
    https://docs.vast.ai/api-reference/hello-world and
    https://docs.vast.ai/api-reference/search/search-offers (not legacy GET ``?q=``).
    """
    if not api_key or not api_key.strip():
        return {
            "source": "vast.ai",
            "available": False,
            "reason": "Set VAST_API_KEY in .env or the shell for marketplace quotes (https://cloud.vast.ai/manage-keys/).",
        }

    key = api_key.strip()
    bundles_url = "https://console.vast.ai/api/v0/bundles/"
    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": _USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    by_gpu: dict[str, Any] = {}
    errors: list[str] = []

    def _http_detail(resp: httpx.Response) -> str:
        text = (resp.text or "").strip().replace("\n", " ")
        if len(text) > 220:
            text = text[:220] + "…"
        return text

    try:
        lim = max(1, min(512, int(limit)))
        for gpu in gpu_filters:
            # Operator-style filters per OpenAPI; gpu_name uses catalog tokens (e.g. RTX_4090).
            base: dict[str, Any] = {
                "verified": {"eq": True},
                "rentable": {"eq": True},
                "rented": {"eq": False},
                "gpu_name": {"eq": gpu},
                "num_gpus": {"eq": 1},
                "direct_port_count": {"gte": 1},
                "order": [["dlperf_per_dphtotal", "desc"]],
                "limit": lim,
            }
            # Hello-world curl uses "on-demand"; OpenAPI enum lists "ondemand" — try both, then omit type.
            bodies = (
                {**base, "type": "on-demand"},
                {**base, "type": "ondemand"},
                dict(base),
            )
            try:
                with httpx.Client(timeout=timeout, headers=headers) as client:
                    r: httpx.Response | None = None
                    for body in bodies:
                        r = client.post(bundles_url, json=body)
                        if r.status_code == 200:
                            break
                        if r.status_code in (401, 403):
                            break
                    if r is None or r.status_code != 200:
                        hint = ""
                        if r is not None and r.status_code in (401, 403):
                            hint = " (check VAST_API_KEY is valid and not expired)"
                        detail = _http_detail(r) if r is not None else "no response"
                        code = r.status_code if r is not None else "?"
                        errors.append(f"{gpu}: HTTP {code}{hint} {detail}")
                        continue
                    try:
                        data = r.json()
                    except Exception as json_exc:
                        errors.append(f"{gpu}: response is not JSON ({json_exc!s}) {_http_detail(r)}")
                        continue
            except httpx.TimeoutException as exc:
                errors.append(f"{gpu}: timeout after {timeout}s ({exc!s})")
                continue
            except httpx.RequestError as exc:
                errors.append(f"{gpu}: network error ({exc!s})")
                continue
            except Exception as exc:
                errors.append(f"{gpu}: {exc!s}")
                continue

            offers = data.get("offers") if isinstance(data, dict) else None
            if not isinstance(offers, list):
                errors.append(f"{gpu}: unexpected response shape (no offers list)")
                continue

            dph_vals: list[float] = []
            for off in offers[:lim]:
                if not isinstance(off, dict):
                    continue
                v = _vast_offer_usd_per_hr(off)
                if v is not None:
                    dph_vals.append(v)

            n_sampled = min(len(offers), lim)
            if dph_vals:
                sorted_d = sorted(dph_vals)
                p10_idx = max(0, int(0.1 * (len(sorted_d) - 1)))
                by_gpu[gpu] = {
                    "n_offers_sampled": n_sampled,
                    "min_usd_per_hr": round(min(dph_vals), 4),
                    "p10_usd_per_hr": round(sorted_d[p10_idx], 4),
                    "median_usd_per_hr": round(sorted_d[len(sorted_d) // 2], 4),
                }
            else:
                errors.append(
                    f"{gpu}: {n_sampled} offer(s) but no positive hourly rate "
                    f"(checked dph_total and related fields per Vast offer schema)"
                )

        any_prices = any(
            isinstance(v, dict) and v.get("min_usd_per_hr") is not None for v in by_gpu.values()
        )
        if not any_prices:
            reason = (
                "VAST_API_KEY is set but no usable $/hr prices were extracted. "
                + (" Details: " + " | ".join(errors[:6]) if errors else "No per-GPU diagnostics.")
            )
            return {
                "source": "vast.ai",
                "available": False,
                "reason": reason[:900],
                "errors": errors,
                "by_gpu": by_gpu,
                "note": "API or filter shape may have changed; see https://docs.vast.ai/api-reference/hello-world",
            }

        out: dict[str, Any] = {
            "source": "vast.ai",
            "available": True,
            "currency": "USD_per_gpu_hour",
            "by_gpu": by_gpu,
            "errors": errors,
            "note": (
                "POST /api/v0/bundles/ per https://docs.vast.ai/api-reference/hello-world ; "
                "rates from dph_total (and fallbacks). Verify on console.vast.ai."
            ),
        }
        if errors:
            out["partial"] = True
        return out

    except Exception as exc:
        return {
            "source": "vast.ai",
            "available": False,
            "reason": f"Vast.ai integration failed unexpectedly: {exc!s}",
            "errors": [str(exc)],
        }


def collect_cycle_metrics(
    *,
    fred_api_key: str | None = None,
    vast_api_key: str | None = None,
    inventory_tickers: tuple[str, ...] | None = None,
    momentum_tickers: tuple[str, ...] | None = None,
    include_dram_spot: bool = True,
    include_fred: bool = True,
    include_yfinance: bool = True,
    include_vast: bool = True,
    include_semianalysis_html: bool = True,
) -> dict[str, Any]:
    """
    Aggregate all quantitative hooks. Missing keys/env vars yield partial results
    plus human-readable ``reason`` / ``errors`` fields — never raises.
    """
    fred_key = fred_api_key if fred_api_key is not None else os.environ.get("FRED_API_KEY", "")
    vast_key = vast_api_key if vast_api_key is not None else os.environ.get("VAST_API_KEY", "")
    inv = inventory_tickers or DEFAULT_INVENTORY_TICKERS
    mom = momentum_tickers if momentum_tickers is not None else inv

    out: dict[str, Any] = {
        "collected_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "book_to_bill_context": {
            "note": (
                "SEMI discontinued the North America equipment book-to-bill ratio series in 2017; "
                "use SEMI billings / WWSEMS press releases for bookings vs billings context, "
                "or industry sell-side aggregations. FRED proxies below are macro / orders, not book-to-bill."
            ),
            "suggested_primary_sources": [
                "https://www.semi.org/en/products-services/market-data/equipment/billings-report",
                "https://www.wsts.org/ (global shipment forecasts)",
            ],
        },
        "gpu_pricing_industry_sources": [dict(entry) for entry in GPU_PRICING_INDUSTRY_SOURCES],
        "gpu_pricing_data_strategy": build_gpu_pricing_data_strategy(),
    }
    if include_fred:
        out["fred"] = fetch_fred_bundle(api_key=fred_key or None)
    if include_yfinance:
        out["yahoo_inventory_quarters"] = fetch_yfinance_inventory_dio(inv)
        out["yahoo_peer_momentum"] = fetch_yfinance_peer_momentum(mom)
    if include_dram_spot:
        out["dram_spot"] = fetch_dramexchange_dram_spot()
    if include_vast:
        try:
            out["gpu_cloud_spot"] = fetch_vast_gpu_rental_summary(api_key=vast_key or None)
        except Exception as exc:
            out["gpu_cloud_spot"] = {
                "source": "vast.ai",
                "available": False,
                "reason": f"Vast.ai block raised unexpectedly: {exc!s}",
                "errors": [str(exc)],
            }
    if include_semianalysis_html:
        out["semi_analysis_page_snapshot"] = fetch_semianalysis_gpu_pricing_index_snapshot()
    return out


__all__ = [
    "DEFAULT_FRED_SERIES",
    "DEFAULT_INVENTORY_TICKERS",
    "GPU_PRICING_INDUSTRY_SOURCES",
    "build_gpu_pricing_data_strategy",
    "collect_cycle_metrics",
    "fetch_dramexchange_dram_spot",
    "fetch_semianalysis_gpu_pricing_index_snapshot",
    "fetch_fred_bundle",
    "fetch_vast_gpu_rental_summary",
    "fetch_yfinance_etf_momentum",
    "fetch_yfinance_inventory_dio",
    "fetch_yfinance_peer_momentum",
]
