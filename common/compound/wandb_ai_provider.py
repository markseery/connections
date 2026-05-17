"""
W&B Inference AI provider — OpenAI-compatible client pointed at W&B Inference.

Weave autopatches OpenAI to log LLM calls to W&B. Requires ``WANDB_API_KEY`` in ``.env``.
"""

from __future__ import annotations

import os

import weave
from openai import OpenAI

from common.simple.user_dir import load_connections_dotenv

WANDB_INFERENCE_BASE = "https://api.inference.wandb.ai/v1"

_weave_initialized = False


def _wandb_api_key() -> str:
    key = os.environ.get("WANDB_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "WANDB_API_KEY not set. Add it to .env or export it. Get it from https://wandb.ai/authorize"
        )
    return key


def _wandb_project() -> str:
    return (
        os.environ.get("WANDB_PROJECT", "markseery-bohcay-llc/inference-testing").strip()
        or "markseery-bohcay-llc/inference-testing"
    )


def init_weave(project: str | None = None) -> None:
    """Initialize Weave once (autopatches OpenAI to log to W&B). Uses WANDB_API_KEY."""
    global _weave_initialized
    if _weave_initialized:
        return
    _wandb_api_key()
    weave.init(project or "markseery-bohcay-llc/inference-testing")
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
    """Chat completion via W&B Inference."""
    client = _client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": message},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def run_demo_completion() -> str:
    """Load env and run a single demo completion (CLI entry)."""
    load_connections_dotenv()
    init_weave()
    return create_completion("Tell me a joke.")
