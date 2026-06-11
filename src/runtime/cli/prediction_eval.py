"""Prediction evaluation and benchmark subcommands."""

from __future__ import annotations

import argparse
import datetime

from src.runtime.cli._shared import _parse_date


def _add_prediction_eval_report(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-eval-report",
        help="Compare baseline, ontology, and learned vote predictors on temporal windows.",
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
        "--bill-semantics-root",
        default=None,
        metavar="PATH",
        help="Optional materialized bill-semantics root for ontology model features.",
    )
    p.add_argument(
        "--congress-archive-manifest",
        default=None,
        metavar="PATH",
        help="Optional Congress archive manifest to record in eval report provenance.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the integrated prediction evaluation report.",
    )
    p.add_argument(
        "--dataset-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the standalone cutoff-safe train/eval dataset.",
    )
    p.add_argument(
        "--manifest-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for an auditable prediction-eval run manifest.",
    )
    p.add_argument(
        "--strict-readiness",
        action="store_true",
        default=False,
        help="Return non-green unless the report readiness status is ready.",
    )
    p.add_argument(
        "--min-training-examples",
        type=int,
        default=None,
        metavar="N",
        help="Optional minimum training example count required for a green report.",
    )
    p.add_argument(
        "--min-evaluation-examples",
        type=int,
        default=None,
        metavar="N",
        help="Optional minimum evaluation example count required for a green report.",
    )
    p.add_argument(
        "--min-bill-semantic-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum bill-semantic coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-bill-metadata-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum loaded-bill-metadata coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-training-bill-semantic-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum training bill-semantic coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-evaluation-bill-semantic-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum evaluation bill-semantic coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-training-feature-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum training feature-source coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-evaluation-feature-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum evaluation feature-source coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-training-feature-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Optional minimum training URL-backed feature-source coverage rate "
            "required for a green report."
        ),
    )
    p.add_argument(
        "--min-training-feature-official-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Optional minimum training official-source-backed feature coverage "
            "rate required for a green report."
        ),
    )
    p.add_argument(
        "--min-evaluation-feature-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Optional minimum evaluation URL-backed feature-source coverage rate "
            "required for a green report."
        ),
    )
    p.add_argument(
        "--min-evaluation-feature-official-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Optional minimum evaluation official-source-backed feature coverage "
            "rate required for a green report."
        ),
    )
    p.add_argument(
        "--min-evaluation-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum evaluation label source-url coverage required for a green report.",
    )
    p.add_argument(
        "--fail-on-unknown-bill-semantic-availability",
        action="store_true",
        default=False,
        help=("Return non-green when any cached bill semantic payload lacks an available_at date."),
    )
    p.add_argument(
        "--fail-on-unknown-bill-signal-availability",
        action="store_true",
        default=False,
        help="Return non-green when any bill signal row lacks cutoff-safe availability dates.",
    )
    p.add_argument(
        "--fail-on-unknown-ontology-edge-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green when any ontology edge lacks a timestamp usable "
            "for cutoff-safe evaluation."
        ),
    )
    p.add_argument(
        "--fail-on-unknown-contribution-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green when any contribution signal row lacks a cutoff-safe "
            "contribution_date, date, or as_of_date timestamp."
        ),
    )
    p.add_argument(
        "--fail-on-unknown-statement-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green when any public-statement signal row lacks a cutoff-safe "
            "statement_date, date, or as_of_date timestamp."
        ),
    )
    p.add_argument(
        "--fail-on-mixed-bill-semantics-models",
        action="store_true",
        default=False,
        help="Return non-green when a bill-semantics cache contains multiple model names.",
    )


def _add_prediction_eval_window_plan(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-eval-window-plan",
        help="Plan annual cutoff-safe prediction eval report windows.",
    )
    p.add_argument(
        "--start-label-year",
        type=int,
        default=2024,
        metavar="YEAR",
        help="First evaluation label calendar year to include.",
    )
    p.add_argument(
        "--end-label-year",
        type=int,
        default=2026,
        metavar="YEAR",
        help="Last evaluation label calendar year to include.",
    )
    p.add_argument(
        "--train-years",
        type=int,
        default=2,
        metavar="N",
        help="Number of historical label years to train on before each eval year.",
    )
    p.add_argument(
        "--label-years",
        type=int,
        default=1,
        metavar="N",
        help="Number of evaluation label years per planned window.",
    )
    p.add_argument(
        "--max-feature-cutoff",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Skip windows whose feature cutoff is after this date.",
    )
    p.add_argument(
        "--output-dir",
        default="out",
        metavar="PATH",
        help="Directory embedded in each planned output command.",
    )
    p.add_argument(
        "--bill-semantics-root",
        default=None,
        metavar="PATH",
        help="Optional bill-semantics root embedded in planned eval report commands.",
    )
    p.add_argument(
        "--congress-archive-manifest",
        default=None,
        metavar="PATH",
        help="Optional Congress archive manifest embedded in planned inventory/eval commands.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the window plan payload.",
    )


def _add_verify_prediction_eval_window_plan(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "verify-prediction-eval-window-plan",
        help="Verify a prediction-eval-window-plan artifact before running planned windows.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-eval-window-plan JSON artifact.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Return non-green when the plan artifact lacks matching run metadata.",
    )
    p.add_argument(
        "--require-commands",
        action="store_true",
        default=False,
        help="Return non-green when planned eval/inventory commands are incomplete.",
    )
    p.add_argument(
        "--min-window-count",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when the plan contains fewer than N windows.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the window-plan verification payload.",
    )


def _add_prediction_eval_window_summary(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "prediction-eval-window-summary",
        help="Aggregate multiple prediction-eval-report artifacts into one longitudinal summary.",
    )
    p.add_argument(
        "--report",
        action="append",
        required=True,
        metavar="PATH",
        help="Prediction eval report artifact to aggregate. Repeat for each window.",
    )
    p.add_argument(
        "--require-report-run-metadata",
        action="store_true",
        default=False,
        help="Return non-green when any report lacks prediction-eval-report run metadata.",
    )
    p.add_argument(
        "--require-ready-reports",
        action="store_true",
        default=False,
        help="Return non-green when any report readiness status is not ready.",
    )
    p.add_argument(
        "--require-non-overlapping-label-windows",
        action="store_true",
        default=False,
        help="Return non-green when report label windows overlap.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the aggregated window summary.",
    )


def _add_verify_prediction_eval_window_summary(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "verify-prediction-eval-window-summary",
        help="Verify a prediction-eval-window-summary artifact.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-eval-window-summary JSON artifact.",
    )
    p.add_argument(
        "--plan",
        default=None,
        metavar="PATH",
        help="Optional prediction-eval-window-plan artifact to compare against.",
    )
    p.add_argument(
        "--require-plan-match",
        action="store_true",
        default=False,
        help="Return non-green unless the summary windows match --plan.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Return non-green when the summary lacks provenance metadata.",
    )
    p.add_argument(
        "--require-current-report-hashes",
        action="store_true",
        default=False,
        help="Return non-green when source report hashes no longer match disk.",
    )
    p.add_argument(
        "--require-non-overlapping-label-windows",
        action="store_true",
        default=False,
        help="Return non-green when summarized label windows overlap.",
    )
    p.add_argument(
        "--min-window-count",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when the summary has fewer than N windows.",
    )
    p.add_argument(
        "--min-total-evaluation-labels",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when the summary has fewer than N evaluation labels.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the window-summary verification payload.",
    )


def _add_verify_prediction_eval_window_run(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "verify-prediction-eval-window-run",
        help="Verify all expected artifacts from a prediction-eval-window-plan run.",
    )
    p.add_argument(
        "--plan",
        required=True,
        metavar="PATH",
        help="Path to a prediction-eval-window-plan JSON artifact.",
    )
    p.add_argument(
        "--summary-verify",
        default=None,
        metavar="PATH",
        help=(
            "Optional verify-prediction-eval-window-summary artifact. Defaults to the "
            "plan verify_summary_command --output path."
        ),
    )
    p.add_argument(
        "--require-window-verifiers",
        action="store_true",
        default=False,
        help="Return non-green unless every per-window verifier artifact exists and is passing.",
    )
    p.add_argument(
        "--require-summary-verify",
        action="store_true",
        default=False,
        help="Return non-green unless the summary verifier artifact exists and is passing.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the window-run verification payload.",
    )


def _add_verify_prediction_eval_manifest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-eval-manifest",
        help="Verify prediction eval report/dataset artifacts against a run manifest.",
    )
    p.add_argument(
        "--manifest",
        required=True,
        metavar="PATH",
        help="Path to a prediction-eval manifest written by --manifest-output.",
    )
    p.add_argument(
        "--require-artifact-run-metadata",
        action="store_true",
        default=False,
        help="Require report and dataset artifacts to include auditable run_metadata.",
    )
    p.add_argument(
        "--require-congress-archive-manifest",
        action="store_true",
        default=False,
        help="Require eval manifest inputs to record a readable Congress archive manifest with matching SHA-256.",
    )
    p.add_argument(
        "--require-ready-quality",
        action="store_true",
        default=False,
        help="Return non-green unless manifest quality is ready and warning-free.",
    )
    p.add_argument(
        "--require-failure-analysis",
        action="store_true",
        default=False,
        help="Return non-green unless the manifest carries failure-analysis counts and top groups.",
    )
    p.add_argument(
        "--require-backfill-recommendations",
        action="store_true",
        default=False,
        help="Return non-green unless failure analysis includes at least one backfill recommendation.",
    )
    p.add_argument(
        "--require-fail-on-unknown-bill-semantic-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the manifest thresholds prove eval was run "
            "with unknown bill-semantic availability as a failure."
        ),
    )
    p.add_argument(
        "--require-fail-on-unknown-bill-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the manifest thresholds prove eval was run "
            "with unknown bill-signal availability as a failure."
        ),
    )
    p.add_argument(
        "--require-fail-on-unknown-ontology-edge-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the manifest thresholds prove eval was run "
            "with unknown ontology-edge availability as a failure."
        ),
    )
    p.add_argument(
        "--require-fail-on-unknown-contribution-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the manifest thresholds prove eval was run "
            "with unknown contribution-signal availability as a failure."
        ),
    )
    p.add_argument(
        "--require-fail-on-unknown-statement-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the manifest thresholds prove eval was run "
            "with unknown statement-signal availability as a failure."
        ),
    )
    p.add_argument(
        "--require-bill-semantics-cache",
        action="store_true",
        default=False,
        help="Return non-green unless the manifest records a bill-semantics cache root and index hash.",
    )
    p.add_argument(
        "--require-bill-semantics-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Return non-green unless the manifest's bill-semantics cache contains this model name; repeatable.",
    )
    p.add_argument(
        "--require-bill-semantics-source-inputs-sha256",
        action="store_true",
        default=False,
        help="Return non-green unless the manifest's bill-semantics cache records source_inputs_sha256.",
    )
    p.add_argument(
        "--require-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Require a model_name to appear in the eval report artifact; repeatable.",
    )
    p.add_argument(
        "--require-ontology-feature-signals",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval dataset feature matrix includes required "
            "ontology feature families, train/eval official URL-backed anchors, and learned "
            "coefficients for each family."
        ),
    )
    p.add_argument(
        "--require-source-family",
        dest="require_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Return non-green unless the eval report source_family_ids include FAMILY. "
            "Repeat for multiple families."
        ),
    )
    p.add_argument(
        "--min-bill-semantic-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest bill semantic coverage is below RATE.",
    )
    _add_prediction_eval_manifest_coverage_threshold_args(p)
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the eval-manifest verification audit payload.",
    )


def _add_verify_prediction_benchmark(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-benchmark",
        help="Run strict verification over a complete prediction benchmark artifact set.",
    )
    p.add_argument(
        "--inventory",
        required=True,
        metavar="PATH",
        help="Path to a prediction-input-inventory artifact.",
    )
    p.add_argument(
        "--backtest",
        required=True,
        metavar="PATH",
        help="Path to a prediction-backtest artifact.",
    )
    p.add_argument(
        "--eval-manifest",
        required=True,
        metavar="PATH",
        help="Path to a prediction-eval manifest artifact.",
    )
    p.add_argument(
        "--bill-semantics-plan",
        required=True,
        metavar="PATH",
        help="Path to a dry-run bill-semantics target plan.",
    )
    p.add_argument(
        "--source-url-audit",
        default=None,
        metavar="PATH",
        help="Optional prediction-source-url-audit artifact to verify with the benchmark bundle.",
    )
    p.add_argument(
        "--eval-window-run-verify",
        default=None,
        metavar="PATH",
        help=(
            "Optional verify-prediction-eval-window-run artifact proving rolling "
            "cutoff-window benchmark execution."
        ),
    )
    p.add_argument(
        "--require-eval-window-run-verify",
        action="store_true",
        default=False,
        help="Return non-green unless --eval-window-run-verify is provided and still verifies.",
    )
    p.add_argument(
        "--require-source-url-audit",
        action="store_true",
        default=False,
        help="Return non-green unless --source-url-audit is provided and verifies.",
    )
    p.add_argument(
        "--require-source-url-audit-no-gaps",
        action="store_true",
        default=False,
        help="Return non-green when the source URL audit contains URL gaps.",
    )
    p.add_argument(
        "--require-source-url-audit-no-official-source-gaps",
        action="store_true",
        default=False,
        help="Return non-green when the source URL audit contains official-source gaps.",
    )
    p.add_argument(
        "--require-source-url-audit-no-portable-context-gaps",
        action="store_true",
        default=False,
        help=(
            "Return non-green when the source URL audit contains portable "
            "state/local samples missing legislative body or session IDs."
        ),
    )
    p.add_argument(
        "--bill-semantics-root",
        default=None,
        metavar="PATH",
        help="Optional bill-semantics cache root to verify with the benchmark bundle.",
    )
    p.add_argument(
        "--fail-on-unmatched-targets",
        action="store_true",
        default=False,
        help="Return non-green when the bill-semantics plan has unmatched targets.",
    )
    p.add_argument(
        "--require-bill-semantics-plan-source-anchors",
        action="store_true",
        default=False,
        help="Return non-green when matched bill-semantics plan targets lack source anchors.",
    )
    p.add_argument(
        "--require-bill-semantics-cache",
        action="store_true",
        default=False,
        help="Return non-green unless --bill-semantics-root is provided and verifies.",
    )
    p.add_argument(
        "--require-bill-semantics-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Return non-green unless the verified semantic cache contains this model name; repeatable.",
    )
    p.add_argument(
        "--require-bill-semantics-source-inputs-sha256",
        action="store_true",
        default=False,
        help="Return non-green unless the semantic cache index records source_inputs_sha256.",
    )
    p.add_argument(
        "--require-clean-inventory",
        action="store_true",
        default=False,
        help="Return non-green when inventory has warning or blocking reasons.",
    )
    p.add_argument(
        "--require-inventory-congress-archive-manifest",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the inventory artifact records a Congress archive "
            "manifest path with a matching SHA-256."
        ),
    )
    p.add_argument(
        "--require-portable-jurisdiction-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when the inventory has portable legislative source "
            "rows without explicit jurisdiction_id values."
        ),
    )
    p.add_argument(
        "--require-portable-body-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when the inventory has portable non-Congress rows "
            "without explicit legislative_body_id values."
        ),
    )
    p.add_argument(
        "--require-portable-session-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when the inventory has portable non-Congress rows "
            "without explicit legislative_session_id values."
        ),
    )
    p.add_argument(
        "--require-source-family",
        dest="require_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Forward a required inventory source family to "
            "verify-prediction-input-inventory. Repeat for multiple families."
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
        help=(
            "Return non-green when inventory training feature vote-history source "
            "coverage is below RATE."
        ),
    )
    p.add_argument(
        "--min-evaluation-feature-vote-history-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Return non-green when inventory evaluation feature vote-history source "
            "coverage is below RATE."
        ),
    )
    p.add_argument(
        "--min-training-label-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory training label source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-evaluation-label-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory evaluation label source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-training-label-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory training label official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-evaluation-label-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory evaluation label official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory bill source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory bill official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-sponsor-availability-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Return non-green when inventory cutoff-available bill sponsor coverage is below RATE."
        ),
    )
    p.add_argument(
        "--require-no-bill-sponsor-introduced-date-fallbacks",
        action="store_true",
        default=False,
        help=(
            "Return non-green when inventory sponsor availability depends on "
            "primary-sponsor introduced_date fallback instead of explicit sponsor dates."
        ),
    )
    p.add_argument(
        "--min-ontology-source-anchor-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory ontology source-anchor coverage is below RATE.",
    )
    p.add_argument(
        "--min-ontology-official-source-anchor-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory ontology official source-anchor coverage is below RATE.",
    )
    p.add_argument(
        "--min-fec-member-attribution-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory FEC member attribution is below RATE.",
    )
    p.add_argument(
        "--min-fec-contributions",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory FEC contribution rows are fewer than N.",
    )
    p.add_argument(
        "--min-member-attributed-fec-contributions",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory member-attributed FEC contribution rows are fewer than N.",
    )
    p.add_argument(
        "--min-members-with-fec-candidate-id",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory members with FEC candidate IDs are fewer than N.",
    )
    p.add_argument(
        "--min-public-statement-signals",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory public-statement signal rows are fewer than N.",
    )
    p.add_argument(
        "--min-members-with-public-statement-signals",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory members with public-statement signals are fewer than N.",
    )
    p.add_argument(
        "--require-evaluated-backtest",
        action="store_true",
        default=False,
        help="Return non-green when the backtest has zero evaluated predictions.",
    )
    p.add_argument(
        "--require-backtest-source-urls",
        action="store_true",
        default=False,
        help="Return non-green when the bundled backtest has prediction labels without source URLs.",
    )
    p.add_argument(
        "--require-backtest-official-source-urls",
        action="store_true",
        default=False,
        help="Return non-green unless bundled backtest prediction source URLs are official.",
    )
    p.add_argument(
        "--require-backtest-congress-archive-manifest",
        action="store_true",
        default=False,
        help="Forward Congress archive manifest provenance checks to verify-prediction-backtest.",
    )
    p.add_argument(
        "--require-backtest-model-name",
        default=None,
        metavar="NAME",
        help="Return non-green unless the bundled backtest has this model_name.",
    )
    p.add_argument(
        "--require-backtest-ontology-feature-signals",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless bundled ontology backtest predictions carry required "
            "feature families with official URL-backed anchors."
        ),
    )
    p.add_argument(
        "--require-backtest-source-family",
        dest="require_backtest_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Forward a required backtest source family to verify-prediction-backtest. "
            "Repeat for multiple families."
        ),
    )
    p.add_argument(
        "--require-ready-quality",
        action="store_true",
        default=False,
        help="Return non-green unless the eval manifest quality block is ready.",
    )
    p.add_argument(
        "--require-eval-failure-analysis",
        action="store_true",
        default=False,
        help="Return non-green unless the eval manifest carries failure-analysis counts and top groups.",
    )
    p.add_argument(
        "--require-eval-backfill-recommendations",
        action="store_true",
        default=False,
        help="Return non-green unless the eval manifest includes at least one backfill recommendation.",
    )
    p.add_argument(
        "--require-eval-congress-archive-manifest",
        action="store_true",
        default=False,
        help="Forward Congress archive manifest provenance checks to verify-prediction-eval-manifest.",
    )
    p.add_argument(
        "--require-eval-fail-on-unknown-bill-semantic-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval manifest proves unknown "
            "bill-semantic availability was configured as a failure."
        ),
    )
    p.add_argument(
        "--require-eval-fail-on-unknown-bill-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval manifest proves unknown "
            "bill-signal availability was configured as a failure."
        ),
    )
    p.add_argument(
        "--require-eval-fail-on-unknown-ontology-edge-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval manifest proves unknown "
            "ontology-edge availability was configured as a failure."
        ),
    )
    p.add_argument(
        "--require-eval-fail-on-unknown-contribution-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval manifest proves unknown "
            "contribution-signal availability was configured as a failure."
        ),
    )
    p.add_argument(
        "--require-eval-fail-on-unknown-statement-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval manifest proves unknown "
            "statement-signal availability was configured as a failure."
        ),
    )
    p.add_argument(
        "--require-eval-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Require a model_name to appear in the eval report artifact; repeatable.",
    )
    p.add_argument(
        "--require-eval-ontology-feature-signals",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval dataset feature matrix includes required "
            "ontology feature families, train/eval official URL-backed anchors, and learned "
            "coefficients for each family."
        ),
    )
    p.add_argument(
        "--require-eval-source-family",
        dest="require_eval_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Forward a required eval-manifest source family to "
            "verify-prediction-eval-manifest. Repeat for multiple families."
        ),
    )
    p.add_argument(
        "--min-bill-semantic-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when the eval manifest bill semantic coverage is below RATE.",
    )
    _add_prediction_eval_manifest_coverage_threshold_args(p)
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the benchmark verification audit payload.",
    )
    p.add_argument(
        "--backfill-plan-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the standalone benchmark backfill plan.",
    )
    p.add_argument(
        "--check-backfill-runtime-requirements",
        action="store_true",
        default=False,
        help="Return non-green when generated backfill-plan env/file prerequisites are absent locally.",
    )


def _add_verify_prediction_backfill_plan(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-backfill-plan",
        help="Verify a benchmark backfill_plan artifact or embedded benchmark plan.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a verify-prediction-benchmark artifact or standalone backfill plan.",
    )
    p.add_argument(
        "--require-source-requirements",
        action="store_true",
        default=False,
        help="Return non-green when any backfill step lacks source requirements.",
    )
    p.add_argument(
        "--require-runtime-requirements",
        action="store_true",
        default=False,
        help="Return non-green when any backfill step lacks env/file runtime requirements.",
    )
    p.add_argument(
        "--check-runtime-requirements",
        action="store_true",
        default=False,
        help="Return non-green when listed env/file runtime requirements are missing locally.",
    )
    p.add_argument(
        "--require-supported-suggested-commands",
        action="store_true",
        default=False,
        help="Return non-green when supported backfill actions lack suggested commands.",
    )
    p.add_argument(
        "--require-blocker-links",
        action="store_true",
        default=False,
        help="Return non-green when semantic materialization is not blocked by missing bill metadata it depends on.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Return non-green when the backfill-plan artifact lacks provenance metadata.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the backfill-plan verification audit payload.",
    )


def _add_prediction_eval_manifest_coverage_threshold_args(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--min-bill-metadata-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest bill metadata coverage is below RATE.",
    )
    parser.add_argument(
        "--min-training-feature-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest training feature source coverage is below RATE.",
    )
    parser.add_argument(
        "--min-evaluation-feature-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest evaluation feature source coverage is below RATE.",
    )
    parser.add_argument(
        "--min-training-feature-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest training feature source URL coverage is below RATE.",
    )
    parser.add_argument(
        "--min-training-feature-official-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest training feature official-source coverage is below RATE.",
    )
    parser.add_argument(
        "--min-evaluation-feature-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest evaluation feature source URL coverage is below RATE.",
    )
    parser.add_argument(
        "--min-evaluation-feature-official-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest evaluation feature official-source coverage is below RATE.",
    )
    parser.add_argument(
        "--min-evaluation-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest evaluation label source URL coverage is below RATE.",
    )
