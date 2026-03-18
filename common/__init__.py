"""
License: MIT
Description: Shared utilities used across servers and clients.
"""

from .google_news_decoder import GoogleNewsDecoder
from .transport_encryption import TransportEncryption, get_transport_encryption
from .json_repair import parse_llm_json, parse_llm_json_or_none, extract_brace_block, repair_json
from .stored_site_content import StoredSiteContent
from .models import ServiceResponse, SkillOutput, ErrorDetail, skill_result


def __getattr__(name: str):
    """Lazy imports for skill_lifecycle to avoid circular dependency with servers.agent."""
    _lifecycle_names = {"SkillLifecycle", "find_live_worker", "load_skill", "register_skill", "route_exists"}
    if name in _lifecycle_names:
        from . import skill_lifecycle as _sl
        return getattr(_sl, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "GoogleNewsDecoder",
    "StoredSiteContent",
    "TransportEncryption",
    "get_transport_encryption",
    "SkillLifecycle",
    "find_live_worker",
    "load_skill",
    "register_skill",
    "route_exists",
    "parse_llm_json",
    "parse_llm_json_or_none",
    "extract_brace_block",
    "repair_json",
    "ServiceResponse",
    "SkillOutput",
    "ErrorDetail",
    "skill_result",
]

