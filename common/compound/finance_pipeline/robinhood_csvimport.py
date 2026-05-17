"""Import Robinhood portfolio activity CSV rows into per-record JSON files."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .activity_parser import parse_money, parse_quantity_or_price, parse_us_date

_HEADER_MAP: dict[str, str] = {
    "activity date": "activity_date",
    "process date": "process_date",
    "settle date": "settle_date",
    "instrument": "instrument",
    "description": "description",
    "trans code": "trans_code",
    "quantity": "quantity",
    "price": "price",
    "amount": "amount",
}


def _normalize_header(header: str) -> str:
    return " ".join((header or "").strip().lower().split())


def _cell_text(value: Any) -> str:
    """Normalize CSV cell values (including DictReader extra-field lists)."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(v) for v in value if v is not None).strip()
    return str(value).strip()


@dataclass
class ImportSummary:
    account_name: str
    input_path: Path
    output_dir: Path
    added: int
    skipped: int
    total_rows: int


class RobinhoodCsvImport:
    """Read Robinhood CSV and persist each row as a JSON record."""

    def __init__(
        self,
        account_name: str,
        input_file_name: str,
        portfolio_dir: Path | None = None,
    ) -> None:
        self.account_name = account_name.strip()
        if not self.account_name:
            raise ValueError("account_name must not be empty")

        root = portfolio_dir or Path("application_files/data/portfolio")
        self.portfolio_dir = root.resolve()
        self.input_path = (self.portfolio_dir / input_file_name).resolve()
        self.output_dir = (self.portfolio_dir / "robinhood" / self.account_name).resolve()

    def _canonical_record(self, row: dict[str, str], row_number: int) -> dict[str, Any]:
        mapped: dict[str, Any] = {}
        for header, value in row.items():
            key = _HEADER_MAP.get(_normalize_header(header))
            if not key:
                continue
            mapped[key] = _cell_text(value)

        record: dict[str, Any] = {
            "account_name": self.account_name,
            "source_file": self.input_path.name,
            "row_number": row_number,
            "activity_date": parse_us_date(mapped.get("activity_date", "")),
            "process_date": parse_us_date(mapped.get("process_date", "")),
            "settle_date": parse_us_date(mapped.get("settle_date", "")),
            "instrument": mapped.get("instrument") or None,
            "description": mapped.get("description") or None,
            "trans_code": mapped.get("trans_code") or None,
            "quantity": parse_quantity_or_price(mapped.get("quantity", "")),
            "price": parse_money(mapped.get("price", "")),
            "amount": parse_money(mapped.get("amount", "")),
        }
        return record

    @staticmethod
    def _is_meaningful_record(record: dict[str, Any]) -> bool:
        """Keep only rows that look like actual transaction entries."""
        return any(
            record.get(key) is not None
            for key in (
                "activity_date",
                "process_date",
                "settle_date",
                "instrument",
                "description",
                "trans_code",
                "quantity",
                "price",
                "amount",
            )
        )

    @staticmethod
    def _record_id(record: dict[str, Any]) -> str:
        dedupe_basis = {
            "account_name": record.get("account_name"),
            "activity_date": record.get("activity_date"),
            "process_date": record.get("process_date"),
            "settle_date": record.get("settle_date"),
            "instrument": record.get("instrument"),
            "description": record.get("description"),
            "trans_code": record.get("trans_code"),
            "quantity": record.get("quantity"),
            "price": record.get("price"),
            "amount": record.get("amount"),
        }
        normalized = json.dumps(
            dedupe_basis,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _validate_headers(self, fieldnames: list[str]) -> None:
        normalized = {_normalize_header(h) for h in fieldnames}
        required = set(_HEADER_MAP.keys())
        missing = sorted(required - normalized)
        if missing:
            raise ValueError(
                "Input file does not match expected Robinhood activity format. "
                f"Missing headers: {missing}"
            )

    def import_records(self) -> ImportSummary:
        if not self.input_path.is_file():
            raise FileNotFoundError(f"Input file not found: {self.input_path}")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        added = 0
        skipped = 0
        total_rows = 0

        with self.input_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError(f"No headers found in input file: {self.input_path}")
            self._validate_headers(reader.fieldnames)

            for row_number, row in enumerate(reader, start=2):
                if not row or all(not _cell_text(v) for v in row.values()):
                    continue

                total_rows += 1
                record = self._canonical_record(row, row_number)
                if not self._is_meaningful_record(record):
                    continue
                record_id = self._record_id(record)
                record["record_id"] = record_id

                out_path = self.output_dir / f"{record_id}.json"
                if out_path.exists():
                    skipped += 1
                    print(f"Skipping record {record_id}: already exists")
                    continue

                out_path.write_text(
                    json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                added += 1
                print(f"Added record {record_id}")

        return ImportSummary(
            account_name=self.account_name,
            input_path=self.input_path,
            output_dir=self.output_dir,
            added=added,
            skipped=skipped,
            total_rows=total_rows,
        )


__all__ = ["ImportSummary", "RobinhoodCsvImport"]
