from __future__ import annotations

from typing import Any

from .distribution_latest import pull_distribution_latest
from .stockanalysis_aum import pull_stockanalysis_aum

_ADAPTERS: dict[str, Any] = {
    "stockanalysis_aum": pull_stockanalysis_aum,
    "distribution_latest": pull_distribution_latest,
}


def pull_source(adapter: str, entity: dict[str, str], params: dict[str, Any], *, timeout_sec: float) -> dict[str, Any]:
    fn = _ADAPTERS.get(adapter)
    if fn is None:
        raise ValueError(f"unknown state source adapter: {adapter}")
    return fn(entity, params, timeout_sec=timeout_sec)
