"""Repository path helpers for CLI entrypoints (repo root on ``sys.path``)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def repo_root() -> Path:
    return _REPO_ROOT


def ensure_sys_path() -> Path:
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return _REPO_ROOT


def ensure_repo_cwd() -> Path:
    import os

    ensure_sys_path()
    os.chdir(_REPO_ROOT)
    return _REPO_ROOT


def resolve_output_dir(out_dir: Path | str | None, *, segment: str) -> Path:
    """
    Resolve ``--out-dir`` for CLIs: default ``application_files/data/<segment>/``
    under the repository root.
    """
    rel_default = Path("application_files") / "data" / segment
    if out_dir is None:
        p = (_REPO_ROOT / rel_default).resolve()
    else:
        raw = Path(out_dir).expanduser()
        p = raw.resolve() if raw.is_absolute() else (_REPO_ROOT / raw).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p
