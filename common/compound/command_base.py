"""Reusable base class and error types for CLI commands."""

from __future__ import annotations

import argparse
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar


ArgsT = TypeVar("ArgsT")


@dataclass
class CommandError(Exception):
    """Expected command failure with explicit exit code."""

    message: str
    exit_code: int = 1

    def __str__(self) -> str:
        return self.message


class UsageError(CommandError):
    """Input/validation failure raised by commands."""


class BaseCommand(ABC, Generic[ArgsT]):
    """Template class for parser-backed commands."""

    @classmethod
    @abstractmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def from_namespace(cls, args: argparse.Namespace) -> ArgsT:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def run(cls, args: ArgsT) -> int:
        raise NotImplementedError

    @classmethod
    def parse_args(cls, argv: list[str] | None = None) -> ArgsT:
        namespace = cls.build_parser().parse_args(argv)
        return cls.from_namespace(namespace)

    @classmethod
    def execute(cls, argv: list[str] | None = None) -> int:
        try:
            args = cls.parse_args(argv)
            return cls.run(args)
        except CommandError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return int(exc.exit_code)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

