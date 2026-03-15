"""
License: MIT
Description: Abstract storage backend and a concrete file-based implementation.
Route callers depend only on the abstract interface; the actual storage mechanism
is not exposed. All stored values are encrypted JSON bytes.
"""

from __future__ import annotations

import base64
import hashlib
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


def _safe_filename(s: str) -> str:
    """Encode string for use as filesystem path segment (reversible)."""
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _key_hash(key: str) -> str:
    """Stable short filename for key (avoids 'file name too long' on long URLs)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


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
    """File-based backend: one file per record under data/namespaces/{ns}/{hash}.enc.
    Uses SHA256(key) for filename to avoid 'file name too long' on long URL keys.
    A per-namespace .index.json maps hash -> key for list_keys.
    """

    def __init__(self, root: str | Path = "data/storage") -> None:
        self._root = Path(root)

    def _ns_dir(self, namespace: str) -> Path:
        return self._root / "namespaces" / _safe_filename(namespace)

    def _index_path(self, namespace: str) -> Path:
        return self._ns_dir(namespace) / ".index.json"

    def _path_for_hash(self, namespace: str, h: str) -> Path:
        return self._ns_dir(namespace) / f"{h}.enc"

    def _path_legacy(self, namespace: str, key: str) -> Path:
        """Legacy path (base64 key); can exceed path limit for long keys."""
        return self._ns_dir(namespace) / f"{_safe_filename(key)}.enc"

    def _load_index(self, namespace: str) -> dict[str, str]:
        path = self._index_path(namespace)
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return dict(data.get("hash_to_key") or {})
        except Exception:
            return {}

    def _save_index(self, namespace: str, index: dict[str, str]) -> None:
        path = self._index_path(namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"hash_to_key": index}, indent=0), encoding="utf-8")

    def get(self, namespace: str, key: str) -> bytes | None:
        h = _key_hash(key)
        p = self._path_for_hash(namespace, h)
        if p.is_file():
            return p.read_bytes()
        p_legacy = self._path_legacy(namespace, key)
        if p_legacy.is_file():
            return p_legacy.read_bytes()
        return None

    def set(self, namespace: str, key: str, value: bytes) -> None:
        h = _key_hash(key)
        ns_dir = self._ns_dir(namespace)
        ns_dir.mkdir(parents=True, exist_ok=True)
        self._path_for_hash(namespace, h).write_bytes(value)
        index = self._load_index(namespace)
        index[h] = key
        self._save_index(namespace, index)

    def delete(self, namespace: str, key: str) -> bool:
        h = _key_hash(key)
        p = self._path_for_hash(namespace, h)
        if p.is_file():
            p.unlink()
            index = self._load_index(namespace)
            index.pop(h, None)
            self._save_index(namespace, index)
            return True
        p_legacy = self._path_legacy(namespace, key)
        if p_legacy.is_file():
            p_legacy.unlink()
            return True
        return False

    def _decode_filename(self, b64: str) -> str:
        pad = 4 - (len(b64) % 4)
        if pad != 4:
            b64 += "=" * pad
        return base64.urlsafe_b64decode(b64.encode("ascii")).decode("utf-8")

    def list_keys(self, namespace: str) -> list[str]:
        ns_dir = self._ns_dir(namespace)
        if not ns_dir.is_dir():
            return []
        index = self._load_index(namespace)
        keys: list[str] = []
        for f in ns_dir.iterdir():
            if f.suffix != ".enc" or f.name.startswith("."):
                continue
            stem = f.stem
            if len(stem) == 64 and all(c in "0123456789abcdef" for c in stem):
                k = index.get(stem)
                if k is not None:
                    keys.append(k)
            else:
                try:
                    keys.append(self._decode_filename(stem))
                except Exception:
                    pass
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
