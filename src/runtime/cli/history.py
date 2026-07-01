"""History backfill and congress-archive subcommands."""

from __future__ import annotations

import argparse

from src.runtime.cli._shared import _parse_date


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


def _add_check_history_backfill_inputs(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "check-history-backfill-inputs",
        help="Validate local backfill inputs before replaying historical snapshots.",
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
        help="Path to the prebuilt local disclosure bundle JSON.",
    )
    p.add_argument(
        "--artifact-root",
        default=None,
        metavar="PATH",
        help="Directory containing bundled disclosure artifacts when storage paths are relative.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Requested chamber scope to check truthfully against the current replay surface.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Requested disclosure artifact limit to check truthfully against the current replay surface.",
    )


def _add_write_congress_archive_manifest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "write-congress-archive-manifest",
        help="Scan a local Congress archive tree and write a manifest.json for validated replay.",
    )
    p.add_argument(
        "--archive-root",
        required=True,
        metavar="PATH",
        help="Path to the local Congress archive directory.",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number represented by the archive.",
    )
    p.add_argument(
        "--manifest-path",
        default=None,
        metavar="PATH",
        help="Path to write manifest.json to. Defaults to {archive-root}/manifest.json.",
    )


def _add_materialize_congress_archive(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "materialize-congress-archive",
        help="Fetch official Congress sources and write a canonical local archive tree plus manifest.",
    )
    p.add_argument(
        "--archive-root",
        required=True,
        metavar="PATH",
        help="Path to the local Congress archive directory to create.",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number to fetch and materialize.",
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
        help="Also fetch and store vote index/XML files for the requested Congress coverage.",
    )
    p.add_argument(
        "--house-vote-year",
        type=int,
        default=None,
        metavar="YEAR",
        help="Calendar year to fetch House vote XML files for.",
    )
    p.add_argument(
        "--senate-session",
        type=int,
        default=None,
        metavar="SESSION",
        help="Senate session number to fetch vote XML files for.",
    )
    p.add_argument(
        "--manifest-path",
        default=None,
        metavar="PATH",
        help="Path to write manifest.json to. Defaults to {archive-root}/manifest.json.",
    )


def _add_materialize_history_backfill_inputs(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "materialize-history-backfill-inputs",
        help="Build both local backfill input legs, then run the typed readiness check.",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number to materialize for both the archive and replay plan.",
    )
    p.add_argument(
        "--congress-archive-root",
        required=True,
        metavar="PATH",
        help="Path to the local Congress archive directory to create.",
    )
    p.add_argument(
        "--disclosures-bundle-path",
        required=True,
        metavar="PATH",
        help="Path where the canonical disclosures bundle JSON will be written.",
    )
    p.add_argument(
        "--disclosures-artifact-root",
        required=True,
        metavar="PATH",
        help="Directory where disclosure artifacts will be written.",
    )
    p.add_argument(
        "--disclosures-year",
        action="append",
        type=int,
        metavar="YEAR",
        help="Disclosure filing year to materialize. Repeat for multiple years.",
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
        help="Also fetch vote XML coverage when materializing the Congress archive.",
    )
    p.add_argument(
        "--house-vote-year",
        type=int,
        default=None,
        metavar="YEAR",
        help="Calendar year to fetch House vote XML files for.",
    )
    p.add_argument(
        "--senate-session",
        type=int,
        default=None,
        metavar="SESSION",
        help="Senate session number to fetch vote XML files for.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber scope for disclosure materialization and readiness checking.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Optional disclosure replay limit to validate against the staged inputs.",
    )
    p.add_argument(
        "--reuse-existing-inputs",
        action="store_true",
        default=False,
        help="Reuse already-staged local inputs when present instead of rematerializing them.",
    )


def _add_run_history_launch_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "run-history-launch-local",
        help="Materialize local replay inputs, validate readiness, then run the launch-window history backfill.",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number to materialize and replay historically.",
    )
    p.add_argument(
        "--congress-archive-root",
        required=True,
        metavar="PATH",
        help="Path to the local Congress archive directory to create.",
    )
    p.add_argument(
        "--disclosures-bundle-path",
        required=True,
        metavar="PATH",
        help="Path where the canonical disclosures bundle JSON will be written.",
    )
    p.add_argument(
        "--disclosures-artifact-root",
        required=True,
        metavar="PATH",
        help="Directory where disclosure artifacts will be written.",
    )
    p.add_argument(
        "--disclosures-year",
        action="append",
        type=int,
        metavar="YEAR",
        help="Disclosure filing year to materialize. Repeat for multiple years.",
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
        "--api-key",
        default=None,
        metavar="KEY",
        help="Congress.gov API key. Falls back to PSEPHOS_CONGRESS_API_KEY env var.",
    )
    p.add_argument(
        "--include-votes",
        action="store_true",
        default=False,
        help="Also fetch vote XML coverage when materializing the Congress archive.",
    )
    p.add_argument(
        "--house-vote-year",
        type=int,
        default=None,
        metavar="YEAR",
        help="Calendar year to fetch House vote XML files for.",
    )
    p.add_argument(
        "--senate-session",
        type=int,
        default=None,
        metavar="SESSION",
        help="Senate session number to fetch vote XML files for.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber scope for disclosure materialization and replay.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of disclosure artifacts to process per snapshot (default: no limit).",
    )
    p.add_argument(
        "--reuse-existing-inputs",
        action="store_true",
        default=False,
        help="Reuse already-staged local inputs when present instead of rematerializing them.",
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
