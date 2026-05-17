"""Aggregate activity JSON by instrument and trans_code."""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def normalize_trans_code(tc: str) -> str:
    s = (tc or "").strip()
    if not s:
        return "(no trans_code)"
    u = s.upper()
    if u in ("CDIV", "MDIV"):
        return "DIV"
    return s


def read_json(path: str) -> dict[str, Any]:
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


class ActivityAggregator:
    """Roll up transactions into frequency, amounts, quantities, and percentages."""

    def aggregate(self, data: dict[str, Any]) -> dict[str, Any]:
        txs = data.get("transactions")
        if not isinstance(txs, list):
            txs = []

        by_instrument_amt: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        by_instrument_qty: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        by_instrument_freq: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        by_trans_code: dict[str, float] = defaultdict(float)
        by_trans_code_qty: dict[str, float] = defaultdict(float)
        total = 0.0
        total_frequency = 0

        for t in txs:
            if not isinstance(t, dict):
                continue
            inst = (t.get("instrument") or "").strip() or "(no instrument)"
            tc = normalize_trans_code(str(t.get("trans_code") or ""))
            amt = t.get("amount")
            if amt is None:
                continue
            try:
                v = float(amt)
            except (TypeError, ValueError):
                continue

            by_instrument_amt[inst][tc] += v
            by_trans_code[tc] += v
            total += v
            by_instrument_freq[inst][tc] += 1
            total_frequency += 1

            qty_raw = t.get("quantity")
            if qty_raw is not None:
                try:
                    qv = float(qty_raw)
                except (TypeError, ValueError):
                    qv = 0.0
                by_instrument_qty[inst][tc] += qv
                by_trans_code_qty[tc] += qv

        nested: dict[str, dict[str, dict[str, Any]]] = {}
        for inst, tc_map in sorted(by_instrument_amt.items()):
            out_tc: dict[str, dict[str, Any]] = {}
            for tc in sorted(tc_map.keys()):
                amt = tc_map[tc]
                global_amt = by_trans_code.get(tc, 0.0)
                pct_amt = round(100.0 * amt / global_amt, 4) if global_amt > 0 else 0.0

                qty = by_instrument_qty.get(inst, {}).get(tc, 0.0)
                global_qty = by_trans_code_qty.get(tc, 0.0)
                pct_qty = round(100.0 * qty / global_qty, 4) if global_qty > 0 else 0.0

                freq = int(by_instrument_freq.get(inst, {}).get(tc, 0))

                out_tc[tc] = {
                    "frequency": freq,
                    "amount": amt,
                    "pct_of_trans_code": pct_amt,
                    "quantity": qty,
                    "pct_quantity_of_trans_code": pct_qty,
                }
            nested[inst] = out_tc

        total_quantity = sum(by_trans_code_qty.values())
        return {
            "statement_id": data.get("statement_id"),
            "by_instrument": nested,
            "by_trans_code": dict(sorted(by_trans_code.items())),
            "by_trans_code_quantity": dict(sorted(by_trans_code_qty.items())),
            "total": total,
            "total_quantity": total_quantity,
            "total_frequency": total_frequency,
        }

    def aggregate_file(self, path: str | Path) -> dict[str, Any]:
        return self.aggregate(read_json(str(path)))

    @staticmethod
    def write_csv(path: Path, agg: dict[str, Any]) -> None:
        fieldnames = [
            "instrument",
            "trans_code",
            "frequency",
            "amount",
            "pct_of_trans_code",
            "quantity",
            "pct_quantity_of_trans_code",
        ]
        by_inst = agg.get("by_instrument") or {}
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for inst in sorted(by_inst.keys()):
                for tc in sorted(by_inst[inst].keys()):
                    rec = by_inst[inst][tc]
                    w.writerow(
                        {
                            "instrument": inst,
                            "trans_code": tc,
                            "frequency": rec["frequency"],
                            "amount": rec["amount"],
                            "pct_of_trans_code": rec["pct_of_trans_code"],
                            "quantity": rec["quantity"],
                            "pct_quantity_of_trans_code": rec["pct_quantity_of_trans_code"],
                        }
                    )
            w.writerow(
                {
                    "instrument": "_TOTAL",
                    "trans_code": "",
                    "frequency": agg.get("total_frequency", 0),
                    "amount": agg.get("total"),
                    "pct_of_trans_code": "",
                    "quantity": agg.get("total_quantity", 0.0),
                    "pct_quantity_of_trans_code": "",
                }
            )

    @staticmethod
    def dumps(agg: dict[str, Any], *, indent: int | None = 2) -> str:
        return json.dumps(agg, indent=indent, ensure_ascii=False)


__all__ = ["ActivityAggregator", "normalize_trans_code", "read_json"]
