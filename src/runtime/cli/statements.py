"""Public-statement subcommands."""

from __future__ import annotations

import argparse


def _add_materialize_public_statement_rows(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "materialize-public-statement-rows",
        help="Convert official raw member statements into recompute-ready rows.",
    )
    p.add_argument(
        "--input",
        required=True,
        metavar="PATH",
        help="Raw .json, .jsonl, or .csv official public-statement records.",
    )
    p.add_argument(
        "--output",
        default="data/prepared/public-statement-sector-rows.jsonl",
        metavar="PATH",
        help="Prepared JSONL output path for recompute --statement-rows.",
    )
    p.add_argument(
        "--taxonomy",
        default="data/taxonomy/sectors.yaml",
        metavar="PATH",
        help="Sector taxonomy used for alias normalization and keyword matching.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate and summarize rows without writing output.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite an existing output JSONL.",
    )
    p.add_argument(
        "--summary-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the materialization summary.",
    )


def _add_materialize_public_statement_rss(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "materialize-public-statement-rss",
        help="Fetch raw official member statement records from legislator RSS feeds.",
    )
    p.add_argument(
        "--output",
        default="data/raw/public-statements.jsonl",
        metavar="PATH",
        help="Raw statement JSONL output path.",
    )
    p.add_argument(
        "--source-file",
        default=None,
        metavar="PATH",
        help="Optional local legislators-current.yaml source file.",
    )
    p.add_argument(
        "--source-url",
        default=(
            "https://raw.githubusercontent.com/unitedstates/congress-legislators/"
            "main/legislators-current.yaml"
        ),
        metavar="URL",
        help="HTTPS legislators YAML source URL used when --source-file is absent.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Count available official RSS feeds without fetching feed items.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite an existing raw statement JSONL.",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Network timeout per source/feed request.",
    )
    p.add_argument(
        "--max-feeds",
        type=int,
        default=None,
        metavar="N",
        help="Optional cap on member RSS feeds fetched.",
    )
    p.add_argument(
        "--max-items-per-feed",
        type=int,
        default=None,
        metavar="N",
        help="Optional cap on RSS items per member feed.",
    )
    p.add_argument(
        "--summary-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the materialization summary.",
    )


def _add_verify_public_statement_rows(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "verify-public-statement-rows",
        help="Verify prepared source-backed public-statement rows for recompute.",
    )
    p.add_argument(
        "--statement-rows",
        required=True,
        metavar="PATH",
        help="Prepared .json, .jsonl, or .csv public-statement row artifact.",
    )
    p.add_argument(
        "--min-rows",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green unless at least N rows are present.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the verification audit payload.",
    )
