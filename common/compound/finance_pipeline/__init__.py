"""Reusable finance CSV → JSON → aggregate pipeline (stdlib-only)."""

from __future__ import annotations

import importlib
from typing import Any

from .activity_parser import ActivityTableParser
from .aggregator import ActivityAggregator
from .csv_ollama import CsvOllamaClient, CsvOllamaConfig
from .distribution_history_comparison import (
    DistributionHistoryComparison,
    DistributionPoint,
    DistributionSeries,
)
from .pipeline import ActivityPipeline, PipelineConfig, PipelineResult
from .price_history_growth import PriceHistoryGrowthAnalyzer, PriceHistoryGrowthResult
from .robinhood_csvimport import ImportSummary, RobinhoodCsvImport
from .stockanalysis_dividends import (
    StockAnalysisDividendExtractor,
    StockAnalysisDividendRow,
    StockAnalysisDividendSnapshot,
)

__all__ = [
    "ActivityAggregator",
    "ActivityPipeline",
    "ActivityTableParser",
    "CsvOllamaClient",
    "CsvOllamaConfig",
    "DistributionHistoryComparison",
    "DistributionPoint",
    "DistributionSeries",
    "ImportSummary",
    "PipelineConfig",
    "PipelineResult",
    "PriceHistoryGrowthAnalyzer",
    "PriceHistoryGrowthResult",
    "PositionSummary",
    "RobinhoodPositionAnalyzer",
    "RobinhoodCsvImport",
    "StockAnalysisDividendExtractor",
    "StockAnalysisDividendRow",
    "StockAnalysisDividendSnapshot",
]

# robinhood_positions pulls openpyxl; lazy-load so lightweight imports (e.g. distribution_pattern_engine) work
# without that dependency.
_LAZY_ROBINHOOD_POSITIONS = ("PositionSummary", "RobinhoodPositionAnalyzer")


def __getattr__(name: str) -> Any:
    if name in _LAZY_ROBINHOOD_POSITIONS:
        mod = importlib.import_module(".robinhood_positions", __package__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
