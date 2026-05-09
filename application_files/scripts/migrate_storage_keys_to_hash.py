#!/usr/bin/env python3
"""
Migrate storage records from legacy base64-key filenames to hash-key format.

Legacy: namespace dir contains {base64(key)}.enc (long URLs can exceed path limit).
New:    namespace dir contains {sha256(key).hex}.enc plus .index.json (hash -> key).

Run from project root. Stop the storage server before running to avoid conflicts.

Usage:
  python migrate_storage_keys_to_hash.py [--namespace rss_notified] [--dry-run]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path


def _safe_filename(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _decode_b64_stem(stem: str) -> str:
    pad = 4 - (len(stem) % 4)
    if pad != 4:
        stem = stem + "=" * pad
    return base64.urlsafe_b64decode(stem.encode("ascii")).decode("utf-8")


def _is_new_format_stem(stem: str) -> bool:
    return len(stem) == 64 and all(c in "0123456789abcdef" for c in stem)


def migrate_namespace(storage_root: Path, namespace: str, dry_run: bool) -> int:
    ns_dir = storage_root / "namespaces" / _safe_filename(namespace)
    if not ns_dir.is_dir():
        print(f"Namespace dir not found: {ns_dir}", file=sys.stderr)
        return 0

    index_path = ns_dir / ".index.json"
    index: dict[str, str] = {}
    if index_path.is_file():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            index = dict(data.get("hash_to_key") or {})
        except Exception as e:
            print(f"Warning: could not load index: {e}", file=sys.stderr)

    migrated = 0
    for f in sorted(ns_dir.iterdir()):
        if f.suffix != ".enc" or f.name.startswith("."):
            continue
        stem = f.stem
        if _is_new_format_stem(stem):
            continue  # already new format
        try:
            key = _decode_b64_stem(stem)
        except Exception as e:
            print(f"Skip {f.name}: decode failed: {e}", file=sys.stderr)
            continue
        h = _key_hash(key)
        new_path = ns_dir / f"{h}.enc"
        if new_path.exists():
            print(f"Skip {key[:60]}...: {h}.enc already exists", file=sys.stderr)
            continue
        raw = f.read_bytes()
        if dry_run:
            print(f"[dry-run] would migrate {stem[:40]}... -> {h}.enc (key len={len(key)})")
            migrated += 1
            continue
        new_path.write_bytes(raw)
        index[h] = key
        f.unlink()
        migrated += 1
        print(f"Migrated {key[:60]}... -> {h}.enc")

    if not dry_run and migrated > 0:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps({"hash_to_key": index}, indent=0), encoding="utf-8")
        print(f"Updated {index_path.name} ({len(index)} entries)")

    return migrated


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate storage records to hash-key format.")
    ap.add_argument(
        "--namespace",
        default="rss_notified",
        help="Namespace to migrate (default: rss_notified)",
    )
    ap.add_argument(
        "--storage-root",
        default=None,
        help="Storage root dir (default: project_root/data/storage)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Do not write; only print what would be done")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent
    storage_root = Path(args.storage_root) if args.storage_root else root / "data" / "storage"
    if not storage_root.is_dir():
        print(f"Storage root not found: {storage_root}", file=sys.stderr)
        return 1

    n = migrate_namespace(storage_root, args.namespace.strip(), args.dry_run)
    print(f"Done: {n} record(s) {'would be ' if args.dry_run else ''}migrated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
