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
    _add_parse_disclosures(sub)
    _add_process_disclosures(sub)
    _add_recompute(sub)
    _add_publish(sub)
    _add_load_congress_local(sub)
    _add_process_disclosures_local(sub)
    _add_run_oracle_local(sub)
    _add_plan_history_backfill(sub)
    _add_run_history_backfill_local(sub)
    _add_aggregate_history(sub)
    _add_verify_publish(sub)
    _add_verify_publish_roundtrip(sub)
    _add_verify_history_aggregate(sub)

    return parser


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    args = build_parser().parse_args(list(argv))
    if (
        args.command == "load-congress"
        and (args.house_vote_year is not None or args.senate_session is not None)
    ):
        args.include_votes = True
    return args


# ---------------------------------------------------------------------------
# Subcommand definitions
# ---------------------------------------------------------------------------


def _add_bootstrap_db(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    description = "Apply the canonical bootstrap schema from db/schema.sql to the target database."
    p = sub.add_parser(
        "bootstrap-db",
        help=description,
        description=description,
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show the bootstrap plan without applying schema SQL.",
    )


def _add_status(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "status",
        help="Show summary counts plus latest ingestion, parse, artifact, and active source state.",
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
    p.add_argument(
        "--include-votes",
        action="store_true",
        default=False,
        help="Also fetch and load vote records for the requested Congress.",
    )
    p.add_argument(
        "--house-vote-year",
        type=int,
        default=None,
        metavar="YEAR",
        help="Calendar year to fetch House roll-call votes for (e.g. 2024).",
    )
    p.add_argument(
        "--senate-session",
        type=int,
        default=None,
        metavar="SESSION",
        help="Senate session number to fetch votes for (e.g. 1 or 2).",
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


def _add_parse_disclosures(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "parse-disclosures",
        help="Run the text-extract parse pipeline over stored, unparsed disclosure artifacts.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber to parse (default: both).",
    )
    p.add_argument(
        "--local-root",
        default=None,
        metavar="PATH",
        help="Directory containing downloaded artifacts. Defaults to local artifact root.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of artifacts to parse in this run (default: no limit).",
    )


def _add_process_disclosures(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "process-disclosures",
        help="Run the parse-transform-load pipeline over stored disclosure artifacts.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber to process (default: both).",
    )
    p.add_argument(
        "--local-root",
        default=None,
        metavar="PATH",
        help="Directory containing downloaded artifacts. Defaults to local artifact root.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of artifacts to process in this run (default: no limit).",
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
        required=True,
        default=None,
        metavar="PATH",
        help="Path to the JSON ZIP bundle used to build ZIP feeds.",
    )


# ---------------------------------------------------------------------------
# Local-oracle subcommand definitions
# ---------------------------------------------------------------------------


def _add_load_congress_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "load-congress-local",
        help="Load congress member, committee, bill, sponsor, and cosponsor data from a local archive.",
    )
    p.add_argument(
        "--archive",
        required=True,
        metavar="PATH",
        help="Path to the local congress archive directory or manifest JSON.",
    )
    p.add_argument(
        "--congress",
        type=int,
        required=True,
        metavar="NUMBER",
        help="Congress number the archive represents (e.g. 119).",
    )


def _add_process_disclosures_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "process-disclosures-local",
        help="Run the parse-transform-load pipeline over a local disclosure bundle.",
    )
    p.add_argument(
        "--bundle",
        required=True,
        metavar="PATH",
        help="Path to the local disclosure bundle (directory or archive).",
    )


def _add_run_oracle_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "run-oracle-local",
        help=(
            "Run the local oracle over a local congress archive and a prebuilt disclosures "
            "bundle. This surface does not fetch live data, request votes, or generate ZIP feeds."
        ),
    )
    p.add_argument(
        "--congress-archive",
        required=True,
        metavar="PATH",
        help="Path to the local congress archive directory or manifest JSON.",
    )
    p.add_argument(
        "--disclosures-bundle",
        required=True,
        metavar="PATH",
        help="Path to the prebuilt local disclosure bundle (directory or archive).",
    )
    p.add_argument(
        "--congress",
        type=int,
        default=None,
        metavar="NUMBER",
        help=(
            "Congress number to use for the archive stage. Defaults to the current Congress "
            "by calendar date when omitted; the resolved value is surfaced in the output."
        ),
    )
    p.add_argument(
        "--snapshot-date",
        type=_parse_date,
        required=True,
        metavar="YYYY-MM-DD",
        help="Date key for the oracle snapshot (e.g. 2025-01-15).",
    )
    p.add_argument(
        "--target-dir",
        required=True,
        metavar="PATH",
        help="Directory where published oracle artifacts are written.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber to process (default: both).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of disclosure artifacts to process (default: no limit).",
    )
    p.add_argument(
        "--snapshot-id",
        default=None,
        metavar="ID",
        help="Explicit snapshot identifier; defaults to snapshot-date ISO string.",
    )
    p.add_argument(
        "--artifact-root",
        default=None,
        metavar="PATH",
        help="Directory containing bundled disclosure artifacts when bundle storage paths are relative.",
    )


def _add_plan_history_backfill(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "plan-history-backfill",
        help="Plan weekly historical snapshot dates for a Congress and target root.",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number to plan historical weekly snapshots for.",
    )
    p.add_argument(
        "--target-root",
        required=True,
        metavar="PATH",
        help="Directory that will hold one per-snapshot publish root per planned week.",
    )
    p.add_argument(
        "--start-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Optional lower bound; clamped to the Congress term.",
    )
    p.add_argument(
        "--end-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Optional upper bound; capped at today for the active Congress.",
    )


def _add_run_history_backfill_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "run-history-backfill-local",
        help="Replay the local oracle weekly across a Congress window and optionally aggregate the results.",
    )
    p.add_argument(
        "--congress-archive",
        required=True,
        metavar="PATH",
        help="Path to the local congress archive directory or manifest JSON.",
    )
    p.add_argument(
        "--disclosures-bundle",
        required=True,
        metavar="PATH",
        help="Path to the prebuilt local disclosure bundle (directory or archive).",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number to replay historically.",
    )
    p.add_argument(
        "--target-root",
        required=True,
        metavar="PATH",
        help="Directory that will hold one per-snapshot publish root per planned week.",
    )
    p.add_argument(
        "--aggregate-root",
        default=None,
        metavar="PATH",
        help="Optional directory to write the merged history-serving publish root into.",
    )
    p.add_argument(
        "--start-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Optional lower bound; clamped to the Congress term.",
    )
    p.add_argument(
        "--end-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Optional upper bound; capped at today for the active Congress.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber to process in disclosure replay steps (default: both).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of disclosure artifacts to process per snapshot (default: no limit).",
    )
    p.add_argument(
        "--artifact-root",
        default=None,
        metavar="PATH",
        help="Directory containing bundled disclosure artifacts when bundle storage paths are relative.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Replace existing per-snapshot publish roots instead of skipping them.",
    )
    p.add_argument(
        "--continue-on-error",
        action="store_true",
        default=False,
        help="Continue attempting later snapshots after a failure.",
    )


def _add_aggregate_history(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "aggregate-history",
        help="Merge many per-snapshot publish roots into one history-serving publish root.",
    )
    p.add_argument(
        "--source-root",
        required=True,
        action="append",
        metavar="PATH",
        help="Per-snapshot publish root to aggregate. Repeat for multiple snapshots.",
    )
    p.add_argument(
        "--target-root",
        required=True,
        metavar="PATH",
        help="Directory to write the aggregated history-serving publish root into.",
    )


def _add_verify_publish(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-publish",
        help="Verify the integrity of a locally published snapshot tree.",
    )
    p.add_argument(
        "--publish-root",
        required=True,
        metavar="PATH",
        help="Root directory of the published snapshot tree to verify.",
    )


def _add_verify_publish_roundtrip(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-publish-roundtrip",
        help="Verify the DB-to-publish roundtrip for a locally published snapshot tree.",
    )
    p.add_argument(
        "--publish-root",
        required=True,
        metavar="PATH",
        help="Root directory of the published snapshot tree to verify.",
    )


def _add_verify_history_aggregate(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-history-aggregate",
        help="Verify the internal consistency of a locally aggregated history-serving publish root.",
    )
    p.add_argument(
        "--publish-root",
        required=True,
        metavar="PATH",
        help="Root directory of the history aggregate publish tree to verify.",
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
