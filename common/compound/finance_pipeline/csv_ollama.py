"""Load CSV as text context and call Ollama /api/generate."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import Request, urlopen


@dataclass
class CsvOllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    model: str = "gemma4:e2b"
    max_context_chars: int = 200_000
    timeout_sec: float = 600.0
    context_header: str = (
        "The following is tab-separated data exported from CSV file(s). "
        "Use it as context when answering.\n\n"
        "--- BEGIN CSV CONTEXT ---\n"
    )
    context_footer: str = (
        "\n--- END CSV CONTEXT ---\n\n"
        "User request:\n"
    )


class CsvOllamaClient:
    """Build prompts from one or more CSV files and return the model response string."""

    def __init__(self, config: CsvOllamaConfig | None = None) -> None:
        self.config = config or CsvOllamaConfig()

    def csv_file_to_text(self, path: Path, max_chars: int | None = None) -> str:
        """Render CSV as tab-separated lines."""
        rows: list[str] = []
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            for row in reader:
                rows.append("\t".join(cell.replace("\t", " ") for cell in row))
        text = "\n".join(rows)
        cap = max_chars if max_chars is not None else self.config.max_context_chars
        if len(text) > cap:
            text = text[:cap] + "\n\n[... truncated to max_context_chars ...]"
        return text

    def build_prompt(self, csv_paths: list[Path], user_prompt: str) -> str:
        blocks = [
            f"=== {p.resolve()} ===\n{self.csv_file_to_text(p, self.config.max_context_chars)}"
            for p in csv_paths
        ]
        context = "\n\n".join(blocks)
        return (
            self.config.context_header
            + context
            + self.config.context_footer
            + user_prompt.strip()
        )

    def generate(self, csv_paths: list[Path], user_prompt: str) -> str:
        """POST to Ollama and return response text."""
        prompt = self.build_prompt(csv_paths, user_prompt)
        return ollama_generate(
            self.config.base_url,
            self.config.model,
            prompt,
            timeout_sec=self.config.timeout_sec,
        )


def ollama_generate(
    base_url: str,
    model: str,
    prompt: str,
    *,
    timeout_sec: float = 600.0,
) -> str:
    """Low-level: POST /api/generate, return ``response`` field."""
    url = base_url.rstrip("/") + "/api/generate"
    payload = json.dumps(
        {"model": model, "prompt": prompt, "stream": False}
    ).encode("utf-8")
    req = Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if "response" in data:
        return str(data["response"])
    return raw


__all__ = ["CsvOllamaClient", "CsvOllamaConfig", "ollama_generate"]
