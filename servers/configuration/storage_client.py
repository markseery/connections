"""
License: MIT
Description: Client for the storage server used by the configuration server.

All configuration records are stored in the `system` namespace in the storage server.
Transport encryption is used for requests/responses to/from the storage server.
"""

from __future__ import annotations

from typing import Any

import httpx

from common.transport_encryption import get_transport_encryption
from .config import get_storage_server_url


SYSTEM_NAMESPACE = "system"


class StorageClient:
    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or get_storage_server_url()
        self._enc = get_transport_encryption()

    def _headers(self) -> dict[str, str]:
        # Request encrypted responses from storage server.
        return {"X-Transport-Encrypted": "1"}

    def _encrypt_body(self, obj: Any) -> dict[str, str]:
        return {"_enc": self._enc.encrypt_json(obj)}

    def _decrypt_payload(self, payload: Any) -> Any:
        if isinstance(payload, dict) and isinstance(payload.get("_enc"), str) and payload["_enc"]:
            return self._enc.decrypt_json(payload["_enc"])
        return payload

    def list_keys(self) -> list[str]:
        url = f"{self._base_url}/namespaces/{SYSTEM_NAMESPACE}/records"
        with httpx.Client(timeout=10.0) as client:
            r = client.get(url, headers=self._headers())
            r.raise_for_status()
            data = self._decrypt_payload(r.json())
            return list(data.get("keys", []))

    def get_record(self, key: str) -> dict[str, Any] | None:
        url = f"{self._base_url}/namespaces/{SYSTEM_NAMESPACE}/records/{key}"
        with httpx.Client(timeout=10.0) as client:
            r = client.get(url, headers=self._headers())
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return self._decrypt_payload(r.json())

    def put_record(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/namespaces/{SYSTEM_NAMESPACE}/records/{key}"
        with httpx.Client(timeout=10.0) as client:
            r = client.put(url, json=self._encrypt_body(value), headers=self._headers())
            r.raise_for_status()
            return self._decrypt_payload(r.json())

    def delete_record(self, key: str) -> bool:
        url = f"{self._base_url}/namespaces/{SYSTEM_NAMESPACE}/records/{key}"
        with httpx.Client(timeout=10.0) as client:
            r = client.delete(url, headers=self._headers())
            if r.status_code == 404:
                return False
            r.raise_for_status()
            return True

