"""Runtime warning filters for yfinance/pandas compatibility noise."""

from __future__ import annotations

import warnings

_INSTALLED = False


def suppress_utcnow_deprecation_warning() -> None:
    """Silence known yfinance pandas Timestamp.utcnow deprecation warning."""
    global _INSTALLED
    if _INSTALLED:
        return
    warnings.filterwarnings(
        "ignore",
        message=r".*Timestamp\.utcnow is deprecated.*",
        category=Warning,
        module=r"yfinance\..*",
    )
    _INSTALLED = True

