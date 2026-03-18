"""
License: MIT
Description: Math skill: sum/add, multiply, divide, subtract, power, root, modulo,
logarithms, factorial, percentage. Use for calculations and totals.

Input: POST body per route (values list, or a/b for binary ops).
Requires: none (pure computation).
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from pydantic import field_validator

from common.skill_response import skill_result

router = APIRouter()


class ValuesRequest(BaseModel):
    """Accepts body with 'values' or 'numbers' (list or comma-separated string)."""
    values: list[float] | str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _accept_values_or_numbers(cls, data: Any) -> Any:
        if isinstance(data, dict) and "values" not in data and "numbers" in data:
            data = {"values": data["numbers"]}
        return data

    @field_validator("values")
    @classmethod
    def _coerce_values(cls, v: Any) -> list[float]:
        if isinstance(v, list):
            return [float(x) for x in v]
        if isinstance(v, str):
            nums = []
            for p in v.replace(",", " ").split():
                p = p.strip()
                if p:
                    nums.append(float(p))
            return nums
        raise TypeError("values must be a list of numbers or comma-separated string")


class TwoNumbersRequest(BaseModel):
    """Binary operation: a and b. Accepts a,b or first,second."""
    a: float = Field(..., description="First operand")
    b: float = Field(..., description="Second operand")

    @model_validator(mode="before")
    @classmethod
    def _accept_first_second(cls, data: Any) -> Any:
        if isinstance(data, dict) and "a" not in data and "first" in data:
            data = {**data, "a": data["first"]}
        if isinstance(data, dict) and "b" not in data and "second" in data:
            data = {**data, "b": data["second"]}
        return data


class PowerRequest(BaseModel):
    base: float = Field(..., description="Base")
    exponent: float = Field(..., description="Exponent")


class RootRequest(BaseModel):
    value: float = Field(..., description="Value to take root of")
    n: float = Field(2, description="Root degree (2 = square root)")


class LogRequest(BaseModel):
    value: float = Field(..., gt=0, description="Value (must be > 0)")
    base: float | None = Field(10.0, description="Log base (default 10; use null for natural log)")


class FactorialRequest(BaseModel):
    n: int = Field(..., ge=0, le=1000, description="Non-negative integer (max 1000)")


class PercentOfRequest(BaseModel):
    percent: float = Field(..., description="Percentage (e.g. 20 for 20%)")
    value: float = Field(..., description="Value to take percentage of")


class WhatPercentRequest(BaseModel):
    x: float = Field(..., description="Part")
    of_y: float = Field(..., description="Whole (must be non-zero)")


class SingleValueRequest(BaseModel):
    value: float = Field(..., description="Single numeric value")


# ─── Sum / Add ─────────────────────────────────────────────────────────────

@router.post("/sum")
def sum_values(req: ValuesRequest) -> dict[str, Any]:
    """Sum of numbers. Body: values or numbers (list). Use when user asks for total, sum, or add."""
    vals = req.values
    total = sum(vals)
    return skill_result(summary=f"**Sum:** {total}", sum=total, count=len(vals))


@router.post("/add")
def add(req: ValuesRequest) -> dict[str, Any]:
    """Same as sum. Body: values or numbers (list). Use when user asks to add numbers."""
    vals = req.values
    total = sum(vals)
    return skill_result(summary=f"**Add:** {total}", sum=total, count=len(vals))


# ─── Multiply ───────────────────────────────────────────────────────────────

@router.post("/multiply")
def multiply(req: ValuesRequest) -> dict[str, Any]:
    """Product of numbers. Body: values or numbers (list). Use when user asks for product or multiply."""
    vals = req.values
    if not vals:
        raise HTTPException(status_code=400, detail="values must contain at least one number")
    product = math.prod(vals)
    return skill_result(summary=f"**Product:** {product}", product=product, count=len(vals))


# ─── Divide ────────────────────────────────────────────────────────────────

@router.post("/divide")
def divide(req: TwoNumbersRequest) -> dict[str, Any]:
    """Divide a by b. Body: a (dividend), b (divisor). Use when user asks to divide."""
    a, b = req.a, req.b
    if b == 0:
        raise HTTPException(status_code=400, detail="division by zero")
    q = a / b
    return skill_result(summary=f"**Divide:** {a} / {b} = {q}", quotient=q, a=a, b=b)


# ─── Subtract ──────────────────────────────────────────────────────────────

@router.post("/subtract")
def subtract(req: TwoNumbersRequest) -> dict[str, Any]:
    """Subtract b from a. Body: a (minuend), b (subtrahend). Use when user asks to subtract."""
    a, b = req.a, req.b
    diff = a - b
    return skill_result(summary=f"**Subtract:** {a} − {b} = {diff}", difference=diff, a=a, b=b)


# ─── Power ────────────────────────────────────────────────────────────────

@router.post("/power")
def power(req: PowerRequest) -> dict[str, Any]:
    """Exponentiation: base^exponent. Body: base, exponent. Use when user asks for power or exponent."""
    base, exp = req.base, req.exponent
    result = math.pow(base, exp)
    return skill_result(summary=f"**Power:** {base}^{exp} = {result}", result=result, base=base, exponent=exp)


# ─── Root ──────────────────────────────────────────────────────────────────

@router.post("/root")
def root(req: RootRequest) -> dict[str, Any]:
    """Nth root of value. Body: value, n (default 2 = square root). Use when user asks for root or square root."""
    value, n = req.value, req.n
    if n == 0:
        raise HTTPException(status_code=400, detail="root degree n must be non-zero")
    if value < 0 and (n != int(n) or int(n) % 2 == 0):
        raise HTTPException(status_code=400, detail="negative value requires odd integer root")
    result = value ** (1 / n)
    return skill_result(summary=f"**Root:** {value}^(1/{n}) = {result}", result=result, value=value, n=n)


@router.post("/sqrt")
def sqrt(req: SingleValueRequest) -> dict[str, Any]:
    """Square root. Body: value. Use for square root."""
    value = req.value
    if value < 0:
        raise HTTPException(status_code=400, detail="square root of negative number")
    result = math.sqrt(value)
    return skill_result(summary=f"**Square root:** √{value} = {result}", result=result, value=value)


# ─── Modulo ────────────────────────────────────────────────────────────────

@router.post("/modulo")
def modulo(req: TwoNumbersRequest) -> dict[str, Any]:
    """Modulo: a mod b (remainder of a divided by b). Body: a, b. Use when user asks for remainder or mod."""
    a, b = req.a, req.b
    if b == 0:
        raise HTTPException(status_code=400, detail="modulo by zero")
    result = a % b
    return skill_result(summary=f"**Modulo:** {a} mod {b} = {result}", result=result, a=a, b=b)


# ─── Logarithms ────────────────────────────────────────────────────────────

@router.post("/log")
def log(req: LogRequest) -> dict[str, Any]:
    """Logarithm. Body: value (> 0), base (optional; default 10). Use when user asks for log."""
    value = req.value
    base = req.base
    if base is None or base == math.e:
        result = math.log(value)
        label = "ln"
    else:
        if base <= 0 or base == 1:
            raise HTTPException(status_code=400, detail="log base must be positive and not 1")
        result = math.log(value, base)
        label = f"log_{base}"
    return skill_result(summary=f"**{label}({value})** = {result}", result=result, value=value, base=base)


@router.post("/ln")
def ln(req: SingleValueRequest) -> dict[str, Any]:
    """Natural logarithm (base e). Body: value (> 0). Use when user asks for natural log or ln."""
    value = req.value
    if value <= 0:
        raise HTTPException(status_code=400, detail="ln requires positive value")
    result = math.log(value)
    return skill_result(summary=f"**ln({value})** = {result}", result=result, value=value)


# ─── Factorial ─────────────────────────────────────────────────────────────

@router.post("/factorial")
def factorial(req: FactorialRequest) -> dict[str, Any]:
    """Factorial of n (n!). Body: n (non-negative integer, max 1000). Use when user asks for factorial."""
    n = req.n
    result = math.factorial(n)
    return skill_result(summary=f"**Factorial:** {n}! = {result}", result=result, n=n)


# ─── Percentage ────────────────────────────────────────────────────────────

@router.post("/percent_of")
def percent_of(req: PercentOfRequest) -> dict[str, Any]:
    """What is percent% of value? Body: percent, value. E.g. 20% of 80 = 16."""
    p, v = req.percent, req.value
    result = (p / 100.0) * v
    return skill_result(summary=f"**{p}% of {v}** = {result}", result=result, percent=p, value=v)


@router.post("/what_percent")
def what_percent(req: WhatPercentRequest) -> dict[str, Any]:
    """x is what percent of of_y? Body: x, of_y. E.g. 16 is 20% of 80."""
    x, of_y = req.x, req.of_y
    if of_y == 0:
        raise HTTPException(status_code=400, detail="of_y must be non-zero")
    result = (x / of_y) * 100.0
    return skill_result(summary=f"**{x} is {result}% of {of_y}**", result=result, x=x, of_y=of_y)


def get_router() -> APIRouter:
    return router
