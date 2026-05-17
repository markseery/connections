"""Reusable CLI command for Robinhood position analysis."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from common.compound.command_base import BaseCommand, UsageError
from common.compound.finance_pipeline.position_reporting import print_position_summary_report
from common.compound.finance_pipeline.robinhood_positions import RobinhoodPositionAnalyzer


@dataclass
class RobinhoodPositionsArgs:
    account_name: str
    include_zero: bool = False
    output: Path | None = None
    forecast_as_of: str | None = None
    mc_constraints_file: Path | None = None
    mc_symbol_cap_pct: float | None = None
    mc_income_cap_pct: float | None = None
    mc_growth_cap_pct: float | None = None
    mc_min_price_history_months: float | None = None
    mc_min_confidence_score: float | None = None
    mc_low_confidence_threshold: float | None = None
    mc_max_low_confidence_total_weight_pct: float | None = None
    mc_allocation_multiplier_min: float | None = None
    mc_allocation_multiplier_max: float | None = None
    mc_max_annualized_growth_pct: float | None = None


class RobinhoodPositionsCommand(BaseCommand[RobinhoodPositionsArgs]):
    """Encapsulates parser, validation, and execution for positions CLI."""

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description=(
                "Analyze application_files/data/portfolio/robinhood/<account_name> "
                "and produce a current-positions summary."
            )
        )
        parser.add_argument("account_name", help="Account folder name under robinhood/")
        parser.add_argument(
            "--include-zero",
            action="store_true",
            help="Include symbols with zero net quantity in output.",
        )
        parser.add_argument(
            "--output",
            type=Path,
            default=None,
            help="Optional output file path (default: <account_name>.positions.json in account folder).",
        )
        parser.add_argument(
            "--forecast-as-of",
            type=str,
            default=None,
            help="Optional as-of date for cash forecast in YYYY-MM-DD format.",
        )
        parser.add_argument(
            "--mc-constraints-file",
            type=Path,
            default=None,
            help="Optional Monte Carlo constraints YAML file path.",
        )
        parser.add_argument(
            "--mc-symbol-cap-pct",
            type=float,
            default=None,
            help="Override max symbol weight cap percent.",
        )
        parser.add_argument(
            "--mc-income-cap-pct",
            type=float,
            default=None,
            help="Override max income contribution cap percent.",
        )
        parser.add_argument(
            "--mc-growth-cap-pct",
            type=float,
            default=None,
            help="Override max growth contribution cap percent.",
        )
        parser.add_argument(
            "--mc-min-price-history-months",
            type=float,
            default=None,
            help="Override minimum price-history months required for Monte Carlo universe.",
        )
        parser.add_argument(
            "--mc-min-confidence-score",
            type=float,
            default=None,
            help="Override minimum allocation confidence score (0-100) required for Monte Carlo universe.",
        )
        parser.add_argument(
            "--mc-low-confidence-threshold",
            type=float,
            default=None,
            help="Override low-confidence score threshold (0-100) used for low-confidence weight limits.",
        )
        parser.add_argument(
            "--mc-max-low-confidence-total-weight-pct",
            type=float,
            default=None,
            help="Override max total weight percent allowed for low-confidence symbols.",
        )
        parser.add_argument(
            "--mc-allocation-multiplier-min",
            type=float,
            default=None,
            help="Override allocation confidence multiplier minimum.",
        )
        parser.add_argument(
            "--mc-allocation-multiplier-max",
            type=float,
            default=None,
            help="Override allocation confidence multiplier maximum.",
        )
        parser.add_argument(
            "--mc-max-annualized-growth-pct",
            type=float,
            default=None,
            help="Override cap on annualized growth percent used for growth projection.",
        )
        return parser

    @classmethod
    def from_namespace(cls, parsed: argparse.Namespace) -> RobinhoodPositionsArgs:
        return RobinhoodPositionsArgs(
            account_name=parsed.account_name,
            include_zero=bool(parsed.include_zero),
            output=parsed.output,
            forecast_as_of=parsed.forecast_as_of,
            mc_constraints_file=parsed.mc_constraints_file,
            mc_symbol_cap_pct=parsed.mc_symbol_cap_pct,
            mc_income_cap_pct=parsed.mc_income_cap_pct,
            mc_growth_cap_pct=parsed.mc_growth_cap_pct,
            mc_min_price_history_months=parsed.mc_min_price_history_months,
            mc_min_confidence_score=parsed.mc_min_confidence_score,
            mc_low_confidence_threshold=parsed.mc_low_confidence_threshold,
            mc_max_low_confidence_total_weight_pct=parsed.mc_max_low_confidence_total_weight_pct,
            mc_allocation_multiplier_min=parsed.mc_allocation_multiplier_min,
            mc_allocation_multiplier_max=parsed.mc_allocation_multiplier_max,
            mc_max_annualized_growth_pct=parsed.mc_max_annualized_growth_pct,
        )

    @staticmethod
    def _parse_forecast_as_of(raw: str | None) -> date | None:
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError as exc:
            raise UsageError("--forecast-as-of must be in YYYY-MM-DD format") from exc

    @staticmethod
    def _build_monte_carlo_overrides(args: RobinhoodPositionsArgs) -> dict[str, float | None]:
        return {
            "max_symbol_weight_pct": args.mc_symbol_cap_pct,
            "max_income_contribution_pct": args.mc_income_cap_pct,
            "max_growth_contribution_pct": args.mc_growth_cap_pct,
            "min_price_history_months": args.mc_min_price_history_months,
            "min_confidence_score": args.mc_min_confidence_score,
            "low_confidence_score_threshold": args.mc_low_confidence_threshold,
            "max_low_confidence_total_weight_pct": args.mc_max_low_confidence_total_weight_pct,
            "allocation_confidence_multiplier_min": args.mc_allocation_multiplier_min,
            "allocation_confidence_multiplier_max": args.mc_allocation_multiplier_max,
            "max_annualized_growth_pct_for_projection": args.mc_max_annualized_growth_pct,
        }

    @classmethod
    def run(cls, args: RobinhoodPositionsArgs) -> int:
        forecast_as_of = cls._parse_forecast_as_of(args.forecast_as_of)
        analyzer = RobinhoodPositionAnalyzer(
            account_name=args.account_name,
            monte_carlo_constraints_path=args.mc_constraints_file,
        )
        analyzer.set_monte_carlo_overrides(cls._build_monte_carlo_overrides(args))
        summary = analyzer.analyze(
            include_zero_positions=args.include_zero,
            forecast_as_of=forecast_as_of,
        )
        output_path = analyzer.write_summary(summary, args.output)
        excel_path = analyzer.write_excel_report(summary)
        cash_forecast_excel_path = excel_path.with_name(
            f"{summary.account_name}.cash_forecast.xlsx"
        )

        print_position_summary_report(
            summary=summary,
            analyzer=analyzer,
            output_path=str(output_path),
            excel_path=str(excel_path),
            cash_forecast_excel_path=str(cash_forecast_excel_path),
            stream=sys.stdout,
        )
        return 0

