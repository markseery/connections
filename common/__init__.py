"""
License: MIT
Description: Shared utilities used across servers and clients.

Modules are organized into three subdirectories:

  simple/    — single-purpose or narrow-function utilities
  compound/  — modules built by combining multiple simple modules
  complex/   — extensive code, orchestration, or multi-step logic

All public symbols are re-exported here so existing ``from common.<module>``
imports continue to work.
"""

# ── simple ────────────────────────────────────────────────────────────
from common.simple import json_repair as json_repair  # noqa: F401
from common.simple import models as models  # noqa: F401
from common.simple import skill_response as skill_response  # noqa: F401
from common.simple import user_dir as user_dir  # noqa: F401
from common.simple import config as config  # noqa: F401
from common.simple import timeouts as timeouts  # noqa: F401
from common.simple import adaptive_poll as adaptive_poll  # noqa: F401

# ── compound ──────────────────────────────────────────────────────────
from common.compound import agent_config as agent_config  # noqa: F401
from common.compound import agent_logger as agent_logger  # noqa: F401
from common.compound import context_compactor as context_compactor  # noqa: F401
from common.compound import http_client as http_client  # noqa: F401
from common.compound import registry_client as registry_client  # noqa: F401
from common.compound import skill_config as skill_config  # noqa: F401
from common.compound import stored_site_content as stored_site_content  # noqa: F401
from common.compound import transport_encryption as transport_encryption  # noqa: F401

# ── complex (lazy to avoid circular imports with servers.agent) ──────
def __getattr__(name: str):
    _complex_modules = {
        "agent_memory", "approval_gate", "context_curator",
        "google_news_decoder", "skill_lifecycle", "skill_catalog",
    }
    if name in _complex_modules:
        import importlib
        return importlib.import_module(f"common.complex.{name}")

    _lifecycle_names = {
        "SkillLifecycle", "find_live_worker", "load_skill",
        "register_skill", "route_exists",
    }
    if name in _lifecycle_names:
        from common.complex import skill_lifecycle as _sl
        return getattr(_sl, name)

    _decoder_names = {"GoogleNewsDecoder"}
    if name in _decoder_names:
        from common.complex import google_news_decoder as _gnd
        return getattr(_gnd, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
