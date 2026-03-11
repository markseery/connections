"""
License: MIT
Description: Simple statistics skill: mean/average, median, stddev.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pydantic import field_validator


router = APIRouter()


class ValuesRequest(BaseModel):
    values: list[float] | str = Field(min_length=1)

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
        except Exception:
            continue
        if math.isnan(fv) or math.isinf(fv):
            continue
        out.append(fv)
    if not out:
        raise HTTPException(status_code=400, detail="values must contain at least one finite number")
    return out


@router.post("/mean")
def mean(req: ValuesRequest) -> dict[str, Any]:
    values = _clean(req.values)
    return {"mean": statistics.fmean(values)}


@router.post("/average")
def average(req: ValuesRequest) -> dict[str, Any]:
    values = _clean(req.values)
    return {"average": statistics.fmean(values)}


@router.post("/median")
def median(req: ValuesRequest) -> dict[str, Any]:
    values = _clean(req.values)
    return {"median": statistics.median(values)}


@router.post("/stddev")
def stddev(req: ValuesRequest) -> dict[str, Any]:
    """
    Population standard deviation (pstdev), so it works for n=1 (returns 0.0).
    """
    values = _clean(req.values)
    return {"stddev": statistics.pstdev(values)}


def get_router() -> APIRouter:
    return router

