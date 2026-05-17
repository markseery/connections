"""Repo root on sys.path and working directory for mgmt CLI scripts."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_root = str(_REPO_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.simple.script_env import (  # noqa: E402
    ensure_repo_cwd,
    ensure_sys_path,
    repo_root,
    resolve_output_dir,
)

__all__ = ["ensure_repo_cwd", "ensure_sys_path", "repo_root", "resolve_output_dir"]
