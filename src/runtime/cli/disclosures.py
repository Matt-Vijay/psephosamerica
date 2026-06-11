"""Financial-disclosure subcommands."""

from __future__ import annotations

import argparse


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


def _add_materialize_disclosures_bundle(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "materialize-disclosures-bundle",
        help="Fetch official disclosure indexes and PDFs, then write a canonical local bundle.json.",
    )
    p.add_argument(
        "--bundle-path",
        required=True,
        metavar="PATH",
        help="Path where the canonical disclosures bundle JSON will be written.",
    )
    p.add_argument(
        "--artifact-root",
        required=True,
        metavar="PATH",
        help="Directory where downloaded disclosure PDFs will be written.",
    )
    p.add_argument(
        "--year",
        required=True,
        action="append",
        type=int,
        metavar="YEAR",
        help="Calendar filing year to include. Repeat for multiple years.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber scope to materialize (default: both).",
    )
