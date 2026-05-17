"""Compute technical indicator snapshots from price history."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List

from common.compound.market_indicators import (
    EMA_PERIODS,
    SMA_PERIODS,
    BollingerBands,
    ExponentialMovingAverage,
    MACD,
    RSI,
    SimpleMovingAverage,
)
from common.compound.market_signal import Signal


def _dc(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Signal):
        return obj.value
    if is_dataclass(obj):
        return _dc(asdict(obj))
    if isinstance(obj, dict):
        return {k: _dc(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dc(v) for v in obj]
    return obj


def compute_indicators_snapshot(
    symbol: str,
    history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    sma_out: Dict[str, Any] = {}
    for p in SMA_PERIODS:
        sma_out[str(p)] = _dc(SimpleMovingAverage(p).compute(history))

    ema_out: Dict[str, Any] = {}
    for p in EMA_PERIODS:
        ema_out[str(p)] = _dc(ExponentialMovingAverage(p).compute(history))

    return {
        "symbol": symbol,
        "bars": len(history),
        "sma": sma_out,
        "ema": ema_out,
        "rsi14": _dc(RSI(14).compute(history)),
        "macd": _dc(MACD().compute(history)),
        "bollinger": _dc(BollingerBands(20, 2.0).compute(history)),
    }
