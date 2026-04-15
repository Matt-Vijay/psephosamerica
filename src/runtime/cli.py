"""CLI argument parser for the Open Pact operator runtime.

Pure parser layer: no DB access, no network calls, no command execution.
Entry points: build_parser(), parse_args(argv).
"""

from __future__ import annotations

import argparse
import datetime
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openpact",
        description="Open Pact operator runtime.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    _add_bootstrap_db(sub)
    _add_status(sub)
    _add_load_congress(sub)
    _add_load_disclosures(sub)
    _add_recompute(sub)
    _add_publish(sub)

    return parser


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    return build_parser().parse_args(list(argv))


# ---------------------------------------------------------------------------
# Subcommand definitions
# ---------------------------------------------------------------------------


def _add_bootstrap_db(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "bootstrap-db",
        help="Apply schema and initial migration SQL to the target database.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print SQL that would be applied without executing it.",
    )


def _add_status(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "status",
        help="Show recent ingestion runs, parse runs, and active data sources.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="Number of recent runs to display (default: 20).",
    )


def _add_load_congress(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "load-congress",
        help="Fetch and load member, committee, bill, and vote data from Congress.gov.",
    )
    p.add_argument(
        "--congress",
        type=int,
        default=None,
        metavar="NUMBER",
        help="Congress number to load (e.g. 118). Defaults to current Congress.",
    )
    p.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help="Congress.gov API key. Falls back to CONGRESS_API_KEY env var.",
    )


def _add_load_disclosures(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "load-disclosures",
        help="Discover and store raw House and Senate disclosure artifacts.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber to load disclosures for (default: both).",
    )
    p.add_argument(
        "--year",
        type=int,
        default=None,
        metavar="YEAR",
        help="Filing year to load (e.g. 2024). Defaults to the current year.",
    )
    p.add_argument(
        "--local-root",
        default=None,
        metavar="PATH",
        help="Directory to mirror downloaded raw artifacts into. Defaults to the local artifact root.",
    )


def _add_recompute(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "recompute",
        help="Run a full conflict-of-interest recompute pass and write score snapshots.",
    )
    p.add_argument(
        "--snapshot-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Date key for the snapshot (default: today).",
    )


def _add_publish(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "publish",
        help="Export an immutable public snapshot from the most recent recompute.",
    )
    p.add_argument(
        "--snapshot-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Date key of the snapshot to publish (default: today).",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        metavar="PATH",
        help="Directory to write exported artifacts into. Defaults to local publish root.",
    )
    p.add_argument(
        "--zip-bundle",
        default=None,
        metavar="PATH",
        help="Path to the JSON ZIP bundle used to build ZIP feeds.",
    )


# ---------------------------------------------------------------------------
# Argument type helpers
# ---------------------------------------------------------------------------


def _parse_date(value: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid date '{value}'. Expected YYYY-MM-DD."
        )
