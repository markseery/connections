"""
License: MIT
Description: Encrypts and decrypts bytes using a key from config. Used so that
all stored JSON is persisted in encrypted form; callers use plain JSON via the API.
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .config import get_storage_encryption_key


def _make_fernet(key_bytes: bytes) -> Fernet:
    """Build a Fernet instance from raw key bytes (32 url-safe base64 or arbitrary)."""
    try:
        return Fernet(key_bytes)
    except Exception as exc:
        print(f"[storage] raw Fernet key invalid, deriving via PBKDF2: {exc}", flush=True)
    # If not valid Fernet key, derive one via PBKDF2
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"storage", iterations=480000)
    derived = base64.urlsafe_b64encode(kdf.derive(key_bytes))
    return Fernet(derived)


def encrypt(data: bytes) -> bytes:
    """Encrypt bytes using the storage encryption key from .env."""
    key = get_storage_encryption_key()
    return _make_fernet(key).encrypt(data)


def decrypt(data: bytes) -> bytes:
    """Decrypt bytes; raises if key is wrong or data tampered."""
    key = get_storage_encryption_key()
    return _make_fernet(key).decrypt(data)
