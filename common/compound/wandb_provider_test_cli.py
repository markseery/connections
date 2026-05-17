"""Smoke tests for :mod:`common.compound.wandb_ai_provider`."""

from __future__ import annotations

import sys

from common.compound.wandb_ai_provider import create_completion, init_weave
from common.simple.user_dir import load_connections_dotenv


def main() -> int:
    load_connections_dotenv()
    print("Initializing Weave (WANDB_API_KEY from .env) ...", flush=True)
    init_weave()
    print("Weave initialized.\n", flush=True)

    tests = [
        ("Tell me a one-line joke.", None),
        ("What is 2 + 2? Reply in one word.", None),
        ("Say hello in one sentence.", "You are a concise assistant."),
    ]

    for i, (message, system) in enumerate(tests, 1):
        print(f"Test {i}: {message!r}", flush=True)
        try:
            kwargs: dict = {"message": message}
            if system is not None:
                kwargs["system_content"] = system
            result = create_completion(**kwargs)
            print(f"  -> {result}\n", flush=True)
        except Exception as e:
            print(f"  ERROR: {e}\n", flush=True)
            return 1

    print("All tests completed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
