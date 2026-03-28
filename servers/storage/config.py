"""
License: MIT
Description: Loads storage server config from environment (.env). Provides the
storage encryption key required for encrypting/decrypting stored JSON records.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from common.user_dir import resolve_env_file

_env_path = resolve_env_file() or Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path)

STORAGE_ENCRYPTION_KEY_ENV = "STORAGE_ENCRYPTION_KEY"


def get_storage_encryption_key() -> bytes:
    """Return the storage encryption key from .env. Raises if missing or invalid."""
    raw = os.environ.get(STORAGE_ENCRYPTION_KEY_ENV)
    if not raw:
        raise RuntimeError(
            f"Missing {STORAGE_ENCRYPTION_KEY_ENV} in environment. "
            "Set it in .env (e.g. run: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")"
        )
    return raw.strip().encode() if isinstance(raw, str) else raw
