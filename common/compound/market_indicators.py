"""Technical indicators: SMA, EMA, RSI, MACD, Bollinger bands.

Price history is a list of dicts with keys ``Open``, ``High``, ``Low``, ``Close``, ``Volume``
(aliases ``open`` / ``close`` / ``volume`` accepted where noted).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from common.compound.market_signal import Signal

SMA_PERIODS: tuple[int, ...] = (9, 20, 21, 50, 100, 200)
EMA_PERIODS: tuple[int, ...] = (9, 20, 21, 50, 100, 200)


def _close(record: Dict[str, Any]) -> Optional[float]:
    v = record.get("Close") if record.get("Close") is not None else record.get("close")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _hlc_v(record: Dict[str, Any]) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    h = record.get("High") or record.get("high")
    l = record.get("Low") or record.get("low")
    c = _close(record)
    v = record.get("Volume") if record.get("Volume") is not None else record.get("volume")
    try:
        hf = float(h) if h is not None else None
        lf = float(l) if l is not None else None
        vf = float(v) if v is not None else None
    except (TypeError, ValueError):
        return None, None, None, None
    return hf, lf, float(c) if c is not None else None, vf


@dataclass
class MovingAverageResult:
    period: int
    kind: str  # "SMA" | "EMA"
    value: float
    signal: Signal
    current_price: float
    pct_from_average: float


class SimpleMovingAverage:
    """Simple moving average over ``period`` closes."""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise ValueError("period must be positive")
        self.period = period

    def compute(self, history: List[Dict[str, Any]]) -> Optional[MovingAverageResult]:
        if len(history) < self.period:
            return None
        closes: List[float] = []
        for record in history[-self.period :]:
            c = _close(record)
            if c is None:
                return None
            closes.append(c)
        sma = sum(closes) / len(closes)
        current = closes[-1]
        if current > sma * 1.02:
            sig = Signal.BULLISH
        elif current < sma * 0.98:
            sig = Signal.BEARISH
        else:
            sig = Signal.NEUTRAL
        pct = (current - sma) / sma * 100 if sma else 0.0
        return MovingAverageResult(
            period=self.period,
            kind="SMA",
            value=round(sma, 4),
            signal=sig,
            current_price=round(current, 4),
            pct_from_average=round(pct, 2),
        )


class ExponentialMovingAverage:
    """Exponential moving average over ``period`` closes."""

    def __init__(self, period: int, smoothing: float = 2.0) -> None:
        if period <= 0:
            raise ValueError("period must be positive")
        self.period = period
        self.smoothing = smoothing
        self._k = smoothing / (period + 1)

    def compute(self, history: List[Dict[str, Any]]) -> Optional[MovingAverageResult]:
        closes: List[float] = []
        for record in history:
            c = _close(record)
            if c is not None:
                closes.append(c)
        if len(closes) < self.period:
            return None
        sma = sum(closes[: self.period]) / self.period
        ema = sma
        for price in closes[self.period :]:
            ema = (price * self._k) + (ema * (1 - self._k))
        current = closes[-1]
        if current > ema * 1.02:
            sig = Signal.BULLISH
        elif current < ema * 0.98:
            sig = Signal.BEARISH
        else:
            sig = Signal.NEUTRAL
        pct = (current - ema) / ema * 100 if ema else 0.0
        return MovingAverageResult(
            period=self.period,
            kind="EMA",
            value=round(ema, 4),
            signal=sig,
            current_price=round(current, 4),
            pct_from_average=round(pct, 2),
        )


@dataclass
class RSIResult:
    period: int
    value: float
    signal: Signal
    description: str


class RSI:
    """Relative Strength Index (default 14-day Wilder-style smoothing)."""

    def __init__(self, period: int = 14) -> None:
        if period <= 0:
            raise ValueError("period must be positive")
        self.period = period

    def compute(self, history: List[Dict[str, Any]]) -> Optional[RSIResult]:
        closes: List[float] = []
        for record in history:
            c = _close(record)
            if c is not None:
                closes.append(c)
        if len(closes) < self.period + 1:
            return None
        gains: List[float] = []
        losses: List[float] = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(-diff)
        avg_gain = sum(gains[: self.period]) / self.period
        avg_loss = sum(losses[: self.period]) / self.period
        for i in range(self.period, len(gains)):
            avg_gain = (avg_gain * (self.period - 1) + gains[i]) / self.period
            avg_loss = (avg_loss * (self.period - 1) + losses[i]) / self.period
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        if rsi >= 70:
            sig = Signal.BEARISH
            desc = "overbought (>=70)"
        elif rsi <= 30:
            sig = Signal.BULLISH
            desc = "oversold (<=30)"
        else:
            sig = Signal.NEUTRAL
            desc = "neutral band"
        return RSIResult(period=self.period, value=round(rsi, 2), signal=sig, description=desc)


@dataclass
class MACDResult:
    macd_line: float
    signal_line: float
    histogram: float
    signal: Signal
    metadata: Dict[str, Any] = field(default_factory=dict)


class MACD:
    """MACD (12, 26, 9) with ``Signal`` from histogram vs zero / cross logic."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        if not (0 < fast < slow and signal > 0):
            raise ValueError("invalid MACD periods")
        self.fast = fast
        self.slow = slow
        self.signal_p = signal

    @staticmethod
    def _ema_series(values: List[float], period: int) -> List[float]:
        if len(values) < period:
            return []
        k = 2 / (period + 1)
        ema = sum(values[:period]) / period
        series = [ema]
        for v in values[period:]:
            ema = (v - ema) * k + ema
            series.append(ema)
        return series

    def compute(self, history: List[Dict[str, Any]]) -> Optional[MACDResult]:
        closes: List[float] = []
        for record in history:
            c = _close(record)
            if c is not None:
                closes.append(c)
        need = self.slow + self.signal_p
        if len(closes) < need:
            return None
        fast_s = self._ema_series(closes, self.fast)
        slow_s = self._ema_series(closes, self.slow)
        offset = self.slow - self.fast
        macd_line_s: List[float] = []
        for i in range(len(slow_s)):
            macd_line_s.append(fast_s[i + offset] - slow_s[i])
        if len(macd_line_s) < self.signal_p:
            return None
        signal_s = self._ema_series(macd_line_s, self.signal_p)
        macd_line = macd_line_s[-1]
        signal_line = signal_s[-1]
        histogram = macd_line - signal_line
        sig_type = Signal.NEUTRAL
        if len(signal_s) >= 2 and len(macd_line_s) >= 2:
            prev_hist = macd_line_s[-2] - signal_s[-2]
            curr_hist = histogram
            if curr_hist > 0 and prev_hist <= 0:
                sig_type = Signal.BULLISH
            elif curr_hist < 0 and prev_hist >= 0:
                sig_type = Signal.BEARISH
            elif curr_hist > 0:
                sig_type = Signal.BULLISH
            elif curr_hist < 0:
                sig_type = Signal.BEARISH
        return MACDResult(
            macd_line=round(macd_line, 4),
            signal_line=round(signal_line, 4),
            histogram=round(histogram, 4),
            signal=sig_type,
            metadata={"fast": self.fast, "slow": self.slow, "signal_period": self.signal_p},
        )


@dataclass
class BollingerBandsResult:
    period: int
    std_multiple: float
    upper: float
    middle: float
    lower: float
    last_close: float
    position_pct: Optional[float]
    signal: Signal


class BollingerBands:
    """Bollinger bands: middle = SMA(period), upper/lower = middle ± std_multiple * stdev."""

    def __init__(self, period: int = 20, std_multiple: float = 2.0) -> None:
        if period <= 0:
            raise ValueError("period must be positive")
        self.period = period
        self.std_multiple = std_multiple

    def compute(self, history: List[Dict[str, Any]]) -> Optional[BollingerBandsResult]:
        closes: List[float] = []
        for record in history[-self.period :]:
            c = _close(record)
            if c is None:
                return None
            closes.append(c)
        if len(closes) < self.period:
            return None
        last_close = closes[-1]
        middle = sum(closes) / self.period
        var = sum((x - middle) ** 2 for x in closes) / self.period
        std = math.sqrt(var)
        upper = middle + self.std_multiple * std
        lower = middle - self.std_multiple * std
        pos: Optional[float] = None
        if upper != lower:
            pos = ((last_close - lower) / (upper - lower)) * 100
        if pos is not None and pos >= 100:
            sig = Signal.BEARISH
        elif pos is not None and pos <= 0:
            sig = Signal.BULLISH
        else:
            sig = Signal.NEUTRAL
        return BollingerBandsResult(
            period=self.period,
            std_multiple=self.std_multiple,
            upper=round(upper, 4),
            middle=round(middle, 4),
            lower=round(lower, 4),
            last_close=round(last_close, 4),
            position_pct=round(pos, 2) if pos is not None else None,
            signal=sig,
        )
