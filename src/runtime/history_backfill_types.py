"""Typed report contracts for local history backfill runs."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from src.export.contracts import HistoryCoveragePayload


def history_backfill_report_path(target_root: Path) -> Path:
    return target_root / "_reports" / "history-backfill.json"


class HistoryBackfillVerifyStagePayload(BaseModel):
    stage: str
    checked: int = Field(ge=0)
    ok: bool
    errors: int = Field(ge=0)
    warnings: int = Field(ge=0)


class HistoryBackfillVerifyPayload(BaseModel):
    ok: bool
    total_checked: int = Field(ge=0)
    total_errors: int = Field(ge=0)
    total_warnings: int = Field(ge=0)
    stages: list[HistoryBackfillVerifyStagePayload] = Field(default_factory=list)


class HistoryBackfillDateWindowPayload(BaseModel):
    start_date: dt.date
    end_date: dt.date
    bounded_by_today: bool


class HistoryBackfillAttemptPayload(BaseModel):
    snapshot_id: str
    snapshot_date: dt.date
    publish_root: str
    status: Literal["completed", "skipped_existing", "failed"]
    reason: str | None = None
    error: str | None = None
    ok: bool | None = None


class HistoryBackfillAggregatePayload(BaseModel):
    latest_snapshot_id: str
    snapshot_count: int = Field(ge=0)
    member_history_count: int = Field(ge=0)
    target_root: str
    coverage_path: str | None = None
    coverage: HistoryCoveragePayload | None = None
    verify: HistoryBackfillVerifyPayload | None = None


class HistoryBackfillReportPayload(BaseModel):
    congress: int
    cadence: str
    target_root: str
    report_path: str | None = None
    overwrite: bool
    continue_on_error: bool
    date_window: HistoryBackfillDateWindowPayload
    planned_count: int = Field(ge=0)
    attempted_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    remaining_count: int = Field(ge=0)
    aggregate_source_count: int = Field(ge=0)
    aggregate_error: str | None = None
    input_readiness: HistoryBackfillInputReadinessPayload | None = None
    attempts: list[HistoryBackfillAttemptPayload] = Field(default_factory=list)
    aggregate: HistoryBackfillAggregatePayload | None = None


class HistoryBackfillCongressArchiveInputsPayload(BaseModel):
    path: str
    kind: Literal["manifest", "directory", "missing", "invalid"]
    exists: bool
    validated_with_manifest: bool
    checked_paths: int = Field(ge=0)
    missing_files: list[str] = Field(default_factory=list)


class HistoryBackfillDisclosuresBundleInputsPayload(BaseModel):
    path: str
    exists: bool
    artifact_count: int = Field(default=0, ge=0)
    house_count: int = Field(default=0, ge=0)
    senate_count: int = Field(default=0, ge=0)
    filing_years: list[int] = Field(default_factory=list)
    relative_artifact_count: int = Field(default=0, ge=0)
    absolute_artifact_count: int = Field(default=0, ge=0)
    resolved_artifact_root: str | None = None
    checked_artifact_count: int = Field(default=0, ge=0)
    missing_artifacts: list[str] = Field(default_factory=list)
    sha256_mismatches: list[str] = Field(default_factory=list)
    validation_violations: list[str] = Field(default_factory=list)


class HistoryBackfillInputReadinessPayload(BaseModel):
    congress_archive: HistoryBackfillCongressArchiveInputsPayload
    disclosures_bundle: HistoryBackfillDisclosuresBundleInputsPayload
    requested_chamber: Literal["house", "senate", "both"] = "both"
    requested_limit: int | None = None
    ready_to_replay: bool
    readiness_status: Literal["ready", "partial", "blocked"]
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
