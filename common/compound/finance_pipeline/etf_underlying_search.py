"""Use aiserver ``search`` profile to infer a primary underlying for a ticker when needed."""

from __future__ import annotations

import json
import re
import sys
from typing import TextIO

from common.compound.aiserver_generate_client import AiserverGenerateClient

# Aiserver profile for POST /generate when SecuritiesDB has no holdings (see servers/aiserver/config.py).
UNDERLYING_ETF_AISERVER_PROFILE = "search"


def underlying_etf_search_prompt(symbol: str) -> str:
    sym = str(symbol or "").strip().upper()
    return (
        f"What is the following ETF, {sym}, tracking? "
        "Reply with a simple response that states the underlying only; do not include citations, links, or source lists."
    )


def _parse_one_word_underlying(text: str) -> tuple[bool, str | None]:
    """
    Expect a one-word answer (or a line whose last word is the answer):
    ``none`` (any case) or a US-listed symbol. Uses the last alphanumeric run on
    the first line so a brief phrase ending in the ticker still works.
    """
    raw = (text or "").strip()
    if not raw:
        return (False, None)
    first_line = raw.splitlines()[0]
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.]*", first_line)
    if not tokens:
        return (False, None)
    word = tokens[-1].strip(".,;:!?")
    if not word or word.lower() == "none":
        return (False, None)
    return (True, word.upper())


def resolve_underlying_etf_via_search(
    *,
    symbol: str,
    aiserver_base_url: str,
    timeout_sec: float = 120.0,
    provider: str | None = None,
    log_io: bool = False,
    log_file: TextIO = sys.stderr,
) -> tuple[bool, str | None]:
    """
    Call aiserver ``/generate`` with profile ``search`` (``UNDERLYING_ETF_AISERVER_PROFILE``).

    When ``log_io`` is True, print the full prompt, then the full JSON body returned by
    ``/generate`` (and flush) before any parsing. Same client pattern as
    ``portfolio_intent_command._call_generate`` / ``upcoming_distributions_aiserver``.

    Returns (True, underlying_ticker) when the model returns a symbol; else (False, None).
    """
    pfx = f"[{str(symbol).strip().upper()}]"
    client = AiserverGenerateClient(base_url=aiserver_base_url, timeout_sec=timeout_sec)
    prompt = underlying_etf_search_prompt(symbol)
    if log_io:
        print(
            f"{pfx} Prompt sent to aiserver (POST /generate, profile={UNDERLYING_ETF_AISERVER_PROFILE}):\n{prompt}\n",
            file=log_file,
            flush=True,
        )
    payload = client.generate(
        prompt=prompt, profile=UNDERLYING_ETF_AISERVER_PROFILE, provider=provider
    )
    if log_io:
        body = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
        print(f"{pfx} Response from aiserver (JSON from /generate):\n{body}\n", file=log_file, flush=True)
    text = AiserverGenerateClient.output_text(payload)
    return _parse_one_word_underlying(text)
