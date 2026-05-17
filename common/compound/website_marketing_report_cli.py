#!/usr/bin/env python3
"""
Ask the aiserver a question using an existing ``website_marketing_analysis.py`` report as context.

Resolves the default report path the same way as ``website_marketing_analysis`` (``marketing_<host>_<path>.md``
under ``application_files/data/website_marketing`` unless you pass ``--out-dir`` / ``--report``).

Example:
  python scripts/website_marketing_report_ask_ai.py https://www.example.com \\
    --prompt "What are the main risks mentioned for investors?"

Verbose trace (progress, file read from disk, how aiserver is called, full prompt, full JSON response) on stderr:
  python scripts/website_marketing_report_ask_ai.py https://www.example.com \\
    --prompt "..." --diagnostics
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from common.simple import script_env

from common.compound.aiserver_discovery import get_aiserver_base_url
from common.compound.aiserver_generate_client import AiserverGenerateClient
from common.simple.user_dir import load_connections_dotenv
from common.compound.website_marketing_analysis_cli import (
    ANALYSIS_TOPICS,
    default_marketing_report_path_for_url,
    marketing_aiserver_401_diagnosis,
)

DEFAULT_REGISTRY_URL = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")
AI_PROFILE_CHOICES = ("fast", "chat", "reason", "agent")


def _resolve_report_path(cli_path: Path) -> Path:
    p = Path(cli_path).expanduser()
    return p if p.is_absolute() else (script_env.repo_root() / p).resolve()


def _report_looks_like_failed_marketing_run(text: str) -> tuple[bool, str]:
    """
    ``website_marketing_analysis`` stores ``[Error: ...]`` per topic when /generate fails
    (e.g. 401). A report that is mostly those lines is not useful context for follow-up prompts.
    """
    t = text or ""
    n_topics = len(ANALYSIS_TOPICS)
    err_blocks = t.count("[Error:")
    if err_blocks >= max(5, (n_topics + 1) // 2):
        return True, f"{err_blocks} aiserver error sections in report"
    if t.count("401 Unauthorized") >= 5:
        return True, "repeated HTTP 401 markers in report"
    return False, ""


def _diag_block(diag: bool, title: str, body: str) -> None:
    if not diag:
        return
    bar = "=" * 72
    print(f"\n{bar}\n{title}\n{bar}\n{body.rstrip()}\n", file=sys.stderr, flush=True)


def _diag_line(diag: bool, msg: str) -> None:
    if not diag:
        return
    print(msg, file=sys.stderr, flush=True)


def main() -> int:
    load_connections_dotenv()
    ap = argparse.ArgumentParser(
        description=(
            "Load the marketing-analysis markdown for a URL (from website_marketing_analysis.py output) "
            "and send your prompt plus that file to the aiserver /generate endpoint."
        ),
    )
    ap.add_argument("url", help="Website URL (same as for website_marketing_analysis.py)")
    ap.add_argument(
        "--prompt",
        required=True,
        help="Question or instructions for the model (report markdown is appended as context).",
    )
    ap.add_argument(
        "--report",
        type=Path,
        default=None,
        metavar="FILE.md",
        help="Explicit report path (default: same default file website_marketing_analysis writes for this URL).",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory where the default report lives (default: application_files/data/website_marketing). "
            "Must match website_marketing_analysis --out-dir if you overrode it."
        ),
    )
    ap.add_argument(
        "--profile",
        choices=AI_PROFILE_CHOICES,
        default="reason",
        metavar="STRENGTH",
        help=f"Aiserver profile (default: reason). Choices: {', '.join(AI_PROFILE_CHOICES)}.",
    )
    ap.add_argument(
        "--provider",
        default=None,
        help="Optional aiserver provider override (same as other CLIs, e.g. run_semiconductor_cycle_signals).",
    )
    ap.add_argument(
        "--registry-url",
        default=DEFAULT_REGISTRY_URL,
        help="Registry URL for aiserver discovery (default: REGISTRY_SERVER_URL or 127.0.0.1:7002).",
    )
    ap.add_argument(
        "--aiserver-url",
        default=None,
        help="Override aiserver base URL (default: registry via get_aiserver_base_url).",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="HTTP timeout seconds for POST /generate (default: 300).",
    )
    ap.add_argument(
        "--max-report-chars",
        type=int,
        default=240_000,
        metavar="N",
        help="Truncate the report to this many characters if longer (default: 240000).",
    )
    ap.add_argument(
        "--allow-error-report",
        action="store_true",
        help="Send the report to the model even if it looks like a failed marketing run (mostly [Error: ...]).",
    )
    ap.add_argument(
        "--diagnostics",
        action="store_true",
        help=(
            "Print progress and full trace to stderr: resolved paths, entire report file from disk, "
            "how the aiserver is invoked (same as other scripts), full prompt body, full JSON response."
        ),
    )
    args = ap.parse_args()
    diag = bool(args.diagnostics)

    _diag_line(diag, "[diagnostics] Starting: resolve marketing report path for URL.")

    if args.report is not None:
        report_path = _resolve_report_path(args.report)
    else:
        report_path = default_marketing_report_path_for_url(args.url, out_dir=args.out_dir)

    _diag_line(diag, f"[diagnostics] Report path: {report_path}")

    if not report_path.is_file():
        print(
            f"Report not found: {report_path}\n"
            "Run website_marketing_analysis.py for this URL first (matching --out-dir if used), "
            "or pass --report explicitly.",
            file=sys.stderr,
        )
        return 1

    _diag_line(diag, "[diagnostics] Reading report file from disk (full contents in section below).")
    report_full = report_path.read_text(encoding="utf-8")
    _diag_block(diag, "FILE CONTENTS RETRIEVED (exact bytes from disk, UTF-8)", report_full)

    bad_report, why = _report_looks_like_failed_marketing_run(report_full)
    if bad_report and not args.allow_error_report:
        aiserver_early = (
            args.aiserver_url or get_aiserver_base_url(registry_override=args.registry_url)
        ).rstrip("/")
        print(
            f"This markdown looks like a failed website_marketing_analysis run ({why}).\n"
            f"File: {report_path}\n\n"
            "Fix aiserver credentials / provider (often HTTP 401 on /generate), then regenerate the report:\n"
            "  python scripts/website_marketing_analysis.py <url> --skip-scrape\n\n"
            "Or pass --allow-error-report to send this file as context anyway.",
            file=sys.stderr,
        )
        if "401" in report_full:
            m = re.search(r"Aiserver profile:\s*([A-Za-z0-9_-]+)", report_full, flags=re.IGNORECASE)
            report_profile = m.group(1).lower() if m else None
            print(
                marketing_aiserver_401_diagnosis(aiserver_url=aiserver_early, profile=report_profile),
                file=sys.stderr,
            )
        return 4

    limit = max(1_000, int(args.max_report_chars))
    if len(report_full) > limit:
        report_for_prompt = report_full[:limit] + "\n\n[... truncated for context length ...]\n"
        _diag_line(
            diag,
            f"[diagnostics] Applied --max-report-chars={limit} (original file was {len(report_full)} chars).",
        )
    else:
        report_for_prompt = report_full

    _diag_line(diag, "[diagnostics] Resolving aiserver base URL (registry or --aiserver-url override).")
    aiserver = (args.aiserver_url or get_aiserver_base_url(registry_override=args.registry_url)).rstrip("/")
    _diag_line(diag, f"[diagnostics] Aiserver base URL: {aiserver}")

    full_prompt = (
        f"{args.prompt.strip()}\n\n"
        "---\n\n"
        "Context (markdown report from website_marketing_analysis):\n\n"
        f"{report_for_prompt}"
    )

    how_called = (
        "HOW THE AI SERVER IS CALLED (same stack as scripts/run_semiconductor_cycle_signals.py)\n"
        "- Module: common.compound.aiserver_discovery.get_aiserver_base_url\n"
        "  (registry_override uses --registry-url / REGISTRY_SERVER_URL unless --aiserver-url is set).\n"
        "- Module: common.compound.aiserver_generate_client.AiserverGenerateClient\n"
        f"- HTTP: POST {aiserver}/generate\n"
        "- JSON body keys: \"prompt\" (string), \"profile\" (string), optional \"provider\" (string).\n"
        f"- This run: profile={args.profile!r}, provider={args.provider!r}, "
        f"timeout_sec={max(30.0, float(args.timeout))!r}.\n"
        "- Response: JSON object; model text is read from output.text via AiserverGenerateClient.output_text()."
    )
    _diag_block(diag, "AISERVER CALL (mechanism)", how_called)

    request_payload_preview = {
        "prompt": f"<string, length {len(full_prompt)}>",
        "profile": args.profile,
        "provider": args.provider,
    }
    _diag_line(
        diag,
        "[diagnostics] Request JSON shape (prompt omitted here; full string in next section):\n"
        + json.dumps(request_payload_preview, indent=2),
    )
    _diag_block(diag, "FULL PROMPT SENT TO AISERVER (exact string in POST body field \"prompt\")", full_prompt)

    _diag_line(diag, "[diagnostics] Calling AiserverGenerateClient.generate(...) now.")

    client = AiserverGenerateClient(aiserver, timeout_sec=max(30.0, float(args.timeout)))
    try:
        raw = client.generate(prompt=full_prompt, profile=args.profile, provider=args.provider)
    except Exception as exc:
        print(f"Error: aiserver /generate failed: {exc}", file=sys.stderr)
        _diag_line(diag, f"[diagnostics] Exception after POST /generate: {exc!r}")
        return 1

    try:
        raw_json = json.dumps(raw, indent=2, ensure_ascii=False, default=str)
    except TypeError:
        raw_json = repr(raw)
    _diag_block(diag, "FULL RAW JSON RESPONSE FROM AISERVER", raw_json)

    text = AiserverGenerateClient.output_text(raw).strip()
    _diag_block(diag, "MODEL OUTPUT TEXT (AiserverGenerateClient.output_text)", text or "(empty string)")

    if not text:
        print("(empty model output)", file=sys.stderr)
        return 1

    _diag_line(diag, "[diagnostics] Writing model output text to stdout.")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
