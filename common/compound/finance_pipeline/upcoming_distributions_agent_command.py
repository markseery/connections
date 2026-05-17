"""Backward-compatible facade for the upcoming-distributions agent CLI.

Implementation lives in ``application_files.distributions``.
"""

from __future__ import annotations

from application_files.distributions.command import UpcomingDistributionsAgentCommand
from application_files.distributions.constants import DEFAULT_INTENT, DEFAULT_SYNTHESIS_TOPIC
from application_files.distributions.models import AgentConfig, UpcomingDistributionsAgentArgs
from common.compound.finance_pipeline.upcoming_distributions_report import (
    extract_cached_portfolio_analysis,
    render_distribution_target_options,
    render_near_term_table,
)

# Backward compatibility for tests importing private helpers from this module
_extract_cached_portfolio_analysis = extract_cached_portfolio_analysis
_render_near_term_table = render_near_term_table
_render_distribution_target_options = render_distribution_target_options

__all__ = [
    "AgentConfig",
    "DEFAULT_INTENT",
    "DEFAULT_SYNTHESIS_TOPIC",
    "UpcomingDistributionsAgentArgs",
    "UpcomingDistributionsAgentCommand",
    "_extract_cached_portfolio_analysis",
    "_render_distribution_target_options",
    "_render_near_term_table",
]
