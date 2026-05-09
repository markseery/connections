#!/usr/bin/env python3
"""
Consolidate per-profile analysis reports into a single file with an
AI-generated executive summary, and produce a formatted PDF.

Given a site name (e.g. "coreweave"), locates the most recent report for
each profile directory under application_files/data/reports/, combines
them, calls the AI server to produce an executive summary, and writes
the consolidated result to application_files/data/reports/consolidated/
as both Markdown and PDF.

Usage:
  python3 consolidate_reports.py coreweave
  python3 consolidate_reports.py nebius --profiles company_profile,marketing_analysis
  python3 consolidate_reports.py coreweave --ai-profile agent
  python3 consolidate_reports.py coreweave --out my_report.md

Requires: registry and aiserver running.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from markdown_pdf import MarkdownPdf, Section

APP_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = APP_ROOT / "data" / "reports"
CONSOLIDATED_DIR = REPORTS_DIR / "consolidated"

DEFAULT_PROFILES = ["company_profile", "marketing_analysis", "operational_metrics"]
DEFAULT_REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")

_SUMMARY_PROMPT = """\
You are a senior analyst writing an executive summary.

Below are {n_sections} separate analysis reports for **{site_name}**, each
covering a different dimension of the company.  Write a concise executive
summary (roughly 400–600 words) that:

1. Opens with a one-paragraph company overview.
2. Highlights the most important findings from each report section.
3. Identifies cross-cutting themes, strengths, and risks.
4. Closes with a forward-looking paragraph on strategic outlook.

Use clear, professional language.  Do NOT reproduce the full reports — distil
only the key insights.

{sections}
"""


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s" if m else f"{s}s"


def _get_aiserver_url(registry_url: str) -> str:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{registry_url}/servers/aiserver")
        r.raise_for_status()
        url = (r.json() or {}).get("url")
        if not url:
            raise ValueError("Registry missing url for aiserver")
        return str(url).rstrip("/")


def _ai_generate(aiserver_url: str, prompt: str, ai_profile: str, timeout: float) -> str:
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            f"{aiserver_url}/generate",
            json={"prompt": prompt, "profile": ai_profile},
        )
        r.raise_for_status()
    out = r.json()
    output = out.get("output") if isinstance(out.get("output"), dict) else out
    if isinstance(output, dict) and "text" in output:
        return str(output["text"]).strip()
    return str(output).strip() if output else ""


def _find_latest_report(profile_dir: Path, site_name: str) -> Path | None:
    """Find the most recently modified .md file matching the site name."""
    if not profile_dir.is_dir():
        return None
    candidates = sorted(
        (f for f in profile_dir.glob("*.md") if site_name in f.stem),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


_PDF_TABLE_CSS = "table, th, td {border: 1px solid #ccc; padding: 6px 10px;} th {background: #f0f0f0;}"


def _markdown_to_pdf(md_text: str, pdf_path: Path, title: str = "") -> None:
    pdf = MarkdownPdf(toc_level=2)
    pdf.add_section(Section(md_text), user_css=_PDF_TABLE_CSS)
    pdf.meta["title"] = title
    pdf.save(str(pdf_path))


def main() -> int:
    parser = argparse.ArgumentParser(description="Consolidate analysis reports for a site")
    parser.add_argument("site", help="Site name to match in report filenames (e.g. coreweave, nebius)")
    parser.add_argument("--profiles", default=",".join(DEFAULT_PROFILES),
                        help=f"Comma-separated profile directory names (default: {','.join(DEFAULT_PROFILES)})")
    parser.add_argument("--ai-profile", default="agent", help="AI profile for summary generation (default: agent)")
    parser.add_argument("--ai-timeout", type=float, default=660.0, help="AI call timeout in seconds (default: 660)")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_URL, help="Registry server URL")
    parser.add_argument("--out", default=None, help="Output file path (default: reports/consolidated/<site>_<ts>.md)")
    args = parser.parse_args()

    site_name = args.site.strip().lower()
    profile_names = [p.strip() for p in args.profiles.split(",") if p.strip()]
    if not profile_names:
        print("Error: no profiles specified", file=sys.stderr)
        return 1

    print(f"Consolidating reports for: {site_name}")
    print(f"Profiles: {', '.join(profile_names)}")
    print(f"Reports dir: {REPORTS_DIR}")
    print()

    sections: list[tuple[str, str, Path]] = []
    for profile in profile_names:
        profile_dir = REPORTS_DIR / profile
        report = _find_latest_report(profile_dir, site_name)
        if report is None:
            print(f"  [{profile}] no report found — skipping")
            continue
        content = report.read_text(encoding="utf-8").strip()
        if not content:
            print(f"  [{profile}] empty report — skipping")
            continue
        sections.append((profile, content, report))
        print(f"  [{profile}] {report.name} ({len(content):,} chars)")

    if not sections:
        print("\nError: no reports found for any profile", file=sys.stderr)
        return 1

    print(f"\nFound {len(sections)} report(s). Generating executive summary ...")
    t0 = time.monotonic()

    section_text = "\n\n".join(
        f"--- Report: {name} ---\n{content}" for name, content, _ in sections
    )
    prompt = _SUMMARY_PROMPT.format(
        n_sections=len(sections),
        site_name=site_name,
        sections=section_text,
    )

    aiserver_url = _get_aiserver_url(args.registry)
    summary = _ai_generate(aiserver_url, prompt, args.ai_profile, args.ai_timeout)
    ai_elapsed = time.monotonic() - t0
    print(f"  Summary generated in {_fmt_elapsed(ai_elapsed)}")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Consolidated Report: {site_name}",
        "",
        f"*Generated {ts} from {len(sections)} analysis profile(s): "
        f"{', '.join(name for name, _, _ in sections)}.*",
        "",
        "> **Note:** The primary source of information in this report is the company's own"
        " website. The content reflects the company's self-reported claims and marketing"
        " materials and should not be treated as independently verified fact.",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        summary,
        "",
        "---",
        "",
    ]
    for profile_name, content, source_file in sections:
        heading = profile_name.replace("_", " ").title()
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(f"*Source: {source_file.name}*")
        lines.append("")
        lines.append(content)
        lines.append("")
        lines.append("---")
        lines.append("")

    report_text = "\n".join(lines)

    if args.out:
        md_path = Path(args.out)
    else:
        CONSOLIDATED_DIR.mkdir(parents=True, exist_ok=True)
        md_path = CONSOLIDATED_DIR / f"{site_name}.md"

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report_text, encoding="utf-8")

    pdf_path = md_path.with_suffix(".pdf")
    _markdown_to_pdf(report_text, pdf_path, title=f"Consolidated Report: {site_name}")

    total_chars = sum(len(c) for _, c, _ in sections)
    print(f"\nMarkdown: {md_path}")
    print(f"PDF:      {pdf_path}")
    print(f"  {len(sections)} sections, {total_chars:,} source chars, "
          f"{len(report_text):,} output chars")
    print(f"  Total: {_fmt_elapsed(time.monotonic() - t0)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
