"""
License: MIT
Description: CLI helper that sends a prompt to the local AI server (/generate).

Resolves aiserver via ``common.compound.aiserver_discovery.get_aiserver_base_url``
unless ``--url`` or ``--inprocess`` is used (see ``docs/SCRIPTS_AND_CLIENT_PATTERNS.md``).

Usage:
  python ask_ai.py "hello"
  python ask_ai.py "hello" --profile fast --provider ollama
  python ask_ai.py "Summarize the key points." --file report.md
  python ask_ai.py "hello" --url http://127.0.0.1:7012
  python ask_ai.py "hello" --inprocess
"""

from __future__ import annotations

from common.compound.ask_ai_command import AskAiCommand


def main() -> int:
    return AskAiCommand.execute()


if __name__ == "__main__":
    raise SystemExit(main())

