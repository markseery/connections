from __future__ import annotations

from typing import Any

from common.compound.http_client import http_client
from common.compound.registry_client import get_server_url


class StateStorageClient:
    def __init__(self, *, storage_url: str | None = None) -> None:
        self._storage_url = (storage_url or get_server_url("storage")).rstrip("/")

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        url = f"{self._storage_url}/namespaces/{namespace}/records/{key}"
        try:
            with http_client("storage") as client:
                r = client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    val = data.get("value") if isinstance(data, dict) else data
                    return val if isinstance(val, dict) else None
        except Exception:
            pass
        return None

    def put(self, namespace: str, key: str, value: dict[str, Any]) -> bool:
        url = f"{self._storage_url}/namespaces/{namespace}/records/{key}"
        try:
            with http_client("storage") as client:
                r = client.put(url, json={"value": value})
                return r.status_code in {200, 201}
        except Exception:
            return False

    def list_keys(self, namespace: str) -> list[str]:
        url = f"{self._storage_url}/namespaces/{namespace}/records"
        try:
            with http_client("storage") as client:
                r = client.get(url)
                if r.status_code == 200:
                    keys = r.json().get("keys")
                    return list(keys) if isinstance(keys, list) else []
        except Exception:
            pass
        return []

    def append_event(
        self,
        namespace: str,
        event: dict[str, Any],
        *,
        max_records: int,
    ) -> None:
        key = "events"
        existing = self.get(namespace, key) or {}
        items = existing.get("items") if isinstance(existing.get("items"), list) else []
        items = list(items)
        items.append(event)
        if max_records > 0 and len(items) > max_records:
            items = items[-max_records:]
        self.put(namespace, key, {"items": items})
