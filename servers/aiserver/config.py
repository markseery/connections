"""
License: MIT
Description: Loads AI server configuration from environment (.env).

Defines defaults for provider selection and model mapping per profile.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

Provider = Literal["ollama", "openai", "xai", "google", "perplexity", "wandb", "anthropic", "mlx"]
Profile = Literal["fast", "chat", "reason", "agent", "code", "image", "video", "search", "batch"]

SUPPORTED_PROVIDERS: set[str] = {"ollama", "openai", "xai", "google", "perplexity", "wandb", "anthropic", "mlx"}
SUPPORTED_PROFILES: set[str] = {"fast", "chat", "reason", "agent", "code", "image", "video", "search", "batch"}


_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path)


def _get_env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


def get_default_provider() -> Provider:
    v = _get_env("AISERVER_DEFAULT_PROVIDER", "ollama")
    if v not in SUPPORTED_PROVIDERS:
        raise RuntimeError(f"Invalid AISERVER_DEFAULT_PROVIDER: {v}")
    return v  # type: ignore[return-value]


def get_provider_for_profile(profile: Profile) -> Provider:
    """
    Provider to use for this profile when the client does not send one.
    Set AISERVER_PROFILE_{PROFILE}_PROVIDER (e.g. AISERVER_PROFILE_FAST_PROVIDER=google)
    so each profile can use a different provider.
    The "search" profile defaults to Perplexity unless overridden.
    """
    key = f"AISERVER_PROFILE_{profile.upper()}_PROVIDER"
    v = _get_env(key)
    if v and v in SUPPORTED_PROVIDERS:
        return v  # type: ignore[return-value]
    if profile == "search":
        return "perplexity"
    if profile == "fast":
        return "anthropic"
    if profile == "batch":
        return "mlx"
    return get_default_provider()


def get_provider_key(provider: Provider) -> str | None:
    if provider == "openai":
        return _get_env("OPENAI_API_KEY")
    if provider == "xai":
        return _get_env("XAI_API_KEY")
    if provider == "google":
        return _get_env("GOOGLE_API_KEY")
    if provider == "perplexity":
        return _get_env("PERPLEXITY_API_KEY")
    if provider == "wandb":
        return _get_env("WANDB_API_KEY")
    if provider == "anthropic":
        return _get_env("ANTHROPIC_API_KEY")
    return None


def get_provider_base_url(provider: Provider) -> str | None:
    if provider == "ollama":
        return _get_env("OLLAMA_BASE_URL", "http://localhost:11434")
    if provider == "openai":
        return _get_env("OPENAI_BASE_URL", "https://api.openai.com")
    if provider == "xai":
        return _get_env("XAI_BASE_URL", "https://api.x.ai")
    if provider == "google":
        return _get_env("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com")
    if provider == "perplexity":
        return _get_env("PERPLEXITY_BASE_URL", "https://api.perplexity.ai")
    if provider == "wandb":
        return _get_env("WANDB_INFERENCE_BASE_URL", "https://api.inference.wandb.ai/v1")
    if provider == "anthropic":
        return _get_env("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    return None


def get_wandb_http_timeout_seconds() -> float:
    """
    httpx timeout for W&B Inference (chat/completions). Large agent prompts can exceed short limits.
    Set AISERVER_WANDB_TIMEOUT_SECONDS in .env (default 600).
    """
    raw = _get_env("AISERVER_WANDB_TIMEOUT_SECONDS")
    if raw is not None:
        try:
            return max(30.0, float(raw))
        except ValueError:
            pass
    return 600.0


def get_model(provider: Provider, profile: Profile) -> str:
    """
    Model to use for (provider, profile). Per-profile override wins:
    AISERVER_PROFILE_{profile}_MODEL, then AISERVER_MODEL_{provider}_{profile}, then code defaults.
    """
    profile_model_key = f"AISERVER_PROFILE_{profile.upper()}_MODEL"
    v = _get_env(profile_model_key)
    if v:
        return v

    key = f"AISERVER_MODEL_{provider.upper()}_{profile.upper()}"
    v = _get_env(key)
    if v:
        return v

    # Reasonable fallbacks if not provided in env.
    if provider == "ollama":
        defaults = {
            "fast": "gemma3:4b",
            "chat": "gemma3:4b",
            "reason": "gemma3:4b",
            "agent": "gemma3:4b",
            "code": "gemma3:4b",
            "image": "gemma3:4b",
            "video": "gemma3:4b",
            "search": "gemma3:4b",
        }
        return defaults[profile]

    if provider == "openai":
        defaults = {
            "fast": "gpt-4o-mini",
            "chat": "gpt-4o",
            "reason": "o3-mini",
            "agent": "gpt-4o",
            "code": "gpt-4o",
            "image": "gpt-image-1",
            "video": "gpt-4o",
            "search": "gpt-4o-mini",
        }
        return defaults[profile]

    if provider == "xai":
        defaults = {
            "fast": "grok-3-mini",
            "chat": "grok-3",
            "reason": "grok-3",
            "agent": "grok-3",
            "code": "grok-3",
            "image": "grok-3",
            "video": "grok-3",
            "search": "grok-3-mini",
        }
        return defaults[profile]

    if provider == "google":
        defaults = {
            "fast": "gemini-3-flash-preview",
            "chat": "gemini-2.0-flash",
            "reason": "gemini-2.0-pro",
            "agent": "gemini-2.0-flash",
            "code": "gemini-2.0-pro",
            "image": "gemini-2.0-flash",
            "video": "gemini-2.0-flash",
            "search": "gemini-2.0-flash",
        }
        return defaults[profile]

    if provider == "perplexity":
        return "search"

    if provider == "wandb":
        defaults = {
            "fast": "zai-org/GLM-5-FP8",
            "chat": "zai-org/GLM-5-FP8",
            "reason": "zai-org/GLM-5-FP8",
            "agent": "zai-org/GLM-5-FP8",
            "code": "zai-org/GLM-5-FP8",
            "image": "zai-org/GLM-5-FP8",
            "video": "zai-org/GLM-5-FP8",
            "search": "zai-org/GLM-5-FP8",
        }
        return defaults[profile]

    if provider == "anthropic":
        defaults = {
            "fast": "claude-haiku-4-5-20251001",
            "chat": "claude-sonnet-4-20250514",
            "reason": "claude-sonnet-4-20250514",
            "agent": "claude-opus-4-6",
            "code": "claude-sonnet-4-20250514",
            "image": "claude-sonnet-4-20250514",
            "video": "claude-sonnet-4-20250514",
            "search": "claude-haiku-4-5-20251001",
        }
        return defaults[profile]

    if provider == "mlx":
        default_model = "mlx-community/Mistral-7B-Instruct-v0.3-4bit"
        return _get_env(f"AISERVER_MODEL_MLX_{profile.upper()}", default_model)

    raise RuntimeError(f"Unsupported provider: {provider}")

