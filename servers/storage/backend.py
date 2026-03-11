"""
License: MIT
Description: Abstract storage backend and a concrete file-based implementation.
Route callers depend only on the abstract interface; the actual storage mechanism
is not exposed. All stored values are encrypted JSON bytes.
"""

from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


def _safe_filename(s: str) -> str:
    """Encode string for use as filesystem path segment (reversible)."""
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")

from .encryption import decrypt, encrypt


class StorageBackend(ABC):
    """Abstract backend: record keys are unique within a namespace."""

    @abstractmethod
    def get(self, namespace: str, key: str) -> bytes | None:
        """Return raw (encrypted) value or None if missing."""
        ...

    @abstractmethod
    def set(self, namespace: str, key: str, value: bytes) -> None:
        """Store raw (encrypted) value."""
        ...

    @abstractmethod
    def delete(self, namespace: str, key: str) -> bool:
        """Remove record; return True if it existed."""
        ...

    @abstractmethod
    def list_keys(self, namespace: str) -> list[str]:
        """Return all record keys in the namespace."""
        ...


class FileEncryptedBackend(StorageBackend):
    """File-based backend: one file per record under data/namespaces/{namespace}/{key}.enc."""

    def __init__(self, root: str | Path = "data/storage") -> None:
        self._root = Path(root)

    def _path(self, namespace: str, key: str) -> Path:
        return self._root / "namespaces" / _safe_filename(namespace) / f"{_safe_filename(key)}.enc"

    def get(self, namespace: str, key: str) -> bytes | None:
        p = self._path(namespace, key)
        if not p.is_file():
            return None
        return p.read_bytes()

    def set(self, namespace: str, key: str, value: bytes) -> None:
        p = self._path(namespace, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(value)

    def delete(self, namespace: str, key: str) -> bool:
        p = self._path(namespace, key)
        if not p.is_file():
            return False
        p.unlink()
        return True

    def _decode_filename(self, b64: str) -> str:
        pad = 4 - (len(b64) % 4)
        if pad != 4:
            b64 += "=" * pad
        return base64.urlsafe_b64decode(b64.encode("ascii")).decode("utf-8")

    def list_keys(self, namespace: str) -> list[str]:
        dir_path = self._root / "namespaces" / _safe_filename(namespace)
        if not dir_path.is_dir():
            return []
        keys: list[str] = []
        for f in dir_path.iterdir():
            if f.suffix == ".enc":
                keys.append(self._decode_filename(f.stem))
        return sorted(keys)


def get_json(backend: StorageBackend, namespace: str, key: str) -> Any | None:
    """Get decrypted JSON record; return None if missing."""
    raw = backend.get(namespace, key)
    if raw is None:
        return None
    return json.loads(decrypt(raw).decode("utf-8"))


def set_json(backend: StorageBackend, namespace: str, key: str, value: Any) -> None:
    """Serialize to JSON and store encrypted."""
    backend.set(namespace, key, encrypt(json.dumps(value).encode("utf-8")))
