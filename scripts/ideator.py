#!/usr/bin/env python3
"""
Ideator — multi-persona AI deliberation engine.

Multiple AI panelists, each with a distinct persona and backed by a different
model profile/provider, hold a structured multi-round conversation on a topic.
Panelists can request skill calls mid-conversation for real data.

Usage:
  python3 scripts/ideator.py data/ideator/competitive_positioning.yaml
  python3 scripts/ideator.py data/ideator/competitive_positioning.yaml --rounds 3
  python3 scripts/ideator.py my_session.yaml --max-workers 6 --ai-timeout 120

Environment:
  REGISTRY_SERVER_URL — default http://127.0.0.1:7002
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.context_curator import ContextCurator
from common.http_client import http_client
from common.registry_client import get_server_url
from common.skill_catalog import SkillCatalog

_BLOCKED_ROUTES: set[str] = {
    "POST /skills/webscraper_skill/scrape",
    "PUT /skills/webscraper_skill/pages",
    "DELETE /skills/webscraper_skill/pages",
    "POST /skills/webscraper_skill/pages",
}


# ── Data structures ──────────────────────────────────────────────────────

class Persona:
    __slots__ = ("id", "name", "profile", "provider", "role", "experience",
                 "concerns", "personality", "speaks_to")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.id: str = raw["id"]
        self.name: str = raw.get("name", self.id.title())
        self.profile: str = raw.get("profile", "agent")
        self.provider: str | None = raw.get("provider")
        self.role: str = raw.get("role", "Participant")
        self.experience: str = raw.get("experience", "")
        self.concerns: list[str] = raw.get("concerns") or []
        self.personality: str = raw.get("personality", "")
        self.speaks_to: list[str] = raw.get("speaks_to") or []

    def system_prompt(self, topic: str, context: str,
                      persona_names: dict[str, str], skill_catalog: str) -> str:
        concerns_str = ", ".join(self.concerns) if self.concerns else "general"
        speaks_to_names = ", ".join(
            persona_names.get(pid, pid) for pid in self.speaks_to
        ) if self.speaks_to else "everyone"

        parts = [
            f"You are {self.name}, a {self.role}.",
            f"Experience: {self.experience}",
            f"Your concerns in this discussion: {concerns_str}.",
            f"Your personality: {self.personality}",
            "",
            f"TOPIC: {topic}",
            f"CONTEXT:\n{context}",
            "",
        ]
        if skill_catalog:
            parts.append(skill_catalog)
            parts.append("")
        parts.extend([
            "RULES:",
            "- Stay in character. Your perspective is shaped by your role and concerns.",
            "- Engage directly with what others have said. Name them.",
            "- Be concise. 2-4 paragraphs max per turn.",
            f"- You are speaking to: {speaks_to_names}. Address them specifically.",
        ])
        return "\n".join(parts)


class Transcript:
    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def append(self, speaker_id: str, speaker_name: str, round_num: int,
               wave: int, text: str, profile: str, provider: str | None) -> None:
        with self._lock:
            self._entries.append({
                "speaker_id": speaker_id,
                "speaker_name": speaker_name,
                "round": round_num,
                "wave": wave,
                "text": text,
                "profile": profile,
                "provider": provider or "",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    def append_skill_result(self, round_num: int, skill: str, result: str) -> None:
        with self._lock:
            self._entries.append({
                "speaker_id": "_system",
                "speaker_name": "Skill Result",
                "round": round_num,
                "wave": -1,
                "text": f"[SKILL_RESULT: {skill}]\n{result}",
                "profile": "",
                "provider": "",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    def format_for_prompt(self) -> str:
        parts: list[str] = []
        for e in self._entries:
            parts.append(f"**{e['speaker_name']}** (round {e['round']}):\n{e['text']}")
        return "\n\n---\n\n".join(parts)

    def to_markdown(self) -> str:
        parts: list[str] = []
        current_round = -1
        for e in self._entries:
            if e["round"] != current_round:
                current_round = e["round"]
                parts.append(f"\n## Round {current_round}\n")
            if e["speaker_id"] == "_system":
                parts.append(f"### {e['speaker_name']}\n\n{e['text']}\n")
            else:
                meta = f"*{e['profile']}"
                if e["provider"]:
                    meta += f" via {e['provider']}"
                meta += "*"
                parts.append(
                    f"### {e['speaker_name']} ({e['speaker_id']})\n\n"
                    f"{meta}\n\n{e['text']}\n"
                )
        return "\n".join(parts)

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)


# ── AI and skill calls ───────────────────────────────────────────────────

def _ai_generate(prompt: str, profile: str, provider: str | None,
                 timeout: float) -> str:
    aiserver = get_server_url("aiserver")
    payload: dict[str, Any] = {"prompt": prompt, "profile": profile}
    if provider and provider not in ("not-specified", ""):
        payload["provider"] = provider

    with http_client("ai_generate", timeout=timeout) as client:
        r = client.post(f"{aiserver}/generate", json=payload)
        r.raise_for_status()

    data = r.json()
    out = data.get("output")
    if isinstance(out, dict):
        return str(out.get("text", "")).strip()
    if isinstance(out, str):
        return out.strip()
    return str(out).strip()


_SKILL_REQUEST_RE = re.compile(
    r"\[SKILL_REQUEST:\s*(\S+)\s+(\{[^]]*\})\]",
)


def _extract_skill_requests(text: str) -> list[tuple[str, str, dict[str, Any]]]:
    results: list[tuple[str, str, dict[str, Any]]] = []
    for m in _SKILL_REQUEST_RE.finditer(text):
        endpoint = m.group(1).strip()
        raw_json = m.group(2)
        try:
            args = json.loads(raw_json)
        except json.JSONDecodeError:
            depth = 0
            end = -1
            start = text.index(raw_json[0], m.start(2))
            for i, ch in enumerate(text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > 0:
                try:
                    args = json.loads(text[start:end])
                except json.JSONDecodeError:
                    continue
            else:
                continue
        results.append((endpoint, m.group(0), args))
    return results


_READ_FILE_RE = re.compile(r"\[READ_FILE:\s*(.+?)\]")

_MAX_FILE_CHARS = 80_000


def _extract_file_requests(text: str) -> list[str]:
    return [m.group(1).strip() for m in _READ_FILE_RE.finditer(text)]


def _read_local_file(path_str: str) -> str:
    p = (ROOT / path_str).resolve()
    if not p.is_file():
        return f"File not found: {path_str}"
    if not str(p).startswith(str(ROOT)):
        return f"Access denied: {path_str} is outside the project"
    content = p.read_text(encoding="utf-8", errors="replace")
    if len(content) > _MAX_FILE_CHARS:
        content = content[:_MAX_FILE_CHARS] + f"\n\n[... truncated at {_MAX_FILE_CHARS} chars ...]"
    return content


def _call_skill(worker_url: str, endpoint: str, args: dict[str, Any],
                timeout: float, method: str = "POST") -> str:
    if not endpoint.startswith("/"):
        endpoint = f"/skills/{endpoint}"
    url = f"{worker_url.rstrip('/')}{endpoint}"
    with http_client("skill_call", timeout=timeout) as client:
        if method == "GET":
            r = client.get(url, params=args or None)
        else:
            r = client.post(url, json=args)
        r.raise_for_status()

    data = _parse_skill_response(r)

    if isinstance(data, dict) and _is_async_job(data):
        job_id = _extract_job_id(data)
        if job_id:
            poll_base = endpoint.rstrip("/")
            return _poll_skill_job(worker_url, f"{poll_base}/{job_id}", timeout)

    return _format_skill_result(data)


def _parse_skill_response(r: Any) -> Any:
    try:
        return r.json()
    except Exception:
        return r.text


def _is_async_job(data: dict[str, Any]) -> bool:
    status = data.get("status") or data.get("data", {}).get("status", "")
    job_id = data.get("job_id") or data.get("data", {}).get("job_id", "")
    return bool(job_id) and status in ("pending", "running", "queued")


def _extract_job_id(data: dict[str, Any]) -> str:
    return str(
        data.get("job_id")
        or data.get("data", {}).get("job_id", "")
    )


def _poll_skill_job(worker_url: str, poll_path: str, timeout: float,
                    interval: float = 2.0, max_attempts: int = 60) -> str:
    url = f"{worker_url.rstrip('/')}{poll_path}"
    for attempt in range(1, max_attempts + 1):
        time.sleep(interval)
        with http_client("skill_call", timeout=30.0) as client:
            r = client.get(url)
        if r.status_code != 200:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        status = data.get("status") or data.get("data", {}).get("status", "")
        if status == "completed":
            return _format_skill_result(data)
        if status == "failed":
            return f"Skill job failed: {data.get('error', 'unknown error')}"
    return "Skill job timed out waiting for completion"


def _format_skill_result(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    summary = data.get("summary", "")
    items = data.get("items")
    if summary and items:
        return f"{summary}\n{json.dumps(items[:10], indent=2)}"
    if summary:
        return summary
    text = data.get("text", "")
    if text:
        return text
    return json.dumps(data, indent=2)


# ── Wave resolution ──────────────────────────────────────────────────────

def resolve_waves(personas: list[Persona]) -> list[list[Persona]]:
    """Split personas into waves based on speaks_to dependencies.

    Wave 1: personas whose speaks_to targets are not in the persona list
    (they speak "first" — they don't depend on hearing from other panelists
    this round). Wave 2: everyone who speaks_to someone in wave 1. Etc.

    Falls back to a single wave if the graph is circular.
    """
    id_set = {p.id for p in personas}
    by_id = {p.id: p for p in personas}
    placed: set[str] = set()
    waves: list[list[Persona]] = []

    remaining = list(personas)
    for _ in range(len(personas)):
        wave = [
            p for p in remaining
            if all(dep in placed or dep not in id_set for dep in p.speaks_to)
        ]
        if not wave:
            waves.append(remaining)
            break
        waves.append(wave)
        for p in wave:
            placed.add(p.id)
        remaining = [p for p in remaining if p.id not in placed]
        if not remaining:
            break

    return waves


# ── Main engine ──────────────────────────────────────────────────────────

_DELAY_BETWEEN_SPEAKERS = 6.0
_DELAY_BETWEEN_ROUNDS = 15.0


def run_ideation(
    config_path: Path,
    *,
    rounds_override: int | None = None,
    max_workers: int = 4,
    ai_timeout: float = 180.0,
    skill_timeout: float = 60.0,
    verbose: bool = True,
) -> Path:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    topic = cfg["topic"]
    base_context = cfg.get("context", "")
    rounds = rounds_override or cfg.get("rounds", 5)
    synthesis_template = cfg.get("synthesis_prompt", "")

    personas = [Persona(p) for p in cfg["personas"]]
    persona_names = {p.id: p.name for p in personas}
    waves = resolve_waves(personas)

    registry_url = os.environ.get("REGISTRY_SERVER_URL", "http://127.0.0.1:7002").rstrip("/")

    sites_dir = ROOT / "data" / "webscrape" / "sites"

    # ── Context curation ──────────────────────────────────────────────
    if verbose:
        print("Curating context…\n", flush=True)

    curator = ContextCurator(
        topic=topic,
        context=base_context,
        sites_dir=sites_dir,
        timeout=ai_timeout,
        verbose=verbose,
    )
    curated = curator.curate()

    if curated and base_context:
        context = f"{base_context}\n\n{curated}"
    elif curated:
        context = curated
    else:
        context = base_context

    if verbose:
        print()
    site_files: list[str] = []
    if sites_dir.is_dir():
        site_files = sorted(
            f"data/webscrape/sites/{p.name}" for p in sites_dir.glob("*.md")
        )

    catalog = SkillCatalog(
        registry_url=registry_url,
        blocked_routes=_BLOCKED_ROUTES,
        local_files=site_files,
    )
    catalog.discover()

    worker_url = catalog.worker_url
    skill_catalog_text = catalog.render(
        request_instruction=(
            "To use a skill, put this on its own line:\n"
            '  [SKILL_REQUEST: /full/route/path {"param": "value", ...}]\n'
            "Use the exact route path and parameter names from the list above. "
            "Only request data when it would strengthen your argument. "
            "Do NOT request /scrape — use the stored content routes or local files instead."
        ),
    )

    if verbose:
        print(f"Topic: {topic}")
        print(f"Personas: {', '.join(p.name for p in personas)}")
        print(f"Rounds: {rounds}")
        print(f"Waves per round: {len(waves)} ({' → '.join(str(len(w)) for w in waves)})")
        print(f"Site files available: {len(site_files)}")
        print(f"Skills discovered: {len(catalog.skills)} skills, "
              f"{sum(len(s.routes) for s in catalog.skills)} routes")
        if not worker_url:
            print("[ideator] WARNING: no live worker found; skill requests will fail")
        print()

    transcript = Transcript()

    system_prompts = {
        p.id: p.system_prompt(topic, context, persona_names, skill_catalog_text)
        for p in personas
    }

    for round_num in range(1, rounds + 1):
        if verbose:
            print(f"{'=' * 60}")
            print(f"  ROUND {round_num}")
            print(f"{'=' * 60}")

        for wave_idx, wave in enumerate(waves):
            conversation_so_far = transcript.format_for_prompt()

            def _speak(persona: Persona) -> tuple[str, str]:
                if conversation_so_far:
                    addresses = ", ".join(
                        persona_names.get(pid, pid) for pid in persona.speaks_to
                    ) if persona.speaks_to else "the group"
                    turn_prompt = (
                        f"{system_prompts[persona.id]}\n\n"
                        "--- CONVERSATION SO FAR ---\n\n"
                        f"{conversation_so_far}\n\n"
                        "--- YOUR TURN ---\n\n"
                        f"Respond to the conversation above. Address {addresses} specifically. "
                        "Build on, challenge, or redirect what has been said."
                    )
                else:
                    turn_prompt = (
                        f"{system_prompts[persona.id]}\n\n"
                        "--- YOUR TURN ---\n\n"
                        "You are opening the discussion. Share your initial take on the topic. "
                        "Set the tone for the conversation."
                    )

                text = _ai_generate(
                    turn_prompt, persona.profile, persona.provider, ai_timeout
                )
                return persona.id, text

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_speak, p): p for p in wave}
                for future in as_completed(futures):
                    persona = futures[future]
                    try:
                        pid, text = future.result()
                        transcript.append(
                            pid, persona.name, round_num, wave_idx,
                            text, persona.profile, persona.provider,
                        )
                        if verbose:
                            print(f"\n  [{persona.name}] ({persona.profile}"
                                  f"{' via ' + persona.provider if persona.provider else ''}):")
                            for line in text.split("\n"):
                                print(f"    {line}")
                    except Exception as exc:
                        error_msg = f"[ERROR: {exc}]"
                        transcript.append(
                            persona.id, persona.name, round_num, wave_idx,
                            error_msg, persona.profile, persona.provider,
                        )
                        if verbose:
                            print(f"\n  [{persona.name}] FAILED: {exc}")
                    time.sleep(_DELAY_BETWEEN_SPEAKERS)

            # Process skill requests and file reads from this wave.
            # All results are appended to the transcript before any further
            # speaking happens, so the next wave/round sees them.
            wave_entries = [
                e for e in transcript.entries
                if e["round"] == round_num and e["wave"] == wave_idx
            ]

            pending_requests: list[tuple[str, str, dict[str, Any]]] = []
            pending_files: list[str] = []

            route_methods = catalog.route_methods
            if catalog.routes:
                for entry in wave_entries:
                    for endpoint, raw_match, args in _extract_skill_requests(entry["text"]):
                        pending_requests.append((endpoint, raw_match, args))

            for entry in wave_entries:
                pending_files.extend(_extract_file_requests(entry["text"]))

            if pending_requests or pending_files:
                if verbose:
                    total = len(pending_requests) + len(pending_files)
                    print(f"\n  --- Pausing conversation: {total} data request(s) pending ---")

                for endpoint, raw_match, args in pending_requests:
                    if f"POST {endpoint}" in _BLOCKED_ROUTES:
                        transcript.append_skill_result(
                            round_num, endpoint,
                            f"Blocked: {endpoint} — use stored content or local files instead"
                        )
                        if verbose:
                            print(f"\n  [SKILL] BLOCKED: {endpoint}")
                        continue
                    if endpoint not in route_methods:
                        transcript.append_skill_result(
                            round_num, endpoint,
                            f"Unknown route: {endpoint} — not in discovered skills"
                        )
                        continue
                    method = route_methods[endpoint]
                    if verbose:
                        print(f"\n  [SKILL] {method} {endpoint} → {json.dumps(args)[:100]}...")
                    try:
                        result = _call_skill(
                            worker_url, endpoint, args, skill_timeout, method
                        )
                        transcript.append_skill_result(round_num, endpoint, result)
                        if verbose:
                            print(f"    Result: {result[:200]}...")
                    except Exception as exc:
                        transcript.append_skill_result(
                            round_num, endpoint, f"Error: {exc}"
                        )
                        if verbose:
                            print(f"    FAILED: {exc}")

                for fp in pending_files:
                    if verbose:
                        print(f"\n  [FILE] Reading {fp}...")
                    content = _read_local_file(fp)
                    transcript.append_skill_result(round_num, f"file:{fp}", content)
                    if verbose:
                        print(f"    Read {len(content)} chars")

                if verbose:
                    print(f"\n  --- All data received, conversation resuming ---")

        if round_num < rounds:
            if verbose:
                print(f"\n  [waiting {_DELAY_BETWEEN_ROUNDS:.0f}s before next round…]", flush=True)
            time.sleep(_DELAY_BETWEEN_ROUNDS)

    # ── Synthesis ────────────────────────────────────────────────────────

    if verbose:
        print(f"\n{'=' * 60}")
        print("  SYNTHESIS")
        print(f"{'=' * 60}\n")

    participants_str = ", ".join(f"{p.name} ({p.role})" for p in personas)
    transcript_str = transcript.format_for_prompt()

    preamble = (
        f"You are a senior facilitator synthesizing a multi-persona deliberation.\n\n"
        f"TOPIC: {topic}\n"
        f"CONTEXT:\n{context}\n\n"
        f"PARTICIPANTS: {participants_str}\n\n"
        "--- FULL TRANSCRIPT ---\n\n"
        f"{transcript_str}\n\n"
        "--- SYNTHESIS TASK ---\n\n"
    )

    if synthesis_template:
        try:
            instructions = synthesis_template.format(
                topic=topic,
                context=context,
                participants=participants_str,
                transcript=transcript_str,
            )
        except KeyError:
            instructions = synthesis_template
        synthesis_prompt = preamble + instructions
    else:
        synthesis_prompt = preamble + (
            "Produce a structured synthesis with these sections:\n"
            "1. **Key Agreements** — points where participants converged\n"
            "2. **Unresolved Tensions** — disagreements or open questions\n"
            "3. **Recommended Actions** — concrete next steps that emerged\n"
            "4. **Notable Quotes** — one standout quote per participant (attributed)\n"
            "5. **Overall Assessment** — your assessment of the discussion quality "
            "and what was missing\n\n"
            "Be thorough but concise."
        )

    synthesis = _ai_generate(synthesis_prompt, "agent", "anthropic", ai_timeout)

    if verbose:
        print(synthesis)

    # ── Write outputs ────────────────────────────────────────────────────

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^\w]+", "_", topic.lower().strip())[:60].strip("_")
    out_dir = ROOT / "data" / "ideator" / "sessions"
    out_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = out_dir / f"{slug}_{ts}_transcript.md"
    transcript_md = (
        f"# Ideator Transcript\n\n"
        f"**Topic:** {topic}\n\n"
        f"**Context:**\n{context}\n\n"
        f"**Participants:** {', '.join(f'{p.name} ({p.role})' for p in personas)}\n\n"
        f"**Rounds:** {rounds}\n\n"
        f"---\n"
        f"{transcript.to_markdown()}"
    )
    transcript_path.write_text(transcript_md, encoding="utf-8")

    synthesis_path = out_dir / f"{slug}_{ts}_synthesis.md"
    synthesis_md = (
        f"# Ideator Synthesis\n\n"
        f"**Topic:** {topic}\n\n"
        f"---\n\n"
        f"{synthesis}\n"
    )
    synthesis_path.write_text(synthesis_md, encoding="utf-8")

    config_copy_path = out_dir / f"{slug}_{ts}_config.yaml"
    shutil.copy2(config_path, config_copy_path)

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"  OUTPUT")
        print(f"{'=' * 60}")
        print(f"  Transcript: {transcript_path}")
        print(f"  Synthesis:  {synthesis_path}")
        print(f"  Config:     {config_copy_path}")

    return synthesis_path


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ideator — multi-persona AI deliberation engine",
    )
    parser.add_argument(
        "config",
        help="Path to ideation config YAML (e.g. data/ideator/competitive_positioning.yaml)",
    )
    parser.add_argument("--rounds", type=int, default=None, help="Override number of rounds")
    parser.add_argument("--max-workers", type=int, default=4, help="Max concurrent AI calls per wave")
    parser.add_argument("--ai-timeout", type=float, default=180.0, help="Per-call AI timeout in seconds")
    parser.add_argument("--skill-timeout", type=float, default=60.0, help="Per-call skill timeout in seconds")
    parser.add_argument("--quiet", action="store_true", help="Suppress live output")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (ROOT / config_path).resolve()
    if not config_path.is_file():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    try:
        run_ideation(
            config_path,
            rounds_override=args.rounds,
            max_workers=args.max_workers,
            ai_timeout=args.ai_timeout,
            skill_timeout=args.skill_timeout,
            verbose=not args.quiet,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Ideation failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
