"""
License: MIT
Description: Loads AI server configuration from environment (.env) and
config/aiserver.yaml.

Defines defaults for provider selection, model mapping per profile,
and context window resolution (env override → live probe → YAML fallback).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

Provider = Literal["ollama", "openai", "xai", "google", "perplexity", "wandb", "anthropic", "mlx"]
Profile = Literal["fast", "chat", "reason", "agent", "code", "image", "video", "search", "batch"]

SUPPORTED_PROVIDERS: set[str] = {"ollama", "openai", "xai", "google", "perplexity", "wandb", "anthropic", "mlx"}
SUPPORTED_PROFILES: set[str] = {"fast", "chat", "reason", "agent", "code", "image", "video", "search", "batch"}


from common.simple.user_dir import resolve_env_file

_REPO_ROOT = Path(__file__).resolve().parents[2]
_env_path = resolve_env_file() or _REPO_ROOT / ".env"
load_dotenv(_env_path)

_AISERVER_YAML = _REPO_ROOT / "config" / "aiserver.yaml"
_yaml_config: dict[str, Any] | None = None


def _load_yaml_config() -> dict[str, Any]:
    global _yaml_config
    if _yaml_config is not None:
        return _yaml_config
    if _AISERVER_YAML.is_file():
        with open(_AISERVER_YAML, encoding="utf-8") as f:
            _yaml_config = yaml.safe_load(f) or {}
    else:
        logger.warning("aiserver config not found at %s, using empty config", _AISERVER_YAML)
        _yaml_config = {}
    return _yaml_config


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


def get_provider_timeout(provider: Provider) -> float:
    """HTTP timeout (seconds) for a provider's generate call.

    Resolution: env AISERVER_TIMEOUT_<PROVIDER> → config/aiserver.yaml
    timeouts.<provider> → timeouts.default → 600.
    """
    env_val = _get_env(f"AISERVER_TIMEOUT_{provider.upper()}")
    if env_val:
        try:
            return max(30.0, float(env_val))
        except ValueError:
            pass
    # Legacy env var for wandb
    if provider == "wandb":
        legacy = _get_env("AISERVER_WANDB_TIMEOUT_SECONDS")
        if legacy:
            try:
                return max(30.0, float(legacy))
            except ValueError:
                pass
    cfg = _load_yaml_config()
    timeouts = cfg.get("timeouts") or {}
    val = timeouts.get(provider)
    if isinstance(val, (int, float)) and val > 0:
        return float(val)
    default_val = timeouts.get("default")
    if isinstance(default_val, (int, float)) and default_val > 0:
        return float(default_val)
    return 600.0


def get_wandb_http_timeout_seconds() -> float:
    """Backward-compatible alias."""
    return get_provider_timeout("wandb")


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


# ── Context window resolution ────────────────────────────────────────────
#
# Resolution order (first match wins):
#   1. Env var:   AISERVER_CONTEXT_WINDOW_<MODEL_UPPER>
#   2. Probe cache: live value fetched from the provider API at startup
#   3. YAML table:  config/aiserver.yaml → context_windows.<model>
#   4. YAML default: config/aiserver.yaml → default_context_window

def _yaml_context_windows() -> dict[str, int]:
    cfg = _load_yaml_config()
    raw = cfg.get("context_windows") or {}
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float)) and v > 0}


def _yaml_default_context_window() -> int:
    cfg = _load_yaml_config()
    val = cfg.get("default_context_window")
    if isinstance(val, (int, float)) and val > 0:
        return int(val)
    return 128_000


_probe_cache: dict[str, int] = {}


def prime_context_cache(provider: Provider, model: str) -> None:
    """Probe a provider API for the model's context window and cache the result.

    Called at startup for each active (provider, profile) combination.
    Safe to call multiple times — skips models already cached.
    """
    if model in _probe_cache:
        return
    from .context_probe import probe_context_window
    result = probe_context_window(provider, model)
    if result is not None:
        _probe_cache[model] = result
        logger.info("Probed context window: %s (%s) = %d tokens", model, provider, result)
    else:
        logger.debug("No probe result for %s (%s), will use YAML fallback", model, provider)


def _model_env_key(model: str) -> str:
    return "AISERVER_CONTEXT_WINDOW_" + (
        model.upper()
        .replace("-", "_")
        .replace("/", "_")
        .replace(".", "_")
        .replace(":", "_")
    )


def get_context_window(model: str) -> int:
    """Return the context window (in tokens) for *model*.

    Resolution: env override → probe cache → YAML table → YAML default.
    """
    env_val = _get_env(_model_env_key(model))
    if env_val:
        try:
            return max(1, int(env_val))
        except ValueError:
            pass

    if model in _probe_cache:
        return _probe_cache[model]

    yaml_table = _yaml_context_windows()
    if model in yaml_table:
        return yaml_table[model]

    return _yaml_default_context_window()


def get_context_window_source(model: str) -> str:
    """Return which layer provided the context window value (for diagnostics)."""
    env_val = _get_env(_model_env_key(model))
    if env_val:
        try:
            int(env_val)
            return "env"
        except ValueError:
            pass
    if model in _probe_cache:
        return "probe"
    if model in _yaml_context_windows():
        return "yaml"
    return "default"


def get_model_info(provider: Provider, profile: Profile) -> dict[str, Any]:
    """Return model metadata for a (provider, profile) pair."""
    model = get_model(provider, profile)
    context_window = get_context_window(model)
    source = get_context_window_source(model)
    return {
        "provider": provider,
        "profile": profile,
        "model": model,
        "context_window": context_window,
        "context_window_source": source,
    }

