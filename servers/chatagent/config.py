"""
License: MIT
Description: ChatAgent server config. Delegates to common registry client.
"""

from common.registry_client import get_registry_url, get_server_url  # noqa: F401


def get_aiserver_url() -> str:
    return get_server_url("aiserver")


def get_config_url() -> str:
    return get_server_url("configuration")
