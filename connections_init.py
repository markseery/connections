#!/usr/bin/env python3
"""
Scaffold the user directory for the connections framework.

Creates the directory structure at ``application_files/`` (or ``CONNECTIONS_USER_DIR``),
copies ``.env.example`` as a starting ``.env`` if none exists, and configures the
git hooks path to use the committed ``.hooks/`` directory.

Usage:
    python connections_init.py
    CONNECTIONS_USER_DIR=/other/path python connections_init.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

SUBDIRS = [
    "config/skills",
    "config/agents",
    "skills",
    "scripts",
    "workflows",
    "notes",
    "data",
    "data/reports",
    "logs",
]


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    from common.simple.user_dir import user_dir

    udir = user_dir()
    print(f"User directory: {udir}")

    for sub in SUBDIRS:
        (udir / sub).mkdir(parents=True, exist_ok=True)
    print(f"  Created {len(SUBDIRS)} subdirectories")

    env_dest = udir / ".env"
    env_example = REPO_ROOT / ".env.example"
    if not env_dest.is_file() and env_example.is_file():
        shutil.copy2(env_example, env_dest)
        print(f"  Copied .env.example → {env_dest}")
        print(f"  ** Edit {env_dest} to set your API keys and encryption keys **")
    elif env_dest.is_file():
        print(f"  .env already exists at {env_dest}")

    hooks_dir = REPO_ROOT / ".hooks"
    if hooks_dir.is_dir():
        try:
            subprocess.run(
                ["git", "config", "core.hooksPath", ".hooks"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )
            print("  Configured git hooks path → .hooks/")
        except Exception as e:
            print(f"  Warning: could not set git hooks path: {e}")

    print()
    print("Done. Your user directory is ready at:")
    print(f"  {udir}")
    print()
    print("Structure:")
    for sub in SUBDIRS:
        print(f"  {sub}/")
    print()
    print("Override location with: export CONNECTIONS_USER_DIR=/your/path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
