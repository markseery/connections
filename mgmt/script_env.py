"""Repo root on sys.path and working directory for mgmt CLI scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def ensure_sys_path() -> Path:
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return REPO_ROOT


def ensure_repo_cwd() -> Path:
    """Ensure imports and relative config paths resolve from the repo root."""
    root = ensure_sys_path()
    os.chdir(root)
    return root
