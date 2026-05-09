#!/usr/bin/env python3
"""
Search Google Docs via the Glean MCP server (HTTP transport).

Uses the same MCP endpoint configured in Cursor (~/.cursor/mcp.json)
and speaks the MCP JSON-RPC protocol directly.

Auth:
  Set GLEAN_API_TOKEN in your environment (or .env).
  The token is the same one Glean issues for API / MCP access.

Usage:
  python3 glean_search.py "quarterly planning"
  python3 glean_search.py "roadmap" --owner me
  python3 glean_search.py "*" --owner me --updated past_week
  python3 glean_search.py "budget" --after 2026-03-01
  python3 glean_search.py "design" --type slides --recent
  python3 glean_search.py "onboarding" --read
  python3 glean_search.py chat "summarise the data center messaging docs"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any

import httpx

_MCP_URL_DEFAULT = "https://coreweave-be.glean.com/mcp/default"


def _load_mcp_url() -> str:
    """Resolve the Glean MCP endpoint from ~/.cursor/mcp.json."""
    cfg_path = Path.home() / ".cursor" / "mcp.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
            servers = cfg.get("mcpServers", {})
            for name, spec in servers.items():
                if "glean" in name.lower() and spec.get("type") == "http":
                    return spec["url"]
        except Exception:
            pass
    return _MCP_URL_DEFAULT


def _get_token() -> str:
    token = os.environ.get("GLEAN_API_TOKEN", "").strip()
    if not token:
        env_file = Path.cwd() / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("GLEAN_API_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip("'\"")
                    break
    if not token:
        print(
            "GLEAN_API_TOKEN not set.\n"
            "Export it in your shell or add to .env:\n"
            "  export GLEAN_API_TOKEN='glean_...'",
            file=sys.stderr,
        )
        sys.exit(1)
    return token


def _mcp_call(
    mcp_url: str,
    token: str,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: float = 60.0,
) -> Any:
    """Send a JSON-RPC tools/call to the Glean MCP HTTP endpoint."""
    request_id = str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }

    with httpx.Client(timeout=timeout) as client:
        r = client.post(mcp_url, json=payload, headers=headers)
        r.raise_for_status()

    data = r.json()

    if "error" in data:
        err = data["error"]
        msg = err.get("message", str(err))
        print(f"MCP error: {msg}", file=sys.stderr)
        sys.exit(1)

    result = data.get("result", data)

    if isinstance(result, dict) and "content" in result:
        parts = result["content"]
        texts = []
        for part in parts if isinstance(parts, list) else [parts]:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part["text"])
            elif isinstance(part, str):
                texts.append(part)
        combined = "\n".join(texts)
        try:
            return json.loads(combined)
        except (json.JSONDecodeError, TypeError):
            return combined

    return result


# ── Search ──────────────────────────────────────────────────────────────────

def _build_search_args(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {"query": args.query, "app": "gdrive"}
    if args.owner:
        params["owner"] = args.owner
    if args.from_person:
        params["from"] = args.from_person
    if args.updated:
        params["updated"] = args.updated
    if args.after:
        params["after"] = args.after
    if args.before:
        params["before"] = args.before
    if args.doc_type:
        params["type"] = args.doc_type
    if args.exhaustive:
        params["exhaustive"] = True
    if args.recent:
        params["sort_by_recency"] = True
    return params


def _print_results(results: list[dict[str, Any]], verbose: bool = False) -> None:
    if not results:
        print("No results found.")
        return

    for i, res in enumerate(results, 1):
        title = res.get("title", "Untitled")
        url = res.get("url", "")
        owner = res.get("owner", {})
        owner_name = owner.get("name", "") if isinstance(owner, dict) else ""
        updated_by = res.get("updatedBy", {})
        updated_name = updated_by.get("name", "") if isinstance(updated_by, dict) else ""
        update_time = res.get("updateTime", "")
        create_time = res.get("createTime", "")
        snippets = res.get("snippets", [])
        matching = res.get("matchingFilters", {})
        folders = matching.get("folder", [])
        product = matching.get("product", [])

        print(f"\n{'─' * 60}")
        print(f"  {i}. {title}")
        if url:
            print(f"     {url}")

        meta = []
        if owner_name:
            meta.append(f"Owner: {owner_name}")
        if updated_name and updated_name != owner_name:
            meta.append(f"Updated by: {updated_name}")
        if update_time:
            meta.append(f"Updated: {update_time[:10]}")
        if create_time:
            meta.append(f"Created: {create_time[:10]}")
        if meta:
            print(f"     {' | '.join(meta)}")
        if folders:
            print(f"     Folders: {', '.join(folders)}")
        if product:
            print(f"     Type: {', '.join(product)}")

        if verbose and snippets:
            print()
            for snip in snippets:
                if isinstance(snip, str):
                    clean = snip.replace("<b>", "").replace("</b>", "").strip()
                    if len(clean) > 300:
                        clean = clean[:300] + "..."
                    for line in textwrap.wrap(clean, width=76):
                        print(f"     {line}")

    print(f"\n{'─' * 60}")
    print(f"  {len(results)} result(s)")


def cmd_search(args: argparse.Namespace) -> int:
    mcp_url = _load_mcp_url()
    token = _get_token()
    search_args = _build_search_args(args)

    print(f"Searching Glean (Google Docs): {args.query}", file=sys.stderr)
    if args.debug:
        print(f"MCP endpoint: {mcp_url}", file=sys.stderr)
        print(f"MCP tool args: {json.dumps(search_args, indent=2)}", file=sys.stderr)

    result = _mcp_call(mcp_url, token, "search", search_args, timeout=args.timeout)

    if args.json:
        print(json.dumps(result, indent=2) if isinstance(result, (dict, list)) else result)
        return 0

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            print(result)
            return 0

    docs = []
    if isinstance(result, dict):
        docs = result.get("documents", result.get("results", []))
    elif isinstance(result, list):
        docs = result

    _print_results(docs, verbose=args.verbose)

    if args.read and docs:
        urls = [d.get("url") for d in docs if d.get("url")]
        if urls:
            cmd_read_urls(urls, mcp_url, token, args.timeout)

    return 0


# ── Read Document ───────────────────────────────────────────────────────────

def cmd_read_urls(
    urls: list[str],
    mcp_url: str,
    token: str,
    timeout: float = 60.0,
) -> None:
    print(f"\nReading {len(urls)} document(s) ...", file=sys.stderr)
    result = _mcp_call(mcp_url, token, "read_document", {"urls": urls}, timeout=timeout)
    if isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, indent=2))


def cmd_read(args: argparse.Namespace) -> int:
    mcp_url = _load_mcp_url()
    token = _get_token()
    urls = args.urls
    print(f"Reading {len(urls)} document(s) via Glean MCP ...", file=sys.stderr)
    result = _mcp_call(mcp_url, token, "read_document", {"urls": urls}, timeout=args.timeout)
    if args.json:
        print(json.dumps(result, indent=2) if isinstance(result, (dict, list)) else result)
    elif isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, indent=2))
    return 0


# ── Chat ────────────────────────────────────────────────────────────────────

def cmd_chat(args: argparse.Namespace) -> int:
    mcp_url = _load_mcp_url()
    token = _get_token()
    chat_args: dict[str, Any] = {"message": args.message}
    if args.context:
        chat_args["context"] = args.context

    print(f"Asking Glean: {args.message}", file=sys.stderr)
    result = _mcp_call(mcp_url, token, "chat", chat_args, timeout=args.timeout)

    if args.json:
        print(json.dumps(result, indent=2) if isinstance(result, (dict, list)) else result)
    elif isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, indent=2))
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Query Glean via the MCP endpoint configured in Cursor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s search "quarterly planning"
              %(prog)s search "roadmap" --owner me --verbose
              %(prog)s search "*" --owner me --recent
              %(prog)s search "design" --type slides --after 2026-03-01
              %(prog)s read https://docs.google.com/document/d/1xd.../edit
              %(prog)s chat "summarise the data center messaging strategy"
        """),
    )
    ap.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout (default 60s)")
    sub = ap.add_subparsers(dest="command")

    # -- search
    sp = sub.add_parser("search", help="Search Google Docs", aliases=["s"])
    sp.add_argument("query", help="Search keywords (* for all matching filters)")
    sp.add_argument("--owner", default=None, help="Doc owner (name or 'me')")
    sp.add_argument("--from", dest="from_person", default=None,
                    help="Updated/commented by (name or 'me')")
    sp.add_argument("--updated", default=None,
                    help="Relative time: today, yesterday, past_week, past_month")
    sp.add_argument("--after", default=None, help="Updated after YYYY-MM-DD")
    sp.add_argument("--before", default=None, help="Updated before YYYY-MM-DD")
    sp.add_argument("--type", dest="doc_type", default=None,
                    choices=["spreadsheet", "slides", "email", "folder"],
                    help="Document type filter")
    sp.add_argument("--exhaustive", action="store_true",
                    help="Return exhaustive results")
    sp.add_argument("--recent", action="store_true",
                    help="Sort by recency instead of relevance")
    sp.add_argument("--verbose", "-v", action="store_true",
                    help="Show document snippets")
    sp.add_argument("--read", action="store_true",
                    help="Also fetch full content for each result")
    sp.add_argument("--json", action="store_true", help="Raw JSON output")
    sp.add_argument("--debug", action="store_true", help="Print request details")

    # -- read
    rp = sub.add_parser("read", help="Read full document content", aliases=["r"])
    rp.add_argument("urls", nargs="+", help="One or more document URLs")
    rp.add_argument("--json", action="store_true", help="Raw JSON output")

    # -- chat
    cp = sub.add_parser("chat", help="Ask Glean AI a question", aliases=["c"])
    cp.add_argument("message", help="Your question")
    cp.add_argument("--context", nargs="*", default=None,
                    help="Previous messages for context")
    cp.add_argument("--json", action="store_true", help="Raw JSON output")

    args = ap.parse_args()

    if args.command in ("search", "s"):
        return cmd_search(args)
    elif args.command in ("read", "r"):
        return cmd_read(args)
    elif args.command in ("chat", "c"):
        return cmd_chat(args)
    else:
        ap.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
