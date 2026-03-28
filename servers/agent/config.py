"""
License: MIT
Description: Agent server config. Delegates to common registry client.
"""

from common.compound.registry_client import get_registry_url, get_server_url  # noqa: F401


def get_aiserver_url() -> str:
    return get_server_url("aiserver")


def get_config_server_url() -> str:
    return get_server_url("configuration")
