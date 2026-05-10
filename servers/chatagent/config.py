"""
License: MIT
Description: ChatAgent server config. Delegates to common registry client.
"""

from common.compound.aiserver_discovery import get_aiserver_base_url
from common.compound.registry_client import get_registry_url, get_server_url  # noqa: F401


def get_aiserver_url() -> str:
    return get_aiserver_base_url()


def get_config_url() -> str:
    return get_server_url("configuration")
