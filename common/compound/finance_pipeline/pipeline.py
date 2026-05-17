"""End-to-end: optional Ollama sidecar → parse activity CSV → aggregate → JSON + CSV."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .activity_parser import ActivityParserConfig, ActivityTableParser
from .aggregator import ActivityAggregator
from .csv_ollama import CsvOllamaClient, CsvOllamaConfig


@dataclass
class PipelineConfig:
    """Configure :class:`ActivityPipeline`."""

    delimiter: str = "auto"
    json_indent: int = 2
    write_activity_json: bool = True
    write_totals_json: bool = True
    write_totals_csv: bool = True
    # If set, write ``<stem>.*`` artifacts here; if ``None``, write next to the input CSV.
    output_dir: Path | None = None
    # Optional: run Ollama on the input CSV and save response (does not feed the parser).
    ollama_prompt: str | None = None
    ollama: CsvOllamaConfig = field(default_factory=CsvOllamaConfig)


@dataclass
class PipelineResult:
    """Paths and in-memory results from :meth:`ActivityPipeline.run`."""

    input_path: Path
    activity: dict[str, Any]
    aggregate: dict[str, Any]
    activity_json_path: Path | None = None
    totals_json_path: Path | None = None
    totals_csv_path: Path | None = None
    ollama_text_path: Path | None = None


class ActivityPipeline:
    """Chain optional LLM sidecar, activity parse, and aggregation."""

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self.config = config or PipelineConfig()
        parser_cfg = ActivityParserConfig(delimiter=self.config.delimiter)
        self._parser = ActivityTableParser(parser_cfg)
        self._aggregator = ActivityAggregator()

    def run(self, input_csv: str | Path) -> PipelineResult:
        path = Path(input_csv).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Not a file: {path}")

        stem = path.stem
        out_parent = path.parent if self.config.output_dir is None else self.config.output_dir
        out_parent.mkdir(parents=True, exist_ok=True)

        ollama_path: Path | None = None
        if self.config.ollama_prompt:
            client = CsvOllamaClient(self.config.ollama)
            ollama_text = client.generate([path], self.config.ollama_prompt)
            ollama_path = out_parent / f"{stem}.ollama.txt"
            ollama_path.write_text(ollama_text, encoding="utf-8")

        activity = self._parser.parse_file(path)
        agg = self._aggregator.aggregate(activity)

        indent = None if self.config.json_indent == 0 else self.config.json_indent

        activity_json_path: Path | None = None
        if self.config.write_activity_json:
            activity_json_path = out_parent / f"{stem}.activity.json"
            activity_json_path.write_text(
                json.dumps(activity, indent=indent, ensure_ascii=False),
                encoding="utf-8",
            )

        totals_json_path: Path | None = None
        totals_csv_path: Path | None = None
        if self.config.write_totals_json:
            totals_json_path = out_parent / f"{stem}.totals.json"
            totals_json_path.write_text(
                self._aggregator.dumps(agg, indent=indent),
                encoding="utf-8",
            )
        if self.config.write_totals_csv:
            totals_csv_path = out_parent / f"{stem}.totals.csv"
            ActivityAggregator.write_csv(totals_csv_path, agg)

        return PipelineResult(
            input_path=path,
            activity=activity,
            aggregate=agg,
            activity_json_path=activity_json_path,
            totals_json_path=totals_json_path,
            totals_csv_path=totals_csv_path,
            ollama_text_path=ollama_path,
        )


def main() -> int:
    import argparse
    import sys

    from common.simple import script_env

    script_env.ensure_sys_path()

    ap = argparse.ArgumentParser(
        description=(
            "Run full pipeline: optional Ollama on CSV → parse activity → aggregate "
            "→ .activity.json, .totals.json, .totals.csv"
        ),
    )
    ap.add_argument(
        "input_csv",
        type=Path,
        help="Broker activity CSV/TSV (required columns: Activity Date, … Amount)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory for generated files (default: application_files/data/activity_pipeline). "
            "Relative paths are from the repo root."
        ),
    )
    ap.add_argument(
        "-d",
        "--delimiter",
        choices=("auto", "tab", "comma"),
        default="auto",
        help="Field delimiter for the activity table (default: auto)",
    )
    ap.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent (0 = compact)",
    )
    ap.add_argument(
        "--no-activity-json",
        action="store_true",
        help="Skip writing <stem>.activity.json",
    )
    ap.add_argument(
        "--no-totals-json",
        action="store_true",
        help="Skip writing <stem>.totals.json",
    )
    ap.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip writing <stem>.totals.csv",
    )
    ap.add_argument(
        "--ollama-prompt",
        default=None,
        help=(
            "If set, call Ollama with this prompt and the CSV as context; "
            "save response to <stem>.ollama.txt under --out-dir (does not replace the parse input)."
        ),
    )
    ap.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434",
        help="Ollama base URL",
    )
    ap.add_argument(
        "--ollama-model",
        default="gemma4:e2b",
        help="Ollama model tag",
    )
    args = ap.parse_args()

    out_dir = script_env.resolve_output_dir(args.out_dir, segment="activity_pipeline")
    ollama_cfg = CsvOllamaConfig(base_url=args.ollama_url, model=args.ollama_model)
    cfg = PipelineConfig(
        delimiter=args.delimiter,
        json_indent=args.indent,
        write_activity_json=not args.no_activity_json,
        write_totals_json=not args.no_totals_json,
        write_totals_csv=not args.no_csv,
        output_dir=out_dir,
        ollama_prompt=args.ollama_prompt,
        ollama=ollama_cfg,
    )

    try:
        pl = ActivityPipeline(cfg)
        res = pl.run(args.input_csv)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Input: {res.input_path}")
    if res.ollama_text_path:
        print(f"Ollama: {res.ollama_text_path}")
    if res.activity_json_path:
        print(f"Activity JSON: {res.activity_json_path}")
    if res.totals_json_path:
        print(f"Totals JSON: {res.totals_json_path}")
    if res.totals_csv_path:
        print(f"Totals CSV: {res.totals_csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
