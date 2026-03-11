"""
License: MIT
Description: Registry server routes.

Allows registration and lookup of named servers. Intended for use by the startup
supervisor and any processes that need to discover where to send requests.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from common.transport_encryption import get_transport_encryption
from .state import RegistryState


router = APIRouter(prefix="/servers", tags=["registry"])

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


def _state(request: Request) -> RegistryState:
    return request.app.state.registry_state


@router.get("")
def list_servers(request: Request) -> Any:
    return _maybe_encrypt_response(request, {"servers": _state(request).list()})


@router.get("/{name}")
def get_server(request: Request, name: str) -> Any:
    reg = _state(request).get(name)
    if reg is None:
        raise HTTPException(status_code=404, detail="Server not registered")
    return _maybe_encrypt_response(request, reg)


@router.put("/{name}")
def register_server(request: Request, name: str, body: dict[str, Any]) -> Any:
    body_any = _maybe_decrypt_body(body)
    if not isinstance(body_any, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    host = body_any.get("host")
    port = body_any.get("port")
    pid = body_any.get("pid")

    if not isinstance(host, str) or not host.strip():
        raise HTTPException(status_code=400, detail="host is required")
    if not isinstance(port, int):
        raise HTTPException(status_code=400, detail="port must be an integer")
    if pid is not None and not isinstance(pid, int):
        raise HTTPException(status_code=400, detail="pid must be an integer when provided")

    reg = _state(request).upsert(name=name, host=host.strip(), port=port, pid=pid)
    return _maybe_encrypt_response(request, reg)


@router.delete("/{name}")
def unregister_server(request: Request, name: str) -> Any:
    if not _state(request).delete(name):
        raise HTTPException(status_code=404, detail="Server not registered")
    return _maybe_encrypt_response(request, {"name": name, "status": "deleted"})

