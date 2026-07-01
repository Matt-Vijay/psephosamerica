from __future__ import annotations

from datetime import date
import shlex
from typing import Self

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


def _reject_boolean_int(value: object, field_name: str) -> object:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value


class PredictionEvalWindowPayload(BaseModel):
    """One cutoff-safe prediction eval window and the commands to run it."""

    window_id: str
    training_feature_cutoff: date
    train_start: date
    train_end: date
    feature_cutoff: date
    label_start: date
    label_end: date
    eval_report_command: str
    input_inventory_command: str
    input_inventory_verify_command: str
    eval_manifest_verify_command: str

    @model_validator(mode="after")
    def window_is_temporal(self) -> Self:
        if self.training_feature_cutoff >= self.train_start:
            raise ValueError("training_feature_cutoff must be before train_start")
        if self.train_start > self.train_end:
            raise ValueError("train_start must be on or before train_end")
        if self.train_end > self.feature_cutoff:
            raise ValueError("train_end must be on or before feature_cutoff")
        if self.feature_cutoff >= self.label_start:
            raise ValueError("feature_cutoff must be before label_start")
        if self.label_start > self.label_end:
            raise ValueError("label_start must be on or before label_end")
        return self


class PredictionEvalWindowPlanPayload(BaseModel):
    """Repeatable annual cutoff plan for prediction eval/report runs."""

    plan_version: str = "psephosamerica-prediction-eval-window-plan-v1"
    start_label_year: int
    end_label_year: int
    train_years: int = Field(default=2, ge=1)
    label_years: int = Field(default=1, ge=1)
    max_feature_cutoff: date | None = None
    window_count: int = Field(ge=0)
    windows: list[PredictionEvalWindowPayload] = Field(default_factory=list)
    summary_command: str
    verify_summary_command: str
    verify_run_command: str

    @field_validator(
        "start_label_year",
        "end_label_year",
        "train_years",
        "label_years",
        "window_count",
        mode="before",
    )
    @classmethod
    def counts_are_plain_ints(cls, value: object, info: ValidationInfo) -> object:
        return _reject_boolean_int(value, info.field_name or "count")

    @model_validator(mode="after")
    def plan_counts_match_windows(self) -> Self:
        if self.start_label_year > self.end_label_year:
            raise ValueError("start_label_year must be on or before end_label_year")
        if self.window_count != len(self.windows):
            raise ValueError("window_count must match windows")
        if [window.window_id for window in self.windows] != sorted(
            {window.window_id for window in self.windows}
        ):
            raise ValueError("windows must have sorted unique IDs")
        if self.max_feature_cutoff is not None and any(
            window.feature_cutoff > self.max_feature_cutoff for window in self.windows
        ):
            raise ValueError("windows must not exceed max_feature_cutoff")
        return self


def build_prediction_eval_window_plan(
    *,
    start_label_year: int,
    end_label_year: int,
    train_years: int = 2,
    label_years: int = 1,
    max_feature_cutoff: date | None = None,
    output_dir: str = "out",
    bill_semantics_root: str | None = None,
    congress_archive_manifest: str | None = None,
    plan_artifact_path: str | None = None,
) -> PredictionEvalWindowPlanPayload:
    """Build annual eval-report command windows for historical vote prediction tests."""
    if isinstance(start_label_year, bool) or isinstance(end_label_year, bool):
        raise ValueError("label years must be integers")
    if start_label_year > end_label_year:
        raise ValueError("start_label_year must be on or before end_label_year")
    if train_years < 1:
        raise ValueError("train_years must be positive")
    if label_years < 1:
        raise ValueError("label_years must be positive")

    windows: list[PredictionEvalWindowPayload] = []
    for label_year in range(start_label_year, end_label_year + 1):
        feature_cutoff = date(label_year - 1, 12, 31)
        if max_feature_cutoff is not None and feature_cutoff > max_feature_cutoff:
            continue
        label_start = date(label_year, 1, 1)
        label_end = date(label_year + label_years - 1, 12, 31)
        train_end = feature_cutoff
        train_start = date(label_year - train_years, 1, 1)
        training_feature_cutoff = date(train_start.year - 1, 12, 31)
        window_id = (
            f"train-{train_start.year}-{train_end.year}__eval-{label_start.year}-{label_end.year}"
        )
        windows.append(
            PredictionEvalWindowPayload(
                window_id=window_id,
                training_feature_cutoff=training_feature_cutoff,
                train_start=train_start,
                train_end=train_end,
                feature_cutoff=feature_cutoff,
                label_start=label_start,
                label_end=label_end,
                eval_report_command=_prediction_eval_report_command(
                    training_feature_cutoff=training_feature_cutoff,
                    train_start=train_start,
                    train_end=train_end,
                    feature_cutoff=feature_cutoff,
                    label_start=label_start,
                    label_end=label_end,
                    output_dir=output_dir,
                    window_id=window_id,
                    bill_semantics_root=bill_semantics_root,
                    congress_archive_manifest=congress_archive_manifest,
                ),
                input_inventory_command=_prediction_input_inventory_command(
                    training_feature_cutoff=training_feature_cutoff,
                    train_start=train_start,
                    train_end=train_end,
                    feature_cutoff=feature_cutoff,
                    label_start=label_start,
                    label_end=label_end,
                    output_dir=output_dir,
                    window_id=window_id,
                    congress_archive_manifest=congress_archive_manifest,
                ),
                input_inventory_verify_command=_prediction_input_inventory_verify_command(
                    output_dir=output_dir,
                    window_id=window_id,
                    require_congress_archive_manifest=congress_archive_manifest is not None,
                ),
                eval_manifest_verify_command=_prediction_eval_manifest_verify_command(
                    output_dir=output_dir,
                    window_id=window_id,
                    require_congress_archive_manifest=congress_archive_manifest is not None,
                ),
            )
        )
    return PredictionEvalWindowPlanPayload(
        start_label_year=start_label_year,
        end_label_year=end_label_year,
        train_years=train_years,
        label_years=label_years,
        max_feature_cutoff=max_feature_cutoff,
        window_count=len(windows),
        windows=windows,
        summary_command=_prediction_eval_window_summary_command(
            output_dir=output_dir,
            window_ids=[window.window_id for window in windows],
        ),
        verify_summary_command=_verify_prediction_eval_window_summary_command(
            output_dir=output_dir,
            window_count=len(windows),
            plan_artifact_path=plan_artifact_path,
        ),
        verify_run_command=_verify_prediction_eval_window_run_command(
            output_dir=output_dir,
            plan_artifact_path=plan_artifact_path,
        ),
    )


def _prediction_eval_report_command(
    *,
    training_feature_cutoff: date,
    train_start: date,
    train_end: date,
    feature_cutoff: date,
    label_start: date,
    label_end: date,
    output_dir: str,
    window_id: str,
    bill_semantics_root: str | None,
    congress_archive_manifest: str | None,
) -> str:
    parts = [
        "python3 -m src.runtime.main prediction-eval-report",
        f"--training-feature-cutoff {training_feature_cutoff.isoformat()}",
        f"--train-start {train_start.isoformat()}",
        f"--train-end {train_end.isoformat()}",
        f"--feature-cutoff {feature_cutoff.isoformat()}",
        f"--label-start {label_start.isoformat()}",
        f"--label-end {label_end.isoformat()}",
        "--fail-on-unknown-bill-semantic-availability",
        "--fail-on-unknown-bill-signal-availability",
        "--fail-on-unknown-ontology-edge-availability",
        "--fail-on-unknown-contribution-signal-availability",
        "--fail-on-unknown-statement-signal-availability",
        f"--output {_quoted_path(output_dir, f'prediction-eval-report-{window_id}.json')}",
        f"--dataset-output {_quoted_path(output_dir, f'prediction-eval-dataset-{window_id}.json')}",
        f"--manifest-output {_quoted_path(output_dir, f'prediction-eval-manifest-{window_id}.json')}",
    ]
    if bill_semantics_root is not None:
        parts.append(f"--bill-semantics-root {shlex.quote(bill_semantics_root)}")
    if congress_archive_manifest is not None:
        parts.append(f"--congress-archive-manifest {shlex.quote(congress_archive_manifest)}")
    return " ".join(parts)


def _prediction_input_inventory_command(
    *,
    training_feature_cutoff: date,
    train_start: date,
    train_end: date,
    feature_cutoff: date,
    label_start: date,
    label_end: date,
    output_dir: str,
    window_id: str,
    congress_archive_manifest: str | None,
) -> str:
    parts = [
        "python3 -m src.runtime.main prediction-input-inventory",
        f"--training-feature-cutoff {training_feature_cutoff.isoformat()}",
        f"--train-start {train_start.isoformat()}",
        f"--train-end {train_end.isoformat()}",
        f"--feature-cutoff {feature_cutoff.isoformat()}",
        f"--label-start {label_start.isoformat()}",
        f"--label-end {label_end.isoformat()}",
        f"--output {_quoted_path(output_dir, f'prediction-input-inventory-{window_id}.json')}",
    ]
    if congress_archive_manifest is not None:
        parts.append(f"--congress-archive-manifest {shlex.quote(congress_archive_manifest)}")
    return " ".join(parts)


def _prediction_eval_manifest_verify_command(
    *,
    output_dir: str,
    window_id: str,
    require_congress_archive_manifest: bool,
) -> str:
    parts = [
        "python3 -m src.runtime.main verify-prediction-eval-manifest",
        f"--manifest {_quoted_path(output_dir, f'prediction-eval-manifest-{window_id}.json')}",
        "--require-artifact-run-metadata",
        "--require-model-name member_vote_rate_baseline",
        "--require-model-name ontology_signal_model",
        "--require-model-name learned_signal_logistic",
        "--require-ontology-feature-signals",
        "--min-training-feature-source-url-coverage-rate 1",
        "--min-training-feature-official-source-coverage-rate 1",
        "--min-evaluation-feature-source-url-coverage-rate 1",
        "--min-evaluation-feature-official-source-coverage-rate 1",
        "--min-evaluation-source-url-coverage-rate 1",
        "--require-fail-on-unknown-bill-semantic-availability",
        "--require-fail-on-unknown-bill-signal-availability",
        "--require-fail-on-unknown-ontology-edge-availability",
        "--require-fail-on-unknown-contribution-signal-availability",
        "--require-fail-on-unknown-statement-signal-availability",
        f"--output {_quoted_path(output_dir, f'prediction-eval-manifest-verify-{window_id}.json')}",
    ]
    if require_congress_archive_manifest:
        parts.append("--require-congress-archive-manifest")
    return " ".join(parts)


def _prediction_input_inventory_verify_command(
    *,
    output_dir: str,
    window_id: str,
    require_congress_archive_manifest: bool,
) -> str:
    parts = [
        "python3 -m src.runtime.main verify-prediction-input-inventory",
        f"--artifact {_quoted_path(output_dir, f'prediction-input-inventory-{window_id}.json')}",
        "--require-run-metadata",
        "--require-portable-jurisdiction-ids",
        "--require-portable-body-ids",
        "--require-portable-session-ids",
        "--require-source-family congress_vote",
        "--require-source-family congress_bill",
        "--min-training-labels 1",
        "--min-evaluation-labels 1",
        "--min-training-feature-vote-history-source-coverage-rate 1",
        "--min-evaluation-feature-vote-history-source-coverage-rate 1",
        "--min-training-label-official-source-url-coverage-rate 1",
        "--min-evaluation-label-official-source-url-coverage-rate 1",
        "--min-bill-official-source-url-coverage-rate 1",
        "--min-bill-sponsor-availability-rate 1",
        "--require-no-bill-sponsor-introduced-date-fallbacks",
        "--min-ontology-official-source-anchor-coverage-rate 1",
        "--min-fec-contributions 1",
        "--min-member-attributed-fec-contributions 1",
        "--min-members-with-fec-candidate-id 1",
        "--min-public-statement-signals 1",
        "--min-members-with-public-statement-signals 1",
        "--output "
        f"{_quoted_path(output_dir, f'prediction-input-inventory-verify-{window_id}.json')}",
    ]
    if require_congress_archive_manifest:
        parts.append("--require-congress-archive-manifest")
    return " ".join(parts)


def _prediction_eval_window_summary_command(
    *,
    output_dir: str,
    window_ids: list[str],
) -> str:
    parts = ["python3 -m src.runtime.main prediction-eval-window-summary"]
    parts.extend(
        f"--report {_quoted_path(output_dir, f'prediction-eval-report-{window_id}.json')}"
        for window_id in window_ids
    )
    parts.extend(
        [
            "--require-report-run-metadata",
            "--require-non-overlapping-label-windows",
            f"--output {_quoted_path(output_dir, 'prediction-eval-window-summary.json')}",
        ]
    )
    return " ".join(parts)


def _verify_prediction_eval_window_summary_command(
    *,
    output_dir: str,
    window_count: int,
    plan_artifact_path: str | None,
) -> str:
    parts = [
        "python3 -m src.runtime.main verify-prediction-eval-window-summary",
        f"--artifact {_quoted_path(output_dir, 'prediction-eval-window-summary.json')}",
    ]
    if plan_artifact_path is not None:
        parts.extend(
            [
                f"--plan {shlex.quote(plan_artifact_path)}",
                "--require-plan-match",
            ]
        )
    parts.extend(
        [
            "--require-run-metadata",
            "--require-current-report-hashes",
            "--require-non-overlapping-label-windows",
            f"--min-window-count {window_count}",
            f"--output {_quoted_path(output_dir, 'prediction-eval-window-summary-verify.json')}",
        ]
    )
    return " ".join(parts)


def _verify_prediction_eval_window_run_command(
    *,
    output_dir: str,
    plan_artifact_path: str | None,
) -> str:
    parts = ["python3 -m src.runtime.main verify-prediction-eval-window-run"]
    if plan_artifact_path is not None:
        parts.append(f"--plan {shlex.quote(plan_artifact_path)}")
    else:
        parts.append(f"--plan {_quoted_path(output_dir, 'prediction-eval-window-plan.json')}")
    parts.extend(
        [
            f"--summary-verify {_quoted_path(output_dir, 'prediction-eval-window-summary-verify.json')}",
            "--require-window-verifiers",
            "--require-summary-verify",
            f"--output {_quoted_path(output_dir, 'prediction-eval-window-run-verify.json')}",
        ]
    )
    return " ".join(parts)


def _quoted_path(output_dir: str, filename: str) -> str:
    return shlex.quote(f"{output_dir.rstrip('/')}/{filename}")
