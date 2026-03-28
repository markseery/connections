"""
License: MIT
Description: Simple statistics skill: mean/average, median, stddev.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from pydantic import field_validator

from common.simple.skill_response import skill_result


router = APIRouter()


class ValuesRequest(BaseModel):
    """Accepts body with 'values' or 'numbers' (list or comma-separated string)."""
    values: list[float] | str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _accept_values_or_numbers(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Planner/LLM often sends "numbers"; accept either key
            if "values" not in data and "numbers" in data:
                data = {"values": data["numbers"]}
        return data

    @field_validator("values")
    @classmethod
    def _coerce_values(cls, v: Any) -> list[float]:
        if isinstance(v, list):
            return [float(x) for x in v]
        if isinstance(v, str):
            parts = [p.strip() for p in v.split(",")]
            nums: list[float] = []
            for p in parts:
                if not p:
                    continue
                nums.append(float(p))
            return nums
        raise TypeError("values must be a list of numbers or a comma-separated string")


def _clean(values: list[float]) -> list[float]:
    out: list[float] = []
    for v in values:
        if v is None or isinstance(v, bool):
            continue
        try:
            fv = float(v)
        except Exception as exc:
            print(f"[statistics] skipping non-numeric value {v!r}: {exc}", flush=True)
            continue
        if math.isnan(fv) or math.isinf(fv):
            continue
        out.append(fv)
    if not out:
        raise HTTPException(status_code=400, detail="values must contain at least one finite number")
    return out


@router.post("/mean")
def mean(req: ValuesRequest) -> dict[str, Any]:
    """Arithmetic mean of numbers. Body: values (list of numbers). Use when user asks for average or mean."""
    values = _clean(req.values)
    v = statistics.fmean(values)
    return skill_result(summary=f"**Mean:** {v}", mean=v)


@router.post("/average")
def average(req: ValuesRequest) -> dict[str, Any]:
    """Same as mean. Body: values (list of numbers). Use when user asks for average."""
    values = _clean(req.values)
    v = statistics.fmean(values)
    return skill_result(summary=f"**Average:** {v}", average=v)


@router.post("/median")
def median(req: ValuesRequest) -> dict[str, Any]:
    """Median of numbers. Body: values (list of numbers). Use when user asks for median."""
    values = _clean(req.values)
    v = statistics.median(values)
    return skill_result(summary=f"**Median:** {v}", median=v)


@router.post("/stddev")
def stddev(req: ValuesRequest) -> dict[str, Any]:
    """Standard deviation of numbers. Body: values (list of numbers). Use when user asks for std dev or spread."""
    values = _clean(req.values)
    v = statistics.pstdev(values)
    return skill_result(summary=f"**Standard deviation:** {v}", stddev=v)


def get_router() -> APIRouter:
    return router

