"""
License: MIT
Description: W&B Inference AI provider — OpenAI-compatible client pointed at
W&B Inference (https://api.inference.wandb.ai/v1). Weave autopatches OpenAI
to log LLM calls to W&B. Only WANDB_API_KEY is required (from .env).

  pip install wandb weave openai python-dotenv

Usage:
  from wandb_ai_provider import create_completion
  create_completion("Tell me a joke.")

  python wandb_ai_provider.py
"""

from __future__ import annotations

import os
from pathlib import Path

# Load .env so WANDB_API_KEY and optional WANDB_PROJECT are set
_dotenv_path = Path(__file__).resolve().parent / ".env"
if _dotenv_path.is_file():
    from dotenv import load_dotenv
    load_dotenv(_dotenv_path)

import weave
from openai import OpenAI

WANDB_INFERENCE_BASE = "https://api.inference.wandb.ai/v1"


def _wandb_api_key() -> str:
    key = os.environ.get("WANDB_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "WANDB_API_KEY not set. Add it to .env or export it. Get it from https://wandb.ai/authorize"
        )
    return key


def _wandb_project() -> str:
    return os.environ.get("WANDB_PROJECT", "markseery-bohcay-llc/inference-testing").strip() or "markseery-bohcay-llc/inference-testing"


_weave_initialized = False


def init_weave(project: str | None = None) -> None:
    """Initialize Weave once (autopatches OpenAI to log to W&B). Uses WANDB_API_KEY."""
    global _weave_initialized
    if _weave_initialized:
        return
    _wandb_api_key()
    weave.init('markseery-bohcay-llc/inference-testing')
    _weave_initialized = True


def _client() -> OpenAI:
    """OpenAI client pointed at W&B Inference; only WANDB_API_KEY is used."""
    init_weave()
    return OpenAI(
        base_url=WANDB_INFERENCE_BASE,
        api_key=_wandb_api_key(),
        project=_wandb_project(),
    )


@weave.op
def create_completion(
    message: str,
    *,
    system_content: str = "You are a helpful assistant.",
    model: str = "meta-llama/Llama-3.1-8B-Instruct",
) -> str:
    """
    Chat completion via W&B Inference. Only WANDB_API_KEY required (in .env).
    """
    # Find your wandb API key at: https://wandb.ai/authorize
    # weave.init('markseery-bohcay-llc/inference-testing')

    print("System content: ", system_content)
    print("Message: ", message)
    print("Model: ", model)

    client = _client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": message},
        ],
        #response_format={ "type": "json_object" }
    )
    print("Hello Mark: ", response.choices[0].message.content)
    return (response.choices[0].message.content or "").strip()


if __name__ == "__main__":
    init_weave()
    result = create_completion("Tell me a joke.")
    print(result)
