"""Core subcommands: bootstrap-db, status, load-congress, recompute, publish, oracle."""

from __future__ import annotations

import argparse

from src.runtime.cli._shared import _parse_date


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
        help="Congress.gov API key. Falls back to PSEPHOS_CONGRESS_API_KEY env var.",
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
    p.add_argument(
        "--statement-rows",
        default=None,
        metavar="PATH",
        help=(
            "Optional .json, .jsonl, or .csv prepared public-statement rows "
            "for member-sector ontology edges."
        ),
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
