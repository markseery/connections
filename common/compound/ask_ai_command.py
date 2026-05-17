"""Reusable command implementation for ask_ai CLI."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.compound.aiserver_discovery import get_aiserver_base_url
from common.compound.aiserver_generate_client import AiserverGenerateClient
from common.compound.command_base import BaseCommand, UsageError


@dataclass
class AskAiArgs:
    prompt: str
    profile: str = "fast"
    provider: str | None = None
    url: str | None = None
    registry_url: str | None = None
    inprocess: bool = False
    file: str | None = None


def _call_over_http(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or "")
    profile = str(payload.get("profile") or "fast")
    provider_raw = payload.get("provider")
    provider = str(provider_raw).strip() if provider_raw else None
    client = AiserverGenerateClient(url, timeout_sec=120.0)
    return client.generate(prompt=prompt, profile=profile, provider=provider)


def _call_inprocess(payload: dict[str, Any]) -> dict[str, Any]:
    from fastapi.testclient import TestClient
    from servers.aiserver.main import app

    with TestClient(app) as client:
        response = client.post("/generate", json=payload)
        response.raise_for_status()
        return response.json()


def _build_prompt_with_context(base_prompt: str, file_path: str | None) -> str:
    if not file_path:
        return base_prompt

    path = Path(file_path)
    if not path.is_file():
        raise UsageError(f"context file not found: {path}")
    try:
        context = path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception as exc:
        raise UsageError(f"reading context file failed: {exc}") from exc

    return (
        "Use the following context when answering.\n\n"
        "--- Context ---\n"
        f"{context}\n\n"
        "--- End context ---\n\n"
        f"{base_prompt}"
    )


class AskAiCommand(BaseCommand[AskAiArgs]):
    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Send a prompt to the AI server.")
        parser.add_argument("prompt", help="Prompt text")
        parser.add_argument(
            "--profile",
            default="fast",
            help="One of: fast, chat, reason, agent, code, image, video (default: fast)",
        )
        parser.add_argument(
            "--provider",
            default=None,
            help="Optional provider: ollama, openai, xai, google, perplexity, wandb (default: server default)",
        )
        parser.add_argument(
            "--url",
            default=None,
            help="AI server base URL (overrides registry lookup).",
        )
        parser.add_argument(
            "--registry-url",
            default=os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002"),
            help="Registry server URL for discovering aiserver (default: REGISTRY_SERVER_URL or 127.0.0.1:7002).",
        )
        parser.add_argument(
            "--inprocess",
            action="store_true",
            help="Call the AI server in-process (no HTTP server required).",
        )
        parser.add_argument(
            "--file",
            "-f",
            default=None,
            metavar="PATH",
            help="Path to a file whose contents are used as context for the prompt (prepended before the prompt).",
        )
        return parser

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> AskAiArgs:
        return AskAiArgs(
            prompt=str(args.prompt),
            profile=str(args.profile),
            provider=(str(args.provider).strip() if args.provider else None),
            url=(str(args.url).strip() if args.url else None),
            registry_url=(str(args.registry_url).strip() if args.registry_url else None),
            inprocess=bool(args.inprocess),
            file=args.file,
        )

    @classmethod
    def run(cls, args: AskAiArgs) -> int:
        prompt_text = _build_prompt_with_context(args.prompt, args.file)
        payload: dict[str, Any] = {"prompt": prompt_text, "profile": args.profile}
        if args.provider:
            payload["provider"] = args.provider

        try:
            if args.inprocess:
                out = _call_inprocess(payload)
            else:
                out = _call_over_http(
                    get_aiserver_base_url(
                        explicit=args.url,
                        registry_override=args.registry_url,
                    ),
                    payload,
                )
        except Exception as exc:
            if not args.inprocess:
                raise UsageError(
                    f"{exc}\nTip: if the server isn't running, try: python mgmt/ask_ai.py \"...\" --inprocess"
                ) from exc
            raise UsageError(str(exc)) from exc

        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

