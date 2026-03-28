"""
License: MIT
Description: Transport-level encryption for messages sent to/from servers.

Uses a transport encryption key loaded from `.env` (TRANSPORT_ENCRYPTION_KEY) to
encrypt/decrypt payloads. This is separate from at-rest storage encryption.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from dotenv import load_dotenv


TRANSPORT_ENCRYPTION_KEY_ENV = "TRANSPORT_ENCRYPTION_KEY"


def _load_env() -> None:
    from common.simple.user_dir import resolve_env_file
    env_path = resolve_env_file() or Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(env_path)


def _derive_fernet_key(key_bytes: bytes) -> bytes:
    """Accept raw Fernet key bytes, or derive a Fernet key from arbitrary bytes."""
    try:
        Fernet(key_bytes)
        return key_bytes
    except Exception as exc:
        print(f"[transport] raw Fernet key invalid, deriving via PBKDF2: {exc}", flush=True)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"transport",
            iterations=480000,
        )
        return base64.urlsafe_b64encode(kdf.derive(key_bytes))


class TransportEncryption:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(_derive_fernet_key(key))

    @classmethod
    def from_env(cls) -> "TransportEncryption":
        _load_env()
        raw = os.environ.get(TRANSPORT_ENCRYPTION_KEY_ENV)
        if not raw:
            raise RuntimeError(
                f"Missing {TRANSPORT_ENCRYPTION_KEY_ENV} in environment. "
                "Set it in .env (e.g. Fernet.generate_key())"
            )
        key = raw.strip().encode("utf-8")
        return cls(key)

    def encrypt_bytes(self, data: bytes) -> str:
        """Encrypt bytes; returns a url-safe token string."""
        return self._fernet.encrypt(data).decode("utf-8")

    def decrypt_bytes(self, token: str) -> bytes:
        """Decrypt token string; returns bytes."""
        return self._fernet.decrypt(token.encode("utf-8"))

    def encrypt_json(self, obj: Any) -> str:
        """Encrypt a JSON-serializable object; returns token string."""
        return self.encrypt_bytes(json.dumps(obj).encode("utf-8"))

    def decrypt_json(self, token: str) -> Any:
        """Decrypt token string and parse JSON."""
        return json.loads(self.decrypt_bytes(token).decode("utf-8"))


_singleton: TransportEncryption | None = None


def get_transport_encryption() -> TransportEncryption:
    global _singleton
    if _singleton is None:
        _singleton = TransportEncryption.from_env()
    return _singleton

