"""
License: MIT
Description: Configuration API routes.

Configuration records are stored via the storage server in the `system` namespace.
Callers address config by (resource_type, resource_name). Record keys are derived
from those parts; the underlying storage mechanism is not exposed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from common.transport_encryption import get_transport_encryption
from .storage_client import StorageClient


router = APIRouter(prefix="/configs", tags=["configs"])

_ENC_HEADER = "x-transport-encrypted"
_ENC_FIELD = "_enc"


def _wants_encrypted_response(request: Request) -> bool:
    return request.headers.get(_ENC_HEADER, "").strip() in {"1", "true", "True", "yes", "YES"}


def _maybe_decrypt_body(body: Any) -> Any:
    if isinstance(body, dict) and isinstance(body.get(_ENC_FIELD), str) and body[_ENC_FIELD]:
        return get_transport_encryption().decrypt_json(body[_ENC_FIELD])
    return body


def _maybe_encrypt_response(request: Request, payload: Any) -> Any:
    if _wants_encrypted_response(request):
        return {_ENC_FIELD: get_transport_encryption().encrypt_json(payload)}
    return payload


def _client(request: Request) -> StorageClient:
    return request.app.state.storage_client


def _key(resource_type: str, resource_name: str) -> str:
    return f"{resource_type}:{resource_name}"


@router.get("")
def list_configs(request: Request) -> Any:
    keys = _client(request).list_keys()
    return _maybe_encrypt_response(request, {"keys": keys})


@router.get("/{resource_type}")
def list_configs_by_type(request: Request, resource_type: str) -> Any:
    """Return all config records for a resource type in a single call (e.g. GET /configs/skill)."""
    prefix = f"{resource_type}:"
    raw_records = _client(request).list_records(prefix=prefix)
    records: dict[str, Any] = {}
    for k, val in raw_records.items():
        name = k.split(":", 1)[1] if ":" in k else k
        records[name] = val
    return _maybe_encrypt_response(request, {"records": records})


@router.get("/{resource_type}/{resource_name}")
def get_config(request: Request, resource_type: str, resource_name: str) -> Any:
    k = _key(resource_type, resource_name)
    rec = _client(request).get_record(k)
    if rec is None:
        raise HTTPException(status_code=404, detail="Config not found")
    return _maybe_encrypt_response(request, rec)


@router.put("/{resource_type}/{resource_name}")
def put_config(request: Request, resource_type: str, resource_name: str, body: dict[str, Any]) -> Any:
    body_any = _maybe_decrypt_body(body)
    if not isinstance(body_any, dict):
        raise HTTPException(status_code=400, detail="Config value must be a JSON object")

    # Ensure the value includes at least the requested identifying fields.
    value = dict(body_any)
    value.setdefault("resource_type", resource_type)
    value.setdefault("resource_name", resource_name)

    # Optional convenience timestamp (storage server will also add createdAt/updatedAt at record level).
    value.setdefault("configUpdatedAt", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    k = _key(resource_type, resource_name)
    rec = _client(request).put_record(k, value)
    return _maybe_encrypt_response(request, rec)


@router.delete("/{resource_type}/{resource_name}")
def delete_config(request: Request, resource_type: str, resource_name: str) -> Any:
    k = _key(resource_type, resource_name)
    if not _client(request).delete_record(k):
        raise HTTPException(status_code=404, detail="Config not found")
    return _maybe_encrypt_response(request, {"key": k, "status": "deleted"})

