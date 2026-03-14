"""
Test script for wandb_ai_provider (W&B Inference only). Run with:
  python test_wandb_provider.py
Requires .env with WANDB_API_KEY. Optional: WANDB_PROJECT (e.g. team/project).
"""

from __future__ import annotations

import sys

from wandb_ai_provider import create_completion, init_weave


def main() -> int:
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
            kwargs = {"message": message}
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
