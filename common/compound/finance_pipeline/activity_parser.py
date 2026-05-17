"""Parse broker-style activity CSV/TSV into structured transaction JSON."""

from __future__ import annotations

import csv
import io
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

UUID_LINE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s*$",
    re.I,
)

_DIV_SHARES_AT = re.compile(
    r"([\d,]+(?:\.\d+)?)\s+shares\s+at\s+([\d,]+(?:\.\d+)?)",
    re.I,
)


def _norm_header(s: str) -> str:
    return " ".join(s.strip().lower().split())


HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "activity_date": ("activity date",),
    "process_date": ("process date",),
    "settle_date": ("settle date",),
    "instrument": ("instrument", "symbol", "ticker"),
    "description": ("description",),
    "trans_code": ("trans code", "transaction code", "type"),
    "quantity": ("quantity", "qty"),
    "price": ("price",),
    "amount": ("amount", "total"),
}


def _match_columns(headers: list[str]) -> dict[str, int]:
    normalized = [_norm_header(h) for h in headers]
    out: dict[str, int] = {}
    for key, aliases in HEADER_ALIASES.items():
        for i, nh in enumerate(normalized):
            if nh in aliases:
                out[key] = i
                break
        if key not in out:
            raise ValueError(
                f"Missing column for {key!r}. Found headers: {headers!r}"
            )
    return out


def parse_us_date(s: str) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def parse_money(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    neg = "(" in s and ")" in s
    s = s.replace("$", "").replace(",", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
        neg = True
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None


def parse_quantity_or_price(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    s = s.replace(",", "").replace("$", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def extract_div_quantity_price(description: str | None) -> tuple[float | None, float | None]:
    if not description:
        return None, None
    m = _DIV_SHARES_AT.search(description)
    if not m:
        return None, None
    try:
        q = float(m.group(1).replace(",", ""))
        p = float(m.group(2).replace(",", ""))
        return q, p
    except ValueError:
        return None, None


def read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8", errors="replace")


def detect_delimiter(sample: str, forced: str | None) -> str:
    if forced in ("\t", "tab"):
        return "\t"
    if forced in (",", "comma"):
        return ","
    first_line = sample.splitlines()[0] if sample.strip() else ""
    if first_line.count("\t") >= 3:
        return "\t"
    if first_line.count(",") >= 3:
        return ","
    return "\t"


@dataclass
class ActivityParserConfig:
    """Delimiter: ``None`` / ``\"auto\"`` to sniff; ``\"tab\"`` / ``\"comma\"`` to force."""

    delimiter: str | None = "auto"


class ActivityTableParser:
    """Parse activity table text or files into ``{statement_id, transactions}`` JSON."""

    def __init__(self, config: ActivityParserConfig | None = None) -> None:
        self.config = config or ActivityParserConfig()

    def parse(self, text: str, delimiter: str | None = None) -> dict[str, Any]:
        forced = delimiter
        if forced == "auto":
            forced = None
        lines = text.splitlines()
        if not lines:
            return {"statement_id": None, "transactions": []}

        idx = 0
        statement_id: str | None = None
        first = lines[0].strip()
        if UUID_LINE.match(first):
            statement_id = first.strip()
            idx = 1

        rest = "\n".join(lines[idx:])
        if not rest.strip():
            return {"statement_id": statement_id, "transactions": []}

        delim = detect_delimiter(rest, forced)
        reader = csv.reader(io.StringIO(rest), delimiter=delim)
        rows = list(reader)
        if not rows:
            return {"statement_id": statement_id, "transactions": []}

        headers = rows[0]
        col = _match_columns(headers)
        transactions: list[dict[str, Any]] = []

        for raw in rows[1:]:
            if not raw or all(not (c or "").strip() for c in raw):
                continue
            while len(raw) < len(headers):
                raw.append("")

            def cell(name: str) -> str:
                return raw[col[name]] if col[name] < len(raw) else ""

            desc = cell("description").strip()
            qty_s = cell("quantity")
            price_s = cell("price")
            amt_s = cell("amount")

            trans_code_raw = cell("trans_code").strip() or None
            row_obj: dict[str, Any] = {
                "activity_date": parse_us_date(cell("activity_date")),
                "process_date": parse_us_date(cell("process_date")),
                "settle_date": parse_us_date(cell("settle_date")),
                "instrument": cell("instrument").strip() or None,
                "description": desc or None,
                "trans_code": trans_code_raw,
                "quantity": parse_quantity_or_price(qty_s),
                "price": parse_money(price_s) if price_s.strip() else None,
                "amount": parse_money(amt_s),
            }

            tc = (trans_code_raw or "").strip().upper()
            if tc in ("CDIV", "MDIV"):
                dq, dp = extract_div_quantity_price(desc)
                if dq is not None and dp is not None:
                    row_obj["quantity"] = dq
                    row_obj["price"] = dp

            transactions.append(row_obj)

        return {"statement_id": statement_id, "transactions": transactions}

    def parse_file(self, path: str | Path, delimiter: str | None = None) -> dict[str, Any]:
        text = read_text(str(path))
        d = delimiter
        if d is None:
            cfg = self.config.delimiter
            if cfg in ("auto", None):
                d = None
            elif cfg in ("tab", "\t"):
                d = "tab"
            elif cfg in ("comma", ","):
                d = "comma"
        return self.parse(text, delimiter=d)


__all__ = [
    "ActivityParserConfig",
    "ActivityTableParser",
    "extract_div_quantity_price",
    "parse_money",
    "parse_us_date",
    "read_text",
]
