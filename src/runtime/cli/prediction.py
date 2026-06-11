"""Prediction backtest/inventory/source-url-audit subcommands."""

from __future__ import annotations

import argparse
import datetime

from src.runtime.cli._shared import _parse_date


def _add_prediction_backtest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-backtest",
        help="Backtest vote predictions using only pre-cutoff features against future votes.",
    )
    p.add_argument(
        "--model",
        choices=["baseline", "ontology"],
        default="baseline",
        help="Prediction scorer to evaluate (default: baseline).",
    )
    p.add_argument(
        "--feature-cutoff",
        type=_parse_date,
        default=datetime.date(2024, 12, 31),
        metavar="YYYY-MM-DD",
        help="Latest vote date allowed in features (default: 2024-12-31).",
    )
    p.add_argument(
        "--label-start",
        type=_parse_date,
        default=datetime.date(2025, 1, 1),
        metavar="YYYY-MM-DD",
        help="First vote date to evaluate (default: 2025-01-01).",
    )
    p.add_argument(
        "--label-end",
        type=_parse_date,
        default=datetime.date(2026, 12, 31),
        metavar="YYYY-MM-DD",
        help="Last vote date to evaluate (default: 2026-12-31).",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the full per-vote backtest artifact.",
    )
    p.add_argument(
        "--bill-semantics-root",
        default=None,
        metavar="PATH",
        help="Optional materialized bill-semantics root for ontology model features.",
    )
    p.add_argument(
        "--congress-archive-manifest",
        default=None,
        metavar="PATH",
        help="Optional Congress archive manifest to record in backtest run provenance.",
    )
    p.add_argument(
        "--fail-on-mixed-bill-semantics-models",
        action="store_true",
        default=False,
        help="Return non-green when a bill-semantics cache contains multiple model names.",
    )
    p.add_argument(
        "--fail-on-missing-bill-semantics-root",
        action="store_true",
        default=False,
        help="Return non-green when ontology backtest runs without --bill-semantics-root.",
    )


def _add_prediction_input_inventory(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-input-inventory",
        help="Report cutoff-window prediction input counts and source coverage without scoring.",
    )
    p.add_argument(
        "--training-feature-cutoff",
        type=_parse_date,
        default=datetime.date(2022, 12, 31),
        metavar="YYYY-MM-DD",
        help="Latest vote date allowed in learned-model training features.",
    )
    p.add_argument(
        "--train-start",
        type=_parse_date,
        default=datetime.date(2023, 1, 1),
        metavar="YYYY-MM-DD",
        help="First historical vote date used as learned-model labels.",
    )
    p.add_argument(
        "--train-end",
        type=_parse_date,
        default=datetime.date(2024, 12, 31),
        metavar="YYYY-MM-DD",
        help="Last historical vote date used as learned-model labels.",
    )
    p.add_argument(
        "--feature-cutoff",
        type=_parse_date,
        default=datetime.date(2024, 12, 31),
        metavar="YYYY-MM-DD",
        help="Latest vote date allowed in evaluation features.",
    )
    p.add_argument(
        "--label-start",
        type=_parse_date,
        default=datetime.date(2025, 1, 1),
        metavar="YYYY-MM-DD",
        help="First future vote date to evaluate.",
    )
    p.add_argument(
        "--label-end",
        type=_parse_date,
        default=datetime.date(2026, 12, 31),
        metavar="YYYY-MM-DD",
        help="Last future vote date to evaluate.",
    )
    p.add_argument(
        "--congress-archive-manifest",
        default=None,
        metavar="PATH",
        help=(
            "Optional local Congress archive manifest whose path and SHA-256 should be "
            "recorded in run_metadata for prediction-input provenance."
        ),
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the prediction input inventory payload.",
    )


def _add_verify_prediction_input_inventory(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-input-inventory",
        help="Verify a prediction-input-inventory artifact and its cutoff metadata.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-input-inventory JSON artifact written by --output.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Require cutoff and source-state run_metadata on the artifact.",
    )
    p.add_argument(
        "--require-congress-archive-manifest",
        action="store_true",
        default=False,
        help=(
            "Require run_metadata to record a readable Congress archive manifest path "
            "with a matching SHA-256."
        ),
    )
    p.add_argument(
        "--require-clean-inventory",
        action="store_true",
        default=False,
        help="Return non-green when inventory has warning or blocking reasons.",
    )
    p.add_argument(
        "--require-portable-jurisdiction-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when portable legislative source rows are missing "
            "explicit jurisdiction_id values."
        ),
    )
    p.add_argument(
        "--require-portable-body-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when portable non-Congress rows are missing explicit "
            "legislative_body_id values."
        ),
    )
    p.add_argument(
        "--require-portable-session-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when portable non-Congress rows are missing explicit "
            "legislative_session_id values."
        ),
    )
    p.add_argument(
        "--require-source-family",
        dest="require_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Return non-green when the inventory source_family_ids do not include "
            "FAMILY. Repeat to require multiple families such as congress_vote and "
            "congress_bill."
        ),
    )
    p.add_argument(
        "--min-training-labels",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory has fewer training labels than N.",
    )
    p.add_argument(
        "--min-evaluation-labels",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory has fewer evaluation labels than N.",
    )
    p.add_argument(
        "--min-training-feature-vote-history-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=("Return non-green when training feature vote-history source coverage is below RATE."),
    )
    p.add_argument(
        "--min-evaluation-feature-vote-history-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Return non-green when evaluation feature vote-history source coverage is below RATE."
        ),
    )
    p.add_argument(
        "--min-training-label-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when training label source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-evaluation-label-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when evaluation label source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-training-label-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when training label official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-evaluation-label-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when evaluation label official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when bill source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when bill official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-sponsor-availability-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when cutoff-available bill sponsor coverage is below RATE.",
    )
    p.add_argument(
        "--require-no-bill-sponsor-introduced-date-fallbacks",
        action="store_true",
        default=False,
        help=(
            "Return non-green when sponsor availability depends on primary-sponsor "
            "introduced_date fallback instead of explicit sponsor dates."
        ),
    )
    p.add_argument(
        "--min-ontology-source-anchor-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when ontology source-anchor coverage is below RATE.",
    )
    p.add_argument(
        "--min-ontology-official-source-anchor-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when ontology official source-anchor coverage is below RATE.",
    )
    p.add_argument(
        "--min-fec-member-attribution-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when FEC member attribution is below RATE.",
    )
    p.add_argument(
        "--min-fec-contributions",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when FEC contribution rows are fewer than N.",
    )
    p.add_argument(
        "--min-member-attributed-fec-contributions",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when member-attributed FEC contribution rows are fewer than N.",
    )
    p.add_argument(
        "--min-members-with-fec-candidate-id",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when members with FEC candidate IDs are fewer than N.",
    )
    p.add_argument(
        "--min-public-statement-signals",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when public-statement signal rows are fewer than N.",
    )
    p.add_argument(
        "--min-members-with-public-statement-signals",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when members with public-statement signals are fewer than N.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the inventory verification audit payload.",
    )


def _add_prediction_source_url_audit(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-source-url-audit",
        help="Audit missing URL-backed feature sources from a prediction eval report.",
    )
    p.add_argument(
        "--eval-report",
        required=True,
        metavar="PATH",
        help="Prediction eval report JSON artifact to inspect.",
    )
    p.add_argument(
        "--fail-on-gaps",
        action="store_true",
        default=False,
        help="Return non-green when any feature source URL gaps are present.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the source URL audit payload.",
    )


def _add_verify_prediction_source_url_audit(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-source-url-audit",
        help="Verify a prediction-source-url-audit artifact and source-report hash.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-source-url-audit JSON artifact written by --output.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Require auditable run_metadata with matching source_state.",
    )
    p.add_argument(
        "--require-no-gaps",
        action="store_true",
        default=False,
        help="Return non-green when any feature source URL gaps remain.",
    )
    p.add_argument(
        "--require-no-official-source-gaps",
        action="store_true",
        default=False,
        help="Return non-green when any feature source lacks an official URL.",
    )
    p.add_argument(
        "--require-no-portable-context-gaps",
        action="store_true",
        default=False,
        help=(
            "Return non-green when portable state/local source samples are missing "
            "legislative body or session IDs."
        ),
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the source URL audit verification payload.",
    )


def _add_verify_prediction_backtest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-backtest",
        help="Verify a prediction-backtest artifact and its semantic-cache metadata.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-backtest JSON artifact written by --output.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Require cutoff, semantic-cache, and source-state run_metadata.",
    )
    p.add_argument(
        "--require-evaluated-predictions",
        action="store_true",
        default=False,
        help="Return non-green when the backtest has zero evaluated predictions.",
    )
    p.add_argument(
        "--require-prediction-source-urls",
        action="store_true",
        default=False,
        help="Return non-green when any prediction label is missing a source URL.",
    )
    p.add_argument(
        "--require-official-prediction-source-urls",
        action="store_true",
        default=False,
        help="Return non-green unless every prediction label source URL is official.",
    )
    p.add_argument(
        "--require-congress-archive-manifest",
        action="store_true",
        default=False,
        help="Return non-green unless run_metadata records a readable Congress archive manifest with matching SHA-256.",
    )
    p.add_argument(
        "--require-bill-semantics-cache",
        action="store_true",
        default=False,
        help="Return non-green unless the backtest artifact records a bill-semantics cache.",
    )
    p.add_argument(
        "--require-bill-semantics-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Return non-green unless the backtest's bill-semantics cache contains this model name; repeatable.",
    )
    p.add_argument(
        "--require-bill-semantics-source-inputs-sha256",
        action="store_true",
        default=False,
        help="Return non-green unless the backtest's bill-semantics cache records source_inputs_sha256.",
    )
    p.add_argument(
        "--require-model-name",
        default=None,
        metavar="NAME",
        help="Return non-green unless the backtest artifact has this model_name.",
    )
    p.add_argument(
        "--require-ontology-feature-signals",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless ontology predictions carry every required "
            "feature family with official URL-backed anchors, with optional loaded "
            "signals marked present or unavailable."
        ),
    )
    p.add_argument(
        "--require-source-family",
        dest="require_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Return non-green unless the backtest source_family_ids include FAMILY. "
            "Repeat for multiple families."
        ),
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the backtest verification audit payload.",
    )


def _add_prediction_offline_readiness_summary(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-offline-readiness-summary",
        help="Summarize local prediction readiness from existing verifier artifacts.",
    )
    p.add_argument(
        "--benchmark-verify",
        required=True,
        metavar="PATH",
        help="Path to a verify-prediction-benchmark output artifact.",
    )
    p.add_argument(
        "--backfill-runtime-verify",
        required=True,
        metavar="PATH",
        help="Path to a verify-prediction-backfill-plan runtime-check artifact.",
    )
    p.add_argument(
        "--backfill-plan",
        default=None,
        metavar="PATH",
        help="Optional prediction-backfill-plan artifact used to inline suggested commands by step.",
    )
    p.add_argument(
        "--env-preflight",
        default=None,
        metavar="PATH",
        help="Optional runtime-env-preflight artifact to include in the readiness snapshot.",
    )
    p.add_argument(
        "--env-preflight-verify",
        default=None,
        metavar="PATH",
        help="Optional verify-runtime-env-preflight artifact to gate env preflight quality.",
    )
    p.add_argument(
        "--congress-load-summary",
        default=None,
        metavar="PATH",
        help="Optional load-congress/load-congress-local artifact to include as a core Congress input check.",
    )
    p.add_argument(
        "--require-congress-load-summary",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless Congress member, bill, and vote prediction inputs "
            "are proven by --congress-load-summary."
        ),
    )
    p.add_argument(
        "--fec-inputs-verify",
        default=None,
        metavar="PATH",
        help="Optional verify-fec-inputs artifact to include as an offline input check.",
    )
    p.add_argument(
        "--public-statement-rows-verify",
        default=None,
        metavar="PATH",
        help="Optional verify-public-statement-rows artifact to include as an offline input check.",
    )
    p.add_argument(
        "--require-eval-window-run",
        action="store_true",
        default=False,
        help="Return non-green unless the benchmark verifier includes a green eval-window run component.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the offline readiness summary payload.",
    )
    p.add_argument(
        "--resume-script-output",
        default=None,
        metavar="PATH",
        help="Optional shell script path containing secret-free resume commands grouped by unlock phase.",
    )


def _add_verify_prediction_offline_readiness_summary(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-offline-readiness-summary",
        help="Verify a prediction-offline-readiness-summary artifact.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-offline-readiness-summary JSON artifact.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green when run_metadata or source_state is missing/stale.",
    )
    p.add_argument(
        "--require-env-preflight-verify",
        action="store_true",
        help="Return non-green when the embedded env-preflight verifier is absent or failed.",
    )
    p.add_argument(
        "--require-eval-window-run",
        action="store_true",
        help="Return non-green when the embedded benchmark eval-window run summary is absent or failed.",
    )
    p.add_argument(
        "--require-resume-script-match",
        action="store_true",
        help="Return non-green when the generated resume script is missing or stale.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the readiness artifact appears to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the offline-readiness verification payload.",
    )


def _add_verify_prediction_resume_script(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-resume-script",
        help="Verify a generated prediction resume script against its readiness summary.",
    )
    p.add_argument(
        "--readiness-summary",
        required=True,
        metavar="PATH",
        help="Path to the prediction-offline-readiness-summary artifact.",
    )
    p.add_argument(
        "--readiness-summary-verify",
        default=None,
        metavar="PATH",
        help="Optional verify-prediction-offline-readiness-summary artifact to gate the summary itself.",
    )
    p.add_argument(
        "--script",
        required=True,
        metavar="PATH",
        help="Path to the generated prediction resume shell script.",
    )
    p.add_argument(
        "--require-env-guards",
        action="store_true",
        help="Return non-green if any env-gated phase lacks its shell guard.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the script appears to contain literal secrets.",
    )
    p.add_argument(
        "--require-safe-commands",
        action="store_true",
        help="Return non-green if executable lines are not approved OpenPact runtime commands.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the resume-script verification payload.",
    )
