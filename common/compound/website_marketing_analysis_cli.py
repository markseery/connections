"""
License: MIT
Description: Scrape a website via webscraper_skill (CLI defaults max_pages=1000, max_depth=10; both are clamped to the skill's configured caps),
then use the stored content to run multiple AI summaries: products, solutions, services,
positioning, brand identity, value proposition, and implied strategy.

Requires: registry, storage, worker (with webscraper_skill available), aiserver.

Usage:
  python scripts/website_marketing_analysis.py https://example.com
  python scripts/website_marketing_analysis.py https://example.com --out report.md
  python scripts/website_marketing_analysis.py https://example.com --out-dir application_files/data/website_marketing
  python scripts/website_marketing_analysis.py https://example.com --skip-scrape --diagnostics
  python scripts/website_marketing_analysis.py https://example.com --profile agent
  python scripts/website_marketing_analysis.py https://example.com --replace-stored
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from common.simple import script_env

from common.complex.skill_lifecycle import find_live_worker
from common.simple.user_dir import load_connections_dotenv


REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
MAX_CONTENT_CHARS = 120_000  # trim combined content so prompts stay within context
AI_TIMEOUT = 180.0
# Default aiserver profile for topic summaries. ``reason`` uses ``AISERVER_PROFILE_REASON_PROVIDER``
# or ``AISERVER_DEFAULT_PROVIDER`` (often ollama in dev). Override with ``--profile`` (e.g. ``agent`` for Anthropic).
DEFAULT_MARKETING_PROFILE = "reason"
MARKETING_PROFILE_CHOICES = (
    "fast",
    "chat",
    "reason",
    "agent",
    "local",
    "code",
    "image",
    "video",
    "search",
    "batch",
    "mlx",
)
SCRAPE_SKILL = "webscraper_skill"
# Match webscraper_skill combined page blocks (URL: line + --- separators).
_PAGE_SEP = "\n\n---\n\n"
_URL_LINE_PREFIX = "URL: "
_STORAGE_NAMESPACE = "webscrape"
# Must stay in sync with ``skills/webscraper_skill.py`` ``ScrapeRequest`` caps (config defaults).
_SCRAPE_MAX_PAGES_CAP = 10_000
_SCRAPE_MAX_DEPTH_CAP = 100

def _is_ai_section_error(text: str) -> bool:
    return (text or "").strip().startswith("[Error:")


def _aiserver_diag(enabled: bool, title: str, body: str) -> None:
    if not enabled:
        return
    bar = "=" * 72
    print(f"\n{bar}\n[aiserver diagnostics] {title}\n{bar}\n{body.rstrip()}\n", file=sys.stderr, flush=True)


def _aiserver_diag_line(enabled: bool, msg: str) -> None:
    if not enabled:
        return
    print(f"[aiserver diagnostics] {msg}", file=sys.stderr, flush=True)


def _format_prompt_for_diagnostics(prompt: str, max_chars: int) -> str:
    """max_chars 0 = length only; -1 = full prompt; else head slice."""
    n = len(prompt)
    if max_chars < 0:
        return prompt
    if max_chars == 0:
        return f"(prompt body omitted; total length={n} characters)"
    if n <= max_chars:
        return prompt
    return (
        prompt[:max_chars]
        + f"\n\n... [{n - max_chars} characters omitted from diagnostic tail; "
        "use --diagnostics-prompt-chars -1 for full prompt on stderr] ...\n"
    )


ANALYSIS_TOPICS = [
    "Headquarters",
    "Years in business",
    "Company executive names and titles",
    "number of data centers, their locations, GPUs, and power capacity",
    "active and contracted megawatts, terawatts, and other measures of power",
    "installed, active and future number of GPUs",
    "teraflops and exaflops",
    "customer names, locations, industry/type of entity, and other relevant details",
    "products",
    "solutions",
    "services",
    "ideal customer profile",
    "positioning statements",
    "overall brand identity",
    "value proposition statements",
    "the implied strategy for how it believes it will win",
]


def marketing_aiserver_401_diagnosis(*, aiserver_url: str, profile: str | None = None) -> str:
    """
    Human-readable explanation when the marketing report is full of ``[Error: ... 401 ...]`` lines.

    The scrape is usually fine; aiserver forwards upstream LLM auth failures as HTTP 401.
    """
    prof = profile or DEFAULT_MARKETING_PROFILE
    env_prof = f"AISERVER_PROFILE_{prof.upper()}_PROVIDER"
    base = aiserver_url.rstrip("/")
    unknown = profile is None
    head = (
        "\n--- Why the markdown looks like \"crap\" ---\n"
        f"- The header still shows a stored scrape (URLs) because **webscraper_skill** worked.\n"
    )
    if unknown:
        head += (
            "- Each topic section was one POST /generate; if the report has a line "
            "'Aiserver profile: …', fix keys for **that** profile (e.g. AISERVER_PROFILE_AGENT_PROVIDER for agent).\n"
        )
    else:
        head += (
            f"- Each topic section is one call to **POST {base}/generate** with profile **{prof!r}** "
            "(summarize_via_ai in this script).\n"
        )
    mid = (
        "- **401 Unauthorized** on that URL is almost always the **LLM provider behind aiserver** rejecting the "
        "call (invalid/expired API key, wrong inference endpoint, or account not allowed for that model). "
        "Aiserver forwards upstream HTTP status (see servers/aiserver/routes.py).\n"
    )
    if unknown:
        fix = (
            "- Fix: read 'Aiserver profile: …' in the report; set AISERVER_PROFILE_<PROFILE>_PROVIDER and the "
            "matching API keys on the aiserver host (see servers/aiserver/config.py). Or regenerate with "
            f"--profile {DEFAULT_MARKETING_PROFILE} (default) to use that profile's provider mapping.\n"
        )
    else:
        fix = (
            f"- Fix: on the host running aiserver, set **{env_prof}** (or **AISERVER_DEFAULT_PROVIDER**) and the matching "
            "API key in .env — e.g. OPENAI_API_KEY, ANTHROPIC_API_KEY, WANDB_API_KEY, XAI_API_KEY, … — "
            "see servers/aiserver/config.py and config/aiserver.yaml. For local-only runs, try "
            "--profile local (ollama) or --profile reason with default provider ollama.\n"
        )
    foot = (
        "- This script does **not** send a separate login to aiserver; only JSON keys prompt and profile "
        "(same idea as other CLIs using AiserverGenerateClient).\n"
        "- After fixing keys or switching profile, regenerate: python scripts/website_marketing_analysis.py <url> --skip-scrape\n"
    )
    return head + mid + fix + foot


def _worker_url() -> str:
    url = find_live_worker(REGISTRY_URL)
    if not url:
        raise RuntimeError("No live worker found in registry")
    return url.rstrip("/")


def _aiserver_url() -> str:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{REGISTRY_URL}/servers/aiserver")
        r.raise_for_status()
        u = (r.json() or {}).get("url")
        if not u:
            raise ValueError("Registry missing url for aiserver")
        return str(u).rstrip("/")


def _ensure_skill_loaded(worker_url: str) -> None:
    """
    Ensure the worker has the scraper skill mounted.

    Some worker deployments auto-load skills on first request; others require an explicit load call.
    """
    with httpx.Client(timeout=15.0) as client:
        r = client.post(f"{worker_url}/worker/skills/{SCRAPE_SKILL}/load")
        r.raise_for_status()


def _unwrap_skill_output(payload: Any) -> dict[str, Any]:
    """
    webscraper_skill returns the canonical SkillOutput shape: {summary, items, text, data}.
    Older scripts expected the payload fields at the top-level.
    """
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _strip_url_query(page_url: str) -> str:
    u = (page_url or "").strip()
    q = u.find("?")
    return u[:q] if q >= 0 else u


def _urls_from_pages_urls_payload(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw = data.get("urls")
    if not isinstance(raw, list):
        return []
    return sorted({str(u).strip() for u in raw if str(u).strip()})


def clear_stored_pages(worker_url: str, base_url: str, *, namespace: str = _STORAGE_NAMESPACE) -> int:
    """
    Delete all stored pages for ``base_url`` (sitename) in the webscraper namespace.

    Returns the number of unique page paths deleted (0 if none were stored).
    """
    _ensure_skill_loaded(worker_url)
    ns = (namespace or "").strip() or _STORAGE_NAMESPACE
    sitename = base_url.strip()
    list_q = urlencode({"sitename": sitename, "namespace": ns})
    deleted_paths = 0
    with httpx.Client(timeout=120.0) as client:
        r = client.get(f"{worker_url}/skills/{SCRAPE_SKILL}/pages/urls?{list_q}")
        r.raise_for_status()
        urls = _urls_from_pages_urls_payload(_unwrap_skill_output(r.json()))
        if not urls:
            return 0
        norms = sorted({_strip_url_query(u) for u in urls})
        for page_url in norms:
            del_q = urlencode({"namespace": ns, "sitename": sitename, "url": page_url})
            dr = client.delete(f"{worker_url}/skills/{SCRAPE_SKILL}/pages?{del_q}")
            if dr.status_code == 404:
                continue
            dr.raise_for_status()
            deleted_paths += 1
    return deleted_paths


def scrape_and_store(worker_url: str, url: str, max_pages: int, max_depth: int) -> dict[str, Any]:
    """Start webscraper_skill scrape job; return job start payload."""
    _ensure_skill_loaded(worker_url)
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{worker_url}/skills/{SCRAPE_SKILL}/scrape",
            json={
                "url": url,
                "max_pages": max_pages,
                "max_depth": max_depth,
                "wait": False,
                "summarize": False,
            },
        )
        r.raise_for_status()
    return _unwrap_skill_output(r.json())


def get_scrape_job(worker_url: str, job_id: str) -> dict[str, Any]:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{worker_url}/skills/{SCRAPE_SKILL}/scrape/{job_id}")
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("Job status response is not a JSON object")
        return _unwrap_skill_output(data)


def wait_for_scrape(worker_url: str, job_id: str, timeout_s: float, poll_s: float) -> dict[str, Any]:
    start = time.monotonic()
    last_line = ""
    while True:
        job = get_scrape_job(worker_url, job_id)
        status = str(job.get("status") or "unknown")
        crawled = int(job.get("pages_crawled") or 0)
        skipped = int(job.get("pages_skipped") or 0)
        failed = int(job.get("pages_failed") or 0)
        visited = int(job.get("urls_visited") or 0)
        max_pages = int(job.get("max_pages") or 0)
        elapsed = time.monotonic() - start

        line = (
            f"  progress: status={status} crawled={crawled}/{max_pages} "
            f"visited={visited} skipped={skipped} failed={failed} elapsed={elapsed:.0f}s"
        )
        if line != last_line:
            print(line, flush=True)
            last_line = line

        if status in {"completed", "failed"}:
            return job
        if elapsed > timeout_s:
            raise TimeoutError(f"Scrape job timed out after {timeout_s:.0f}s (job_id={job_id})")
        time.sleep(poll_s)


def get_stored_content(worker_url: str, base_url: str) -> dict[str, Any]:
    """Fetch stored scrape for base_url from skill (reads from storage)."""
    _ensure_skill_loaded(worker_url)
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{worker_url}/skills/{SCRAPE_SKILL}/stored",
            params={"base_url": base_url},
        )
        r.raise_for_status()
    payload = _unwrap_skill_output(r.json())
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        raise ValueError("Stored scrape has no value")
    return value


def _format_page_for_combined(page_url: str, text: str) -> str:
    """One stored page block: current page URL then body (matches webscraper_skill)."""
    raw = (text or "").strip()
    u = (page_url or "").strip()
    if raw.startswith(_URL_LINE_PREFIX):
        return raw
    if not u:
        return raw
    return f"{_URL_LINE_PREFIX}{u}\n\n{raw}"


def combined_text(value: dict[str, Any], max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Build one text block from content_by_url, optionally truncated."""
    content_by_url = value.get("content_by_url") or {}
    parts: list[str] = []
    total = 0
    for u in sorted(content_by_url.keys()):
        if total >= max_chars:
            break
        block = _format_page_for_combined(str(u), str(content_by_url.get(u) or ""))
        chunk = block[: max_chars - total]
        if chunk.strip():
            parts.append(chunk)
            total += len(chunk)
    return _PAGE_SEP.join(parts) if parts else ""


def summarize_via_ai(
    aiserver_url: str,
    topic: str,
    content: str,
    profile: str = DEFAULT_MARKETING_PROFILE,
    *,
    diagnostics: bool = False,
    diagnostics_prompt_chars: int = 8_000,
    topic_index: tuple[int, int] | None = None,
) -> tuple[str, dict[str, Any]]:
    """One AI call: summarize content with focus on topic. Returns (text, aiserver response meta)."""
    prompt = (
        f"Summarize the following website content with focus on: **{topic}**. "
        "Be concise; use bullet points where helpful. If the site does not clearly address this topic, say so.\n\n"
        f"{content}"
    )
    base = aiserver_url.rstrip("/")
    endpoint = f"{base}/generate"
    payload: dict[str, Any] = {"prompt": prompt, "profile": profile}

    if diagnostics:
        idx = f"{topic_index[0]}/{topic_index[1]}" if topic_index else "?"
        _aiserver_diag_line(diagnostics, f"topic {idx!r}: preparing POST {endpoint}")
        _aiserver_diag(
            diagnostics,
            f"REQUEST JSON (topic {idx}; same keys as AiserverGenerateClient /generate)",
            json.dumps(
                {
                    "url": endpoint,
                    "method": "POST",
                    "json_keys": sorted(payload.keys()),
                    "profile": profile,
                    "prompt_total_chars": len(prompt),
                    "prompt_body_for_diagnostics": _format_prompt_for_diagnostics(
                        prompt, diagnostics_prompt_chars
                    ),
                },
                indent=2,
                ensure_ascii=False,
            ),
        )

    with httpx.Client(timeout=AI_TIMEOUT) as client:
        r = client.post(endpoint, json=payload)

    if diagnostics:
        idx = f"{topic_index[0]}/{topic_index[1]}" if topic_index else "?"
        hdrs = {k: v for k, v in r.headers.items() if k.lower() in {"content-type", "content-length"}}
        if r.is_success:
            body_note = "JSON body follows in the next diagnostic block."
        else:
            body_preview = (r.text or "")[:16_000]
            if len(r.text or "") > 16_000:
                body_preview += "\n... [response body truncated for diagnostics] ..."
            body_note = f"body (text, up to 16k chars):\n{body_preview}"
        _aiserver_diag(
            diagnostics,
            f"HTTP RESPONSE (topic {idx})",
            f"status_code={r.status_code}\nheaders={hdrs!r}\n{body_note}",
        )

    try:
        r.raise_for_status()
    except Exception:
        if diagnostics and r.headers.get("content-type", "").startswith("application/json"):
            try:
                err_obj = r.json()
                _aiserver_diag(
                    diagnostics,
                    f"RESPONSE JSON (error path, topic {idx})",
                    json.dumps(err_obj, indent=2, ensure_ascii=False, default=str),
                )
            except Exception:
                pass
        raise

    out = r.json()
    if diagnostics:
        try:
            raw_json = json.dumps(out, indent=2, ensure_ascii=False, default=str)
        except TypeError:
            raw_json = repr(out)
        _aiserver_diag(diagnostics, f"RESPONSE JSON (success, topic {idx})", raw_json)

    meta = {
        "provider": out.get("provider"),
        "profile": out.get("profile"),
        "model": out.get("model"),
    }
    output = out.get("output") if isinstance(out.get("output"), dict) else out
    if isinstance(output, dict) and "text" in output:
        text = str(output["text"]).strip()
    else:
        text = str(output).strip() if output else ""
    if diagnostics:
        _aiserver_diag(
            diagnostics,
            f"PARSED MODEL TEXT (topic {idx})",
            text or "(empty after parsing output.text)",
        )
    return text, meta


def run_analyses(
    aiserver_url: str,
    content: str,
    *,
    profile: str = DEFAULT_MARKETING_PROFILE,
    diagnostics: bool = False,
    diagnostics_prompt_chars: int = 8_000,
) -> dict[str, str]:
    """Run all marketing topic summaries; return topic -> summary."""
    results: dict[str, str] = {}
    provider_reported = False
    n_topics = len(ANALYSIS_TOPICS)
    if diagnostics:
        _aiserver_diag(
            diagnostics,
            "CALL PATTERN (same as other repo CLIs)",
            "Each topic uses raw httpx POST to {base}/generate with JSON "
            '{{"prompt": "<string>", "profile": "<string>"}} — the same endpoint and body shape as '
            "common.compound.aiserver_generate_client.AiserverGenerateClient (e.g. run_semiconductor_cycle_signals.py); "
            "this script does not send a separate provider field, so aiserver picks the provider from "
            f"AISERVER_PROFILE_{profile.upper()}_PROVIDER or AISERVER_DEFAULT_PROVIDER.".format(
                base=aiserver_url.rstrip("/")
            ),
        )
        try:
            mi_url = f"{aiserver_url.rstrip('/')}/model-info"
            with httpx.Client(timeout=10.0) as c:
                mr = c.get(mi_url, params={"profile": profile})
                mr.raise_for_status()
                info = mr.json()
            _aiserver_diag(
                diagnostics,
                "RESOLVED PROVIDER + MODEL (GET /model-info; same resolution as first /generate)",
                json.dumps(info, indent=2, ensure_ascii=False, default=str),
            )
        except Exception as ex:
            _aiserver_diag_line(
                diagnostics,
                f"GET /model-info?profile={profile!r} failed (aiserver may be down): {ex!r}",
            )
    for i, topic in enumerate(ANALYSIS_TOPICS, start=1):
        print(f"  Summarizing: {topic} ...", flush=True)
        try:
            text, meta = summarize_via_ai(
                aiserver_url,
                topic,
                content,
                profile,
                diagnostics=diagnostics,
                diagnostics_prompt_chars=diagnostics_prompt_chars,
                topic_index=(i, n_topics),
            )
            results[topic] = text
            if not provider_reported:
                provider_reported = True
                prov = meta.get("provider") or "?"
                model = meta.get("model") or "?"
                print(f"  (aiserver: profile={profile}, provider={prov}, model={model})", flush=True)
                if prov != "wandb":
                    env_hint = f"AISERVER_PROFILE_{profile.upper()}_PROVIDER"
                    print(
                        f"  WARNING: provider is {prov!r}, not wandb. Set {env_hint}=wandb in .env to use W&B.",
                        flush=True,
                    )
        except Exception as e:
            results[topic] = f"[Error: {e}]"
            if diagnostics:
                _aiserver_diag_line(diagnostics, f"topic {i}/{n_topics}: exception propagated as [Error: ...] — {e!r}")
    return results


def _default_marketing_report_path(out_dir: Path, url: str) -> Path:
    parsed = urlparse(url)
    host = (parsed.netloc or "site").replace(":", "_")
    path_bit = (parsed.path or "").strip("/").replace("/", "_")[:80] or "root"
    raw = f"{host}_{path_bit}"
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in raw)[:160]
    return out_dir / f"marketing_{safe}.md"


def default_marketing_report_path_for_url(url: str, *, out_dir: Path | str | None = None) -> Path:
    """
    Default markdown path written by :func:`main` for ``url`` (same rules as ``--out-dir`` / default filename).

    ``url`` is normalized the same way as the marketing-analysis CLI (``https://`` added when missing).
    """
    u = (url or "").strip()
    if not u.startswith("http://") and not u.startswith("https://"):
        u = "https://" + u
    base = script_env.resolve_output_dir(out_dir, segment="website_marketing")
    return _default_marketing_report_path(base, u)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape a website, store content, then run marketing analysis summaries via AI."
    )
    parser.add_argument("url", help="Website URL to analyze (e.g. https://example.com)")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory for the default report when --out is omitted (default: "
            "application_files/data/website_marketing). Relative paths are from the repo root."
        ),
    )
    parser.add_argument(
        "--out",
        default="",
        help=(
            "Markdown report path. Use - for stdout only. "
            "If omitted, writes marketing_<host>_<path>.md under --out-dir."
        ),
    )
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Skip scrape; use already-stored content for base URL",
    )
    parser.add_argument(
        "--replace-stored",
        action="store_true",
        help=(
            "Before scraping, delete all pages already stored for this site URL "
            f"(namespace {_STORAGE_NAMESPACE!r}), then crawl and store fresh pages. "
            "Incompatible with --skip-scrape."
        ),
    )
    parser.add_argument(
        "--scrape-timeout",
        type=float,
        default=3600.0,
        help="Max seconds to wait for scraping job to complete (default: 3600)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between scrape progress polls (default: 2.0)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1000,
        help=(
            f"Maximum pages to crawl (default: 1000, clamped 1–{_SCRAPE_MAX_PAGES_CAP}; "
            "worker may apply a lower limit via webscraper_skill config)."
        ),
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=10,
        help=(
            f"Maximum crawl depth (default: 10, clamped 1–{_SCRAPE_MAX_DEPTH_CAP}; "
            "worker may apply a lower limit via webscraper_skill config)."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=MARKETING_PROFILE_CHOICES,
        default=DEFAULT_MARKETING_PROFILE,
        metavar="PROFILE",
        help=(
            f"Aiserver profile for each /generate call (default: {DEFAULT_MARKETING_PROFILE}). "
            "Provider is chosen by aiserver from AISERVER_PROFILE_<PROFILE>_PROVIDER or AISERVER_DEFAULT_PROVIDER. "
            "Use local for ollama, or agent if you intend Anthropic/etc. for that profile."
        ),
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help=(
            "Log each aiserver /generate call on stderr: endpoint, HTTP status, response body preview, "
            "full JSON on success, and prompt slice (see --diagnostics-prompt-chars)."
        ),
    )
    parser.add_argument(
        "--diagnostics-prompt-chars",
        type=int,
        default=8_000,
        metavar="N",
        help=(
            "With --diagnostics: include up to N characters of each prompt in the diagnostic JSON "
            "(default 8000). Use 0 to omit prompt text (lengths only). Use -1 to print the entire prompt "
            "(can be huge, repeated per topic)."
        ),
    )
    args = parser.parse_args()
    load_connections_dotenv()
    out_dir = script_env.resolve_output_dir(args.out_dir, segment="website_marketing")
    url = args.url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    try:
        worker_url = _worker_url()
        aiserver_url = _aiserver_url()
    except Exception as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    if args.diagnostics:
        _aiserver_diag_line(
            True,
            f"registry={REGISTRY_URL!r} worker={worker_url!r} aiserver={aiserver_url!r}",
        )

    if args.replace_stored and args.skip_scrape:
        print("Error: --replace-stored cannot be used with --skip-scrape.", file=sys.stderr)
        return 1

    if not args.skip_scrape:
        max_pages = max(1, min(_SCRAPE_MAX_PAGES_CAP, int(args.max_pages)))
        max_depth = max(1, min(_SCRAPE_MAX_DEPTH_CAP, int(args.max_depth)))
        if int(args.max_pages) != max_pages or int(args.max_depth) != max_depth:
            print(
                f"  Note: crawl limits clamped to max_pages={max_pages}, max_depth={max_depth} "
                f"(requested {int(args.max_pages)}, {int(args.max_depth)}).",
                flush=True,
            )
        if args.replace_stored:
            print(f"Clearing stored pages for {url!r} (namespace {_STORAGE_NAMESPACE}) ...", flush=True)
            try:
                removed = clear_stored_pages(worker_url, url)
                print(f"  Removed {removed} stored page path(s).", flush=True)
            except Exception as e:
                print(f"Failed to clear stored pages: {e}", file=sys.stderr)
                return 1
        print(f"Scraping site (max_pages={max_pages}, max_depth={max_depth}) ...", flush=True)
        try:
            started = scrape_and_store(worker_url, url, max_pages=max_pages, max_depth=max_depth)
            job_id = str(started.get("job_id") or "")
            if not job_id:
                raise RuntimeError(f"Missing job_id in response: {started!r}")
            print(f"  job_id={job_id} (polling for progress)", flush=True)
            final = wait_for_scrape(
                worker_url=worker_url,
                job_id=job_id,
                timeout_s=float(args.scrape_timeout),
                poll_s=float(args.poll_interval),
            )
            if str(final.get("status")) != "completed":
                raise RuntimeError(str(final.get("error") or "scrape failed"))
            print(
                f"  Stored {final.get('url_count', 0)} pages at key {final.get('url', '')}",
                flush=True,
            )
        except Exception as e:
            print(f"Scrape failed: {e}", file=sys.stderr)
            return 1
    else:
        print("Skipping scrape (using existing stored content).", flush=True)

    print("Loading stored content ...", flush=True)
    try:
        value = get_stored_content(worker_url, url)
    except Exception as e:
        print(f"Failed to load stored content: {e}", file=sys.stderr)
        return 1

    content = combined_text(value)
    if not content:
        print("No content in stored scrape.", file=sys.stderr)
        return 1
    print(f"  Using {len(content)} chars from {len(value.get('content_by_url') or {})} pages.", flush=True)

    print(f"Using profile: {args.profile}", flush=True)
    print(f"Running AI marketing analyses ...", flush=True)
    if args.diagnostics:
        _aiserver_diag_line(
            True,
            f"combined scrape text length={len(content)} chars (cap MAX_CONTENT_CHARS={MAX_CONTENT_CHARS})",
        )
    try:
        analyses = run_analyses(
            aiserver_url,
            content,
            profile=str(args.profile),
            diagnostics=bool(args.diagnostics),
            diagnostics_prompt_chars=int(args.diagnostics_prompt_chars),
        )
    except Exception as e:
        print(f"AI analysis failed: {e}", file=sys.stderr)
        return 1

    n_topics = len(ANALYSIS_TOPICS)
    n_err = sum(1 for t in ANALYSIS_TOPICS if _is_ai_section_error(analyses.get(t, "")))
    if n_err == n_topics:
        print(
            "\nAll marketing aiserver calls failed — no report was written.\n"
            "Typical cause: HTTP 401 Unauthorized on POST {aiserver}/generate (missing or invalid API keys, "
            "wrong provider, or aiserver not configured for profile "
            f"{args.profile!r}). Check `.env` and aiserver logs.\n"
            "After fixing auth, re-run with `--skip-scrape` to reuse the crawl you already stored.\n".format(
                aiserver=aiserver_url.rstrip("/")
            ),
            file=sys.stderr,
        )
        sample = next(
            (analyses.get(t, "") for t in ANALYSIS_TOPICS if _is_ai_section_error(analyses.get(t, ""))),
            "",
        )
        if sample:
            print(f"Example: {sample[:500]}\n", file=sys.stderr)
        if "401" in "".join(analyses.get(t, "") for t in ANALYSIS_TOPICS):
            print(
                marketing_aiserver_401_diagnosis(aiserver_url=aiserver_url, profile=str(args.profile)),
                file=sys.stderr,
            )
        return 1
    if n_err > 0:
        print(
            f"Warning: {n_err}/{n_topics} topic aiserver calls failed; "
            "report still contains [Error: ...] for those sections.",
            file=sys.stderr,
        )

    lines = [
        f"# Marketing analysis: {url}",
        "",
        f"*Based on stored scrape ({len(value.get('urls') or [])} URLs). Aiserver profile: {args.profile}.*",
        "",
        "---",
        "",
    ]
    for topic in ANALYSIS_TOPICS:
        lines.append(f"## {topic.title()}")
        lines.append("")
        lines.append(analyses.get(topic, ""))
        lines.append("")
        lines.append("---")
        lines.append("")

    report = "\n".join(lines)
    raw_out = (args.out or "").strip()
    if raw_out == "-":
        print(report)
        return 0
    if raw_out:
        out_path = Path(args.out).expanduser()
        out_path = out_path if out_path.is_absolute() else (out_dir / out_path)
    else:
        out_path = _default_marketing_report_path(out_dir, url)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"Report written to {out_path}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
