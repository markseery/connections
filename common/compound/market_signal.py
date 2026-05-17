"""Discrete trading signal labels used by market indicator results."""

from __future__ import annotations

from enum import Enum


class Signal(str, Enum):
    """Bullish / bearish / neutral classification for indicator outputs."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
