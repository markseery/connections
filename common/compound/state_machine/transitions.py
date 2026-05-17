from __future__ import annotations

import json
from typing import Any


def _get_field(raw: dict[str, Any], field: str) -> Any:
    if not field:
        return None
    cur: Any = raw
    for part in field.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def compute_state_id(
    raw: dict[str, Any],
    *,
    compare: dict[str, Any],
    initial_state_id: str,
) -> str:
    template = compare.get("state_id_template")
    if isinstance(template, str) and template.strip():
        out = template
        for key, val in raw.items():
            out = out.replace("{" + key + "}", str(val if val is not None else ""))
        return out.strip() or initial_state_id
    buckets = compare.get("states")
    if isinstance(buckets, list):
        field = str(compare.get("field") or "")
        val = _get_field(raw, field) if field else None
        try:
            num = float(val) if val is not None else None
        except (TypeError, ValueError):
            num = None
        if num is not None:
            for b in buckets:
                if not isinstance(b, dict):
                    continue
                bid = str(b.get("id") or "")
                when = b.get("when")
                if not isinstance(when, dict) or not bid:
                    continue
                ok = True
                if "lt" in when and not (num < float(when["lt"])):
                    ok = False
                if "lte" in when and not (num <= float(when["lte"])):
                    ok = False
                if "gte" in when and not (num >= float(when["gte"])):
                    ok = False
                if "gt" in when and not (num > float(when["gt"])):
                    ok = False
                if ok:
                    return bid
    field = str(compare.get("field") or "")
    if field:
        v = _get_field(raw, field)
        if v is not None:
            return str(v)
    return initial_state_id


def state_changed(
    old_raw: dict[str, Any] | None,
    new_raw: dict[str, Any],
    old_state_id: str | None,
    new_state_id: str,
    compare: dict[str, Any],
) -> bool:
    mode = str(compare.get("mode") or "numeric_delta")
    if old_raw is None and old_state_id in (None, "", "unknown"):
        return new_state_id not in ("", "unknown")

    if mode == "composite_delta":
        fields = compare.get("fields")
        if not isinstance(fields, list) or not fields:
            return old_state_id != new_state_id
        if old_raw is None:
            return True
        for f in fields:
            if _get_field(old_raw, str(f)) != _get_field(new_raw, str(f)):
                return True
        return False

    if mode == "string_eq":
        field = str(compare.get("field") or "")
        if old_raw is None:
            return True
        return _get_field(old_raw, field) != _get_field(new_raw, field)

    if mode == "bucket_only" or mode == "state_id_only":
        return old_state_id != new_state_id

    # numeric_delta
    field = str(compare.get("field") or "")
    if not field:
        return old_state_id != new_state_id
    try:
        old_v = float(_get_field(old_raw or {}, field) or 0)
        new_v = float(_get_field(new_raw, field) or 0)
    except (TypeError, ValueError):
        return old_state_id != new_state_id

    min_abs = float(compare.get("min_absolute") or 0)
    min_rel = float(compare.get("min_relative") or 0)
    delta = abs(new_v - old_v)
    if delta == 0:
        return old_state_id != new_state_id
    if min_abs > 0 and delta < min_abs:
        return False
    if min_rel > 0:
        base = abs(old_v) if old_v != 0 else abs(new_v)
        if base > 0 and (delta / base) < min_rel:
            return False
    return True


def raw_json_for_template(raw: dict[str, Any]) -> str:
    return json.dumps(raw, indent=2, sort_keys=True, default=str)
