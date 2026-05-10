"""
User directory resolution for the connections framework.

Each user has a personal directory (default ``<repo>/application_files/``)
that holds config overrides, custom skills, scripts, workflows, data, and
logs.  This directory is gitignored so user content never leaks into commits.

The framework resolves paths with a priority chain: user dir first, then
repo defaults — so user files win without touching the shared codebase.

Override the location via the ``CONNECTIONS_USER_DIR`` environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_USER_DIR = _REPO_ROOT / "application_files"

_cached: Path | None = None


def user_dir() -> Path:
    """Return the resolved user directory, creating it if absent."""
    global _cached
    if _cached is not None:
        return _cached
    raw = os.environ.get("CONNECTIONS_USER_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
        _cached = (p if p.is_absolute() else _REPO_ROOT / p).resolve()
    else:
        _cached = _DEFAULT_USER_DIR.resolve()
    _cached.mkdir(parents=True, exist_ok=True)
    return _cached


def repo_root() -> Path:
    return _REPO_ROOT


def resolve_config(relative: str | Path) -> Path:
    """Return user override if it exists, otherwise the repo default.

    *relative* is something like ``config/skills/stock_skill.yaml``.
    """
    user_path = user_dir() / relative
    if user_path.is_file():
        return user_path
    return _REPO_ROOT / relative


def resolve_config_dir(relative: str | Path) -> tuple[Path | None, Path | None]:
    """Return (user_config_dir, repo_config_dir) for a relative dir path.

    Either may be ``None`` if it doesn't exist.
    """
    user_path = user_dir() / relative
    repo_path = _REPO_ROOT / relative
    return (
        user_path if user_path.is_dir() else None,
        repo_path if repo_path.is_dir() else None,
    )


def resolve_data(relative: str | Path) -> Path:
    """Data always lives in the user directory."""
    p = user_dir() / "data" / relative
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def resolve_logs() -> Path:
    p = user_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_env_file() -> Path | None:
    """Return one ``.env`` path for callers that need a single file (e.g. display).

    Prefer ``application_files/.env`` when it exists, else repo root ``.env``.
    For loading variables into ``os.environ``, use :func:`load_connections_dotenv`
    instead so keys in *either* file are applied.
    """
    user_env = user_dir() / ".env"
    if user_env.is_file():
        return user_env
    repo_env = _REPO_ROOT / ".env"
    if repo_env.is_file():
        return repo_env
    return None


def load_connections_dotenv() -> None:
    """Load ``.env`` from repo root and user dir into ``os.environ``.

    Loads ``<repo>/.env`` first with ``override=False`` (does not clobber vars
    already exported in the shell), then ``application_files/.env`` with
    ``override=True`` so user entries win on the same key.

    When both files exist, variables present only in the repo file are still
    visible after the second load (dotenv does not unset keys omitted from the
    user file). That matches the common layout: secrets in repo ``.env``, plus a
    smaller ``application_files/.env`` for overrides.
    """
    from dotenv import load_dotenv

    repo_env = _REPO_ROOT / ".env"
    if repo_env.is_file():
        load_dotenv(repo_env, override=False)
    user_env = user_dir() / ".env"
    if user_env.is_file():
        load_dotenv(user_env, override=True)


def resolve_workflows_dir() -> tuple[Path | None, Path | None]:
    """Return (user_workflows_dir, repo_workflows_dir)."""
    user_wf = user_dir() / "workflows"
    repo_wf = _REPO_ROOT / "data" / "workflows"
    return (
        user_wf if user_wf.is_dir() else None,
        repo_wf if repo_wf.is_dir() else None,
    )


def resolve_workflow(name: str) -> Path | None:
    """Find a workflow YAML by name — user dir first, then repo data/workflows."""
    user_wf, repo_wf = resolve_workflows_dir()
    if user_wf:
        p = user_wf / name
        if p.is_file():
            return p
    if repo_wf:
        p = repo_wf / name
        if p.is_file():
            return p
    return None


def user_skills_dir() -> Path | None:
    """Return the user's custom skills directory if it exists and has .py files."""
    d = user_dir() / "skills"
    if d.is_dir() and any(d.glob("*.py")):
        return d
    return None
