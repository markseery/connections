"""
License: MIT
Description: CRUD and list HTTP routes for the storage API. Records are addressed
by namespace and record key; keys are unique within a namespace. All payloads are JSON.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from common.transport_encryption import get_transport_encryption
from .backend import StorageBackend, get_json, set_json


def _backend(request: Request) -> StorageBackend:
    if getattr(request.app.state, "storage_backend", None) is not None:
        return request.app.state.storage_backend
    from .main import get_backend
    return get_backend()


router = APIRouter(prefix="/namespaces/{namespace}/records", tags=["records"])
BackendDep = Annotated[StorageBackend, Depends(_backend)]

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


@router.get("")
def list_records(request: Request, namespace: str, backend: BackendDep) -> Any:
    """List all record keys in the namespace."""
    keys = backend.list_keys(namespace)
    return _maybe_encrypt_response(request, {"namespace": namespace, "keys": keys})


@router.get("/{key}")
def get_record(request: Request, namespace: str, key: str, backend: BackendDep) -> Any:
    """Return the JSON record for the given namespace and key. 404 if missing."""
    value = get_json(backend, namespace, key)
    if value is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return _maybe_encrypt_response(request, {"namespace": namespace, "key": key, "value": value})


@router.put("/{key}")
def put_record(request: Request, namespace: str, key: str, body: dict[str, Any], backend: BackendDep) -> Any:
    """Create or replace a JSON record. Body is the stored value."""
    body_any = _maybe_decrypt_body(body)
    if not isinstance(body_any, dict):
        raise HTTPException(status_code=400, detail="Record value must be a JSON object")

    existing = get_json(backend, namespace, key)

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    created_at: str
    if isinstance(existing, dict) and isinstance(existing.get("createdAt"), str) and existing["createdAt"]:
        created_at = existing["createdAt"]
    else:
        created_at = now

    value = dict(body_any)
    value["createdAt"] = created_at
    value["updatedAt"] = now

    set_json(backend, namespace, key, value)
    return _maybe_encrypt_response(request, {"namespace": namespace, "key": key, "value": value})


@router.delete("/{key}")
def delete_record(request: Request, namespace: str, key: str, backend: BackendDep) -> Any:
    """Remove the record. 404 if it did not exist."""
    if not backend.delete(namespace, key):
        raise HTTPException(status_code=404, detail="Record not found")
    return _maybe_encrypt_response(request, {"namespace": namespace, "key": key, "status": "deleted"})
