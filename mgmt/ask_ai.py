"""
License: MIT
Description: CLI helper that sends a prompt to the local AI server (/generate).

Resolves aiserver via ``common.compound.aiserver_discovery.get_aiserver_base_url``
unless ``--url`` or ``--inprocess`` is used (see ``docs/SCRIPTS_AND_CLIENT_PATTERNS.md``).

Usage (from repo root)::

  python mgmt/ask_ai.py "hello"
  python mgmt/ask_ai.py "hello" --profile fast --provider ollama
  python mgmt/ask_ai.py "Summarize the key points." --file report.md
  python mgmt/ask_ai.py "hello" --url http://127.0.0.1:7012
  python mgmt/ask_ai.py "hello" --inprocess
"""

from __future__ import annotations

import script_env

script_env.ensure_repo_cwd()

from common.compound.ask_ai_command import AskAiCommand


def main() -> int:
    return AskAiCommand.execute()


if __name__ == "__main__":
    raise SystemExit(main())
