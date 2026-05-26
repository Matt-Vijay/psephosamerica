from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from src.prediction.window_plan import (
    PredictionEvalWindowPayload,
    PredictionEvalWindowPlanPayload,
    build_prediction_eval_window_plan,
)


def test_build_prediction_eval_window_plan_skips_windows_after_max_feature_cutoff() -> None:
    plan = build_prediction_eval_window_plan(
        start_label_year=2024,
        end_label_year=2026,
        train_years=2,
        label_years=1,
        max_feature_cutoff=dt.date(2024, 12, 31),
        output_dir="out/prediction",
        bill_semantics_root="out/bill-semantics",
        plan_artifact_path="out/prediction/prediction-eval-window-plan.json",
    )

    assert plan.plan_version == "openpact-prediction-eval-window-plan-v1"
    assert plan.window_count == 2
    assert [window.window_id for window in plan.windows] == [
        "train-2022-2023__eval-2024-2024",
        "train-2023-2024__eval-2025-2025",
    ]
    assert (
        plan.summary_command == "python3 -m src.runtime.main prediction-eval-window-summary "
        "--report out/prediction/prediction-eval-report-train-2022-2023__eval-2024-2024.json "
        "--report out/prediction/prediction-eval-report-train-2023-2024__eval-2025-2025.json "
        "--require-report-run-metadata "
        "--require-non-overlapping-label-windows "
        "--output out/prediction/prediction-eval-window-summary.json"
    )
    assert (
        plan.verify_summary_command
        == "python3 -m src.runtime.main verify-prediction-eval-window-summary "
        "--artifact out/prediction/prediction-eval-window-summary.json "
        "--plan out/prediction/prediction-eval-window-plan.json "
        "--require-plan-match "
        "--require-run-metadata "
        "--require-current-report-hashes "
        "--require-non-overlapping-label-windows "
        "--min-window-count 2 "
        "--output out/prediction/prediction-eval-window-summary-verify.json"
    )
    assert (
        plan.verify_run_command == "python3 -m src.runtime.main verify-prediction-eval-window-run "
        "--plan out/prediction/prediction-eval-window-plan.json "
        "--summary-verify out/prediction/prediction-eval-window-summary-verify.json "
        "--require-window-verifiers "
        "--require-summary-verify "
        "--output out/prediction/prediction-eval-window-run-verify.json"
    )

    first = plan.windows[0]
    assert first.training_feature_cutoff == dt.date(2021, 12, 31)
    assert first.train_start == dt.date(2022, 1, 1)
    assert first.train_end == dt.date(2023, 12, 31)
    assert first.feature_cutoff == dt.date(2023, 12, 31)
    assert first.label_start == dt.date(2024, 1, 1)
    assert first.label_end == dt.date(2024, 12, 31)
    assert (
        first.eval_report_command == "python3 -m src.runtime.main prediction-eval-report "
        "--training-feature-cutoff 2021-12-31 "
        "--train-start 2022-01-01 "
        "--train-end 2023-12-31 "
        "--feature-cutoff 2023-12-31 "
        "--label-start 2024-01-01 "
        "--label-end 2024-12-31 "
        "--fail-on-unknown-bill-semantic-availability "
        "--fail-on-unknown-bill-signal-availability "
        "--fail-on-unknown-ontology-edge-availability "
        "--fail-on-unknown-contribution-signal-availability "
        "--fail-on-unknown-statement-signal-availability "
        "--output out/prediction/prediction-eval-report-train-2022-2023__eval-2024-2024.json "
        "--dataset-output out/prediction/prediction-eval-dataset-train-2022-2023__eval-2024-2024.json "
        "--manifest-output out/prediction/prediction-eval-manifest-train-2022-2023__eval-2024-2024.json "
        "--bill-semantics-root out/bill-semantics"
    )
    assert (
        first.input_inventory_command == "python3 -m src.runtime.main prediction-input-inventory "
        "--training-feature-cutoff 2021-12-31 "
        "--train-start 2022-01-01 "
        "--train-end 2023-12-31 "
        "--feature-cutoff 2023-12-31 "
        "--label-start 2024-01-01 "
        "--label-end 2024-12-31 "
        "--output out/prediction/prediction-input-inventory-train-2022-2023__eval-2024-2024.json"
    )
    assert (
        first.eval_manifest_verify_command
        == "python3 -m src.runtime.main verify-prediction-eval-manifest "
        "--manifest out/prediction/prediction-eval-manifest-train-2022-2023__eval-2024-2024.json "
        "--require-artifact-run-metadata "
        "--require-model-name member_vote_rate_baseline "
        "--require-model-name ontology_signal_model "
        "--require-model-name learned_signal_logistic "
        "--require-ontology-feature-signals "
        "--min-training-feature-source-url-coverage-rate 1 "
        "--min-training-feature-official-source-coverage-rate 1 "
        "--min-evaluation-feature-source-url-coverage-rate 1 "
        "--min-evaluation-feature-official-source-coverage-rate 1 "
        "--min-evaluation-source-url-coverage-rate 1 "
        "--require-fail-on-unknown-bill-semantic-availability "
        "--require-fail-on-unknown-bill-signal-availability "
        "--require-fail-on-unknown-ontology-edge-availability "
        "--require-fail-on-unknown-contribution-signal-availability "
        "--require-fail-on-unknown-statement-signal-availability "
        "--output out/prediction/prediction-eval-manifest-verify-train-2022-2023__eval-2024-2024.json"
    )
    assert (
        first.input_inventory_verify_command
        == "python3 -m src.runtime.main verify-prediction-input-inventory "
        "--artifact out/prediction/prediction-input-inventory-train-2022-2023__eval-2024-2024.json "
        "--require-run-metadata "
        "--require-portable-jurisdiction-ids "
        "--require-portable-body-ids "
        "--require-portable-session-ids "
        "--require-source-family congress_vote "
        "--require-source-family congress_bill "
        "--min-training-labels 1 "
        "--min-evaluation-labels 1 "
        "--min-training-feature-vote-history-source-coverage-rate 1 "
        "--min-evaluation-feature-vote-history-source-coverage-rate 1 "
        "--min-training-label-official-source-url-coverage-rate 1 "
        "--min-evaluation-label-official-source-url-coverage-rate 1 "
        "--min-bill-official-source-url-coverage-rate 1 "
        "--min-bill-sponsor-availability-rate 1 "
        "--require-no-bill-sponsor-introduced-date-fallbacks "
        "--min-ontology-official-source-anchor-coverage-rate 1 "
        "--min-fec-contributions 1 "
        "--min-member-attributed-fec-contributions 1 "
        "--min-members-with-fec-candidate-id 1 "
        "--min-public-statement-signals 1 "
        "--min-members-with-public-statement-signals 1 "
        "--output out/prediction/prediction-input-inventory-verify-train-2022-2023__eval-2024-2024.json"
    )


def test_build_prediction_eval_window_plan_quotes_paths_with_spaces() -> None:
    plan = build_prediction_eval_window_plan(
        start_label_year=2025,
        end_label_year=2025,
        output_dir="out/prediction windows",
        bill_semantics_root="out/bill semantics",
        plan_artifact_path="out/prediction windows/window plan.json",
    )

    command = plan.windows[0].eval_report_command
    assert "--output 'out/prediction windows/prediction-eval-report-" in command
    assert "--bill-semantics-root 'out/bill semantics'" in command
    assert (
        "'out/prediction windows/prediction-eval-manifest-verify-"
        in plan.windows[0].eval_manifest_verify_command
    )
    assert (
        "'out/prediction windows/prediction-input-inventory-verify-"
        in plan.windows[0].input_inventory_verify_command
    )
    assert "'out/prediction windows/prediction-eval-window-summary.json'" in plan.summary_command
    assert (
        "'out/prediction windows/prediction-eval-window-summary-verify.json'"
        in plan.verify_summary_command
    )
    assert "--plan 'out/prediction windows/window plan.json'" in plan.verify_summary_command
    assert "--plan 'out/prediction windows/window plan.json'" in plan.verify_run_command
    assert (
        "'out/prediction windows/prediction-eval-window-run-verify.json'" in plan.verify_run_command
    )


def test_build_prediction_eval_window_plan_rejects_invalid_years() -> None:
    with pytest.raises(ValueError, match="start_label_year"):
        build_prediction_eval_window_plan(start_label_year=2026, end_label_year=2025)

    with pytest.raises(ValueError, match="train_years"):
        build_prediction_eval_window_plan(
            start_label_year=2025,
            end_label_year=2025,
            train_years=0,
        )


def test_prediction_eval_window_plan_payload_rejects_mismatched_windows() -> None:
    window = PredictionEvalWindowPayload(
        window_id="train-2023-2024__eval-2025-2025",
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        eval_report_command="eval",
        input_inventory_command="inventory",
        input_inventory_verify_command="verify-inventory",
        eval_manifest_verify_command="verify-manifest",
    )

    with pytest.raises(ValidationError, match="window_count"):
        PredictionEvalWindowPlanPayload(
            start_label_year=2025,
            end_label_year=2025,
            window_count=0,
            windows=[window],
            summary_command="summary",
            verify_summary_command="verify-summary",
            verify_run_command="verify-run",
        )


def test_prediction_eval_window_payload_rejects_leaky_cutoff() -> None:
    with pytest.raises(ValidationError, match="feature_cutoff"):
        PredictionEvalWindowPayload(
            window_id="bad",
            training_feature_cutoff=dt.date(2022, 12, 31),
            train_start=dt.date(2023, 1, 1),
            train_end=dt.date(2025, 1, 1),
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            eval_report_command="eval",
            input_inventory_command="inventory",
            input_inventory_verify_command="verify-inventory",
            eval_manifest_verify_command="verify-manifest",
        )
