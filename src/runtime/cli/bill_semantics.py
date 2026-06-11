"""Bill-semantics subcommands."""

from __future__ import annotations

import argparse

from src.runtime.cli._shared import _parse_date


def _add_materialize_bill_semantics(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "materialize-bill-semantics",
        help="Use OpenAI to extract and cache source-backed semantic bill features.",
    )
    p.add_argument(
        "--output-root",
        required=True,
        metavar="PATH",
        help="Directory where bill semantic JSON artifacts and index.json will be written.",
    )
    p.add_argument(
        "--model",
        default="gpt-5.5",
        metavar="MODEL",
        help="OpenAI model for semantic extraction (default: gpt-5.5).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Optional max number of bills to materialize.",
    )
    p.add_argument(
        "--bill-key",
        action="append",
        default=None,
        metavar="KEY",
        help="Materialize only this bill key, for example 119-hr-1. Repeat for multiple bills.",
    )
    p.add_argument(
        "--missing-from-report",
        default=None,
        metavar="PATH",
        help=(
            "Materialize bill keys listed in prediction-eval-report semantic "
            "coverage missing_bill_keys fields."
        ),
    )
    p.add_argument(
        "--feature-cutoff",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Only materialize bills available at or before this feature cutoff, "
            "matching prediction inventory/eval windows."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Plan semantic materialization targets without calling OpenAI or writing cache files.",
    )
    p.add_argument(
        "--plan-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for a dry-run bill-semantics target plan.",
    )
    p.add_argument(
        "--summary-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the bill-semantics materialization summary.",
    )
    p.add_argument(
        "--fail-on-unmatched-targets",
        action="store_true",
        default=False,
        help="Return non-green when requested bill keys are not available in loaded bills.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Recompute semantic payloads even when cached files already exist.",
    )


def _add_verify_bill_semantics(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-bill-semantics",
        help="Verify a materialized bill-semantics cache index and payload hashes.",
    )
    p.add_argument(
        "--root",
        required=True,
        metavar="PATH",
        help="Bill-semantics cache root containing index.json and bills/*.json.",
    )
    p.add_argument(
        "--require-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Return non-green unless the semantic cache contains this model name; repeatable.",
    )
    p.add_argument(
        "--require-source-inputs-sha256",
        action="store_true",
        default=False,
        help="Return non-green unless the cache index records source_inputs_sha256.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the bill-semantics verification audit payload.",
    )


def _add_verify_bill_semantics_plan(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-bill-semantics-plan",
        help="Verify a bill-semantics dry-run target plan and source eval-report hash.",
    )
    p.add_argument(
        "--plan",
        required=True,
        metavar="PATH",
        help="Path to a dry-run bill-semantics plan written by --plan-output.",
    )
    p.add_argument(
        "--fail-on-unmatched-targets",
        action="store_true",
        default=False,
        help="Return non-green when the plan contains unmatched target bill keys.",
    )
    p.add_argument(
        "--require-source-report",
        action="store_true",
        default=False,
        help="Require source eval-report path, hash, missing keys, and run metadata.",
    )
    p.add_argument(
        "--require-matched-source-anchors",
        action="store_true",
        default=False,
        help="Return non-green when matched plan bills lack source anchors.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the bill-semantics-plan verification audit payload.",
    )
