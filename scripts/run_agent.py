#!/usr/bin/env python3
"""
Run the Agent skill: send a prompt to the worker's Agent skill, which uses the
AI server to generate a response. Prints the response text to stdout.

With --client (e.g. mark.a.seery@gmail.com), the script uses the storage server
to load or create a "current_memory" record in the client namespace and passes
it as context to every AI call; after each response it appends the exchange to
memory and saves.

Usage:
  python scripts/run_agent.py "Your prompt here"
  python scripts/run_agent.py --client mark.a.seery@gmail.com --prompt "Remember I prefer Python"
  python scripts/run_agent.py --client mark.a.seery@gmail.com "What do I prefer?"

Requires: registry (unless --worker-url), worker with agent_skill, aiserver.
With --client: storage server (via registry or STORAGE_SERVER_URL).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

# Allow running from repo root or from scripts/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.skill_lifecycle import find_live_worker

SKILL_NAME = "agent_skill"
LOAD_TIMEOUT = 10.0
RESPOND_TIMEOUT = 120.0
STORAGE_TIMEOUT = 15.0
CURRENT_MEMORY_KEY = "current_memory"
RECORD_TYPE = "current_memory"


def get_worker_url(registry_url: str) -> str:
    url = find_live_worker(registry_url.rstrip("/"))
    if not url:
        raise SystemExit("No live worker found in registry")
    return url.rstrip("/")


def get_storage_url(registry_url: str) -> str:
    with httpx.Client(timeout=5.0) as client:
        r = client.get(f"{registry_url.rstrip('/')}/servers/storage")
        r.raise_for_status()
        u = (r.json() or {}).get("url", "").strip().rstrip("/")
        if not u:
            raise SystemExit("Registry has no storage server URL")
        return u


def ensure_skill_loaded(worker_url: str) -> None:
    with httpx.Client(timeout=LOAD_TIMEOUT) as client:
        r = client.post(f"{worker_url}/worker/skills/{SKILL_NAME}/load")
        r.raise_for_status()


def call_agent(worker_url: str, prompt: str, profile: str, context: str | None = None) -> dict:
    payload: dict = {"prompt": prompt, "profile": profile}
    if context:
        payload["context"] = context
    with httpx.Client(timeout=RESPOND_TIMEOUT) as client:
        r = client.post(f"{worker_url}/skills/{SKILL_NAME}/respond", json=payload)
        r.raise_for_status()
    return r.json()


# ----- Memory (current_memory record in client namespace) ---------------------

def _memory_record(client: str, attributes: list[dict]) -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "recordType": RECORD_TYPE,
        "namespace": client,
        "attributes": attributes,
    }


def _format_memory_context(attributes: list[dict]) -> str:
    """Turn attributes [{datetime, memory}, ...] into a single context string."""
    if not attributes:
        return ""
    parts = []
    for a in attributes:
        dt = a.get("datetime", "")
        mem = a.get("memory", "")
        if mem:
            parts.append(f"[{dt}]\n{mem}")
    return "\n\n".join(parts)


def get_current_memory(storage_url: str, client: str) -> dict | None:
    """Fetch current_memory record for client namespace. Returns None if 404."""
    ns = quote(client.strip(), safe="")
    key = quote(CURRENT_MEMORY_KEY, safe="")
    url = f"{storage_url}/namespaces/{ns}/records/{key}"
    with httpx.Client(timeout=STORAGE_TIMEOUT) as client_http:
        r = client_http.get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
    data = r.json()
    return (data.get("value") if isinstance(data.get("value"), dict) else None)


def ensure_current_memory(storage_url: str, client: str) -> dict:
    """Get existing current_memory or create and save a new one. Returns the record (with attributes)."""
    existing = get_current_memory(storage_url, client)
    if existing is not None:
        return existing
    record = _memory_record(client, [])
    put_current_memory(storage_url, client, record)
    return record


def put_current_memory(storage_url: str, client: str, record: dict) -> None:
    """Save current_memory record (recordType, namespace, attributes)."""
    ns = quote(client.strip(), safe="")
    key = quote(CURRENT_MEMORY_KEY, safe="")
    url = f"{storage_url}/namespaces/{ns}/records/{key}"
    # Storage expects the value to store; it will add createdAt/updatedAt
    body = {
        "recordType": record.get("recordType", RECORD_TYPE),
        "namespace": record.get("namespace", client),
        "attributes": record.get("attributes", []),
    }
    with httpx.Client(timeout=STORAGE_TIMEOUT) as client_http:
        r = client_http.put(url, json=body)
        r.raise_for_status()


def append_to_memory(record: dict, prompt: str, response_text: str) -> dict:
    """Append one exchange (prompt + response) to record attributes. Returns updated record copy."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    memory_entry = f"User: {prompt}\nAssistant: {response_text}"
    attrs = list(record.get("attributes") or [])
    attrs.append({"datetime": now, "memory": memory_entry})
    return {**record, "attributes": attrs}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the Agent skill: send a prompt and print the AI response."
    )
    ap.add_argument(
        "prompt_arg",
        nargs="?",
        default="",
        help="Prompt text (or use --prompt)",
    )
    ap.add_argument(
        "--prompt",
        metavar="TEXT",
        default="",
        help="Prompt text (overrides positional prompt)",
    )
    ap.add_argument(
        "--client",
        metavar="NAME",
        default="",
        help="Client identifier (e.g. email); enables memory in storage under this namespace",
    )
    ap.add_argument(
        "--profile",
        metavar="NAME",
        default="agent",
        help="Aiserver profile (default: agent)",
    )
    ap.add_argument(
        "--worker-url",
        metavar="URL",
        default="",
        help="Worker base URL; if omitted, discovered from registry",
    )
    ap.add_argument(
        "--storage-url",
        metavar="URL",
        default="",
        help="Storage server URL; if omitted and --client is set, discovered from registry",
    )
    ap.add_argument(
        "--registry-url",
        metavar="URL",
        default="http://127.0.0.1:7002",
        help="Registry URL for worker discovery",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print profile/provider to stderr",
    )
    args = ap.parse_args()

    prompt = (args.prompt or args.prompt_arg or "").strip()
    if not prompt:
        print("Error: prompt is required (positional or --prompt)", file=sys.stderr)
        return 1

    worker_url = (args.worker_url or "").strip().rstrip("/")
    if not worker_url:
        try:
            worker_url = get_worker_url(args.registry_url)
        except SystemExit as e:
            print(e, file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"Using worker: {worker_url}", file=sys.stderr)

    context: str | None = None
    memory_record: dict | None = None
    storage_url = ""
    client = (args.client or "").strip()
    if client:
        storage_url = (
            (args.storage_url or "").strip().rstrip("/")
            or os.environ.get("STORAGE_SERVER_URL", "").strip().rstrip("/")
        )
        if not storage_url:
            try:
                storage_url = get_storage_url(args.registry_url)
            except SystemExit as e:
                print(e, file=sys.stderr)
                return 1
            if not args.quiet:
                print(f"Using storage: {storage_url}", file=sys.stderr)
        try:
            memory_record = ensure_current_memory(storage_url, client)
            context = _format_memory_context(memory_record.get("attributes") or [])
        except Exception as e:
            print(f"Storage/memory error: {e}", file=sys.stderr)
            return 1

    try:
        ensure_skill_loaded(worker_url)
    except Exception as e:
        print(f"Failed to load {SKILL_NAME}: {e}", file=sys.stderr)
        return 1

    try:
        data = call_agent(worker_url, prompt, args.profile, context=context)
    except Exception as e:
        print(f"Agent skill error: {e}", file=sys.stderr)
        return 1

    # Common response shape: summary (optional) + text
    summary = data.get("summary", "").strip()
    text = data.get("text", "")
    if not args.quiet and summary:
        print(summary, file=sys.stderr)
    if not args.quiet and (data.get("profile") or data.get("provider")):
        print(
            f"[profile={data.get('profile')} provider={data.get('provider')}]",
            file=sys.stderr,
        )
    print(text)

    if client and memory_record is not None and storage_url:
        try:
            updated = append_to_memory(memory_record, prompt, text)
            put_current_memory(storage_url, client, updated)
        except Exception as e:
            print(f"Warning: could not save memory: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
