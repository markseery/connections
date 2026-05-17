"""Reusable CLI command for Robinhood CSV import."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from common.compound.command_base import BaseCommand
from common.compound.finance_pipeline.robinhood_csvimport import RobinhoodCsvImport


@dataclass
class RobinhoodCsvImportArgs:
    account_name: str
    input_file_name: str


class RobinhoodCsvImportCommand(BaseCommand[RobinhoodCsvImportArgs]):
    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description=(
                "Import records from application_files/data/portfolio/<input_file> "
                "into application_files/data/portfolio/robinhood/<account_name>/"
            )
        )
        parser.add_argument("account_name", help="Output account folder name under robinhood/")
        parser.add_argument(
            "input_file_name",
            help="CSV filename located in application_files/data/portfolio",
        )
        return parser

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> RobinhoodCsvImportArgs:
        return RobinhoodCsvImportArgs(
            account_name=str(args.account_name),
            input_file_name=str(args.input_file_name),
        )

    @classmethod
    def run(cls, args: RobinhoodCsvImportArgs) -> int:
        importer = RobinhoodCsvImport(
            account_name=args.account_name,
            input_file_name=args.input_file_name,
        )
        summary = importer.import_records()
        print(
            "Import complete: "
            f"total_rows={summary.total_rows}, added={summary.added}, skipped={summary.skipped}"
        )
        print(f"Output directory: {summary.output_dir}")
        return 0

