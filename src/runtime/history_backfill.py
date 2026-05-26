"""Planning and local replay helpers for historical snapshot backfills."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal
from uuid import uuid4

from src.ingest.congress.archive import CongressArchiveManifest
from src.ingest.congress.archive_manifest import load_manifest
from src.ingest.congress.archive_validate import validate_congress_archive_manifest
from src.runtime.congress_options import current_congress_for_date
from src.export.contracts import HistoryCoveragePayload
from src.export.local_store import load_history_coverage
from src.export.writer import history_coverage_path, manifest_path
from src.runtime.context import RuntimeContext, open_connection
from src.runtime.disclosures_bundle import DisclosuresBundle
from src.runtime.disclosures_bundle import load_disclosures_bundle
from src.runtime.disclosures_bundle_files import Sha256Mismatch, read_and_verify_entry
from src.runtime.disclosures_bundle_validate import (
    DisclosuresBundleValidationError,
    validate_disclosures_bundle,
)
from src.runtime.oracle_contracts import (
    CongressOracleOptions,
    LocalOracleRunResult,
    LocalOracleOptions,
)
from src.runtime.oracle_local import run_oracle_local
from src.runtime.history_verify import verify_history_aggregate_local
from src.runtime.history_verify_types import HistoryVerifyResult
from src.runtime.history_backfill_types import (
    HistoryBackfillCongressArchiveInputsPayload,
    HistoryBackfillDisclosuresBundleInputsPayload,
    HistoryBackfillAggregatePayload,
    HistoryBackfillAttemptPayload,
    HistoryBackfillDateWindowPayload,
    HistoryBackfillInputReadinessPayload,
    HistoryBackfillReportPayload,
    HistoryBackfillVerifyPayload,
    HistoryBackfillVerifyStagePayload,
    history_backfill_report_path,
)


_WEEKDAY_NAMES = {
    0: "monday",
    1: "tuesday",
    2: "wednesday",
    3: "thursday",
    4: "friday",
    5: "saturday",
    6: "sunday",
}

if TYPE_CHECKING:
    from src.pipeline.history_aggregate_run import HistoryAggregateResult


@dataclass(frozen=True)
class CongressDateWindow:
    congress: int
    start_date: dt.date
    end_date: dt.date
    bounded_by_today: bool


@dataclass(frozen=True)
class HistoricalSnapshotTarget:
    congress: int
    snapshot_date: dt.date
    snapshot_id: str
    publish_root: Path


@dataclass(frozen=True)
class HistoryBackfillPlan:
    congress: int
    date_window: CongressDateWindow
    cadence: str
    targets: list[HistoricalSnapshotTarget]


@dataclass(frozen=True)
class HistoryBackfillAttempt:
    snapshot_id: str
    snapshot_date: dt.date
    publish_root: Path
    status: Literal["completed", "skipped_existing", "failed"]
    reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class HistoryBackfillExecutionResult:
    plan: HistoryBackfillPlan
    attempts: list[HistoryBackfillAttempt]

    @property
    def completed_count(self) -> int:
        return sum(attempt.status == "completed" for attempt in self.attempts)

    @property
    def skipped_count(self) -> int:
        return sum(attempt.status == "skipped_existing" for attempt in self.attempts)

    @property
    def failed_count(self) -> int:
        return sum(attempt.status == "failed" for attempt in self.attempts)

    @property
    def ok(self) -> bool:
        return self.failed_count == 0


@dataclass(frozen=True)
class LocalHistoryAggregateSummary:
    latest_snapshot_id: str
    snapshot_count: int
    member_history_count: int
    target_root: Path
    coverage_path: Path | None = None
    coverage: HistoryCoveragePayload | None = None
    verify: HistoryVerifyResult | None = None


@dataclass(frozen=True)
class LocalHistoryBackfillResult:
    execution: HistoryBackfillExecutionResult
    snapshot_results: dict[str, LocalOracleRunResult]
    aggregate_source_roots: list[Path]
    target_root: Path
    overwrite: bool
    continue_on_error: bool
    report_path: Path | None = None
    aggregate: LocalHistoryAggregateSummary | None = None
    aggregate_error: str | None = None
    input_readiness: HistoryBackfillInputReadinessPayload | None = None

    @property
    def plan(self) -> HistoryBackfillPlan:
        return self.execution.plan

    @property
    def attempts(self) -> list[HistoryBackfillAttempt]:
        return self.execution.attempts

    @property
    def completed_count(self) -> int:
        return self.execution.completed_count

    @property
    def skipped_count(self) -> int:
        return self.execution.skipped_count

    @property
    def failed_count(self) -> int:
        return self.execution.failed_count

    @property
    def attempted_count(self) -> int:
        return len(self.execution.attempts)

    @property
    def remaining_count(self) -> int:
        return max(0, len(self.plan.targets) - self.attempted_count)

    @property
    def ok(self) -> bool:
        aggregate_ok = (
            self.aggregate.verify.ok
            if self.aggregate is not None and self.aggregate.verify is not None
            else True
        )
        input_ready = (
            self.input_readiness.ready_to_replay if self.input_readiness is not None else True
        )
        return input_ready and self.execution.ok and self.aggregate_error is None and aggregate_ok


def _manifest_checked_path_count(manifest: CongressArchiveManifest) -> int:
    return (
        3
        + len(manifest.cosponsors)
        + len(manifest.member_details)
        + len(manifest.bill_details)
        + len(manifest.house_votes)
        + len(manifest.senate_votes)
    )


def _check_congress_archive_inputs(
    archive: Path,
) -> tuple[HistoryBackfillCongressArchiveInputsPayload, list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []

    if not archive.exists():
        return (
            HistoryBackfillCongressArchiveInputsPayload(
                path=str(archive),
                kind="missing",
                exists=False,
                validated_with_manifest=False,
                checked_paths=0,
            ),
            [f"congress archive path does not exist: {archive}"],
            warnings,
        )

    if archive.is_file():
        if archive.suffix != ".json":
            return (
                HistoryBackfillCongressArchiveInputsPayload(
                    path=str(archive),
                    kind="invalid",
                    exists=True,
                    validated_with_manifest=False,
                    checked_paths=0,
                ),
                [f"congress archive file must be a manifest JSON: {archive}"],
                warnings,
            )
        try:
            manifest = load_manifest(archive)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return (
                HistoryBackfillCongressArchiveInputsPayload(
                    path=str(archive),
                    kind="manifest",
                    exists=True,
                    validated_with_manifest=False,
                    checked_paths=0,
                ),
                [f"congress archive manifest is invalid: {exc}"],
                warnings,
            )
        result = validate_congress_archive_manifest(manifest)
        missing_files = [item.label for item in result.missing]
        if missing_files:
            blockers.append("congress archive manifest references missing files")
        return (
            HistoryBackfillCongressArchiveInputsPayload(
                path=str(archive),
                kind="manifest",
                exists=True,
                validated_with_manifest=True,
                checked_paths=_manifest_checked_path_count(manifest),
                missing_files=missing_files,
            ),
            blockers,
            warnings,
        )

    if archive.is_dir():
        manifest_file = archive / "manifest.json"
        if manifest_file.is_file():
            payload, blockers, warnings = _check_congress_archive_inputs(manifest_file)
            return (
                payload.model_copy(update={"path": str(archive), "kind": "directory"}),
                blockers,
                warnings,
            )

        required = {
            "members": archive / "members.json",
            "committees": archive / "committees.json",
            "bills": archive / "bills.json",
        }
        missing_files = [label for label, path in required.items() if not path.exists()]
        if missing_files:
            blockers.append("congress archive directory is missing required root files")
        warnings.append(
            "congress archive directory has no manifest.json; validation is limited to required root files"
        )
        return (
            HistoryBackfillCongressArchiveInputsPayload(
                path=str(archive),
                kind="directory",
                exists=True,
                validated_with_manifest=False,
                checked_paths=len(required),
                missing_files=missing_files,
            ),
            blockers,
            warnings,
        )

    return (
        HistoryBackfillCongressArchiveInputsPayload(
            path=str(archive),
            kind="invalid",
            exists=True,
            validated_with_manifest=False,
            checked_paths=0,
        ),
        [f"congress archive path is not a file or directory: {archive}"],
        warnings,
    )


def _verify_absolute_entry(
    entry_path: Path,
    *,
    expected_sha256: str,
    storage_uri: str,
) -> None:
    if not entry_path.exists():
        raise FileNotFoundError(storage_uri)
    actual_sha256 = hashlib.sha256(entry_path.read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        raise Sha256Mismatch(
            f"SHA-256 mismatch for {storage_uri!r}: expected {expected_sha256!r}, got {actual_sha256!r}"
        )


def _check_disclosures_bundle_inputs(
    bundle_path: Path,
    *,
    artifact_root: Path | None = None,
) -> tuple[HistoryBackfillDisclosuresBundleInputsPayload, list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []

    if not bundle_path.exists():
        return (
            HistoryBackfillDisclosuresBundleInputsPayload(
                path=str(bundle_path),
                exists=False,
            ),
            [f"disclosures bundle path does not exist: {bundle_path}"],
            warnings,
        )

    try:
        bundle = load_disclosures_bundle(bundle_path)
        validate_disclosures_bundle(bundle)
    except DisclosuresBundleValidationError as exc:
        return (
            HistoryBackfillDisclosuresBundleInputsPayload(
                path=str(bundle_path),
                exists=True,
                validation_violations=list(exc.violations),
            ),
            [f"disclosures bundle is invalid: {exc}"],
            warnings,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return (
            HistoryBackfillDisclosuresBundleInputsPayload(
                path=str(bundle_path),
                exists=True,
            ),
            [f"disclosures bundle is invalid: {exc}"],
            warnings,
        )

    if artifact_root is not None and not artifact_root.exists():
        blockers.append(f"artifact_root does not exist: {artifact_root}")

    relative_artifact_count = 0
    absolute_artifact_count = 0
    checked_artifact_count = 0
    missing_artifacts: list[str] = []
    sha256_mismatches: list[str] = []
    house_count = 0
    senate_count = 0
    filing_years = sorted({entry.filing_year for entry in bundle.artifacts})

    for entry in bundle.artifacts:
        if entry.chamber == "house":
            house_count += 1
        elif entry.chamber == "senate":
            senate_count += 1

        storage_uri_path = Path(entry.storage_uri)
        if storage_uri_path.is_absolute():
            absolute_artifact_count += 1
            if artifact_root is not None and not artifact_root.exists():
                continue
            try:
                _verify_absolute_entry(
                    storage_uri_path,
                    expected_sha256=entry.sha256,
                    storage_uri=entry.storage_uri,
                )
            except FileNotFoundError:
                missing_artifacts.append(entry.storage_uri)
            except Sha256Mismatch:
                sha256_mismatches.append(entry.storage_uri)
            else:
                checked_artifact_count += 1
            continue

        relative_artifact_count += 1
        if artifact_root is None or not artifact_root.exists():
            continue
        try:
            read_and_verify_entry(entry, artifact_root)
        except FileNotFoundError:
            missing_artifacts.append(entry.storage_uri)
        except Sha256Mismatch:
            sha256_mismatches.append(entry.storage_uri)
        else:
            checked_artifact_count += 1

    if relative_artifact_count > 0 and artifact_root is None:
        blockers.append(
            "artifact_root is required to resolve relative disclosures bundle storage_uri values"
        )
    if missing_artifacts:
        blockers.append("disclosures bundle artifacts are missing")
    if sha256_mismatches:
        blockers.append("disclosures bundle artifacts failed sha256 verification")

    return (
        HistoryBackfillDisclosuresBundleInputsPayload(
            path=str(bundle_path),
            exists=True,
            artifact_count=len(bundle.artifacts),
            house_count=house_count,
            senate_count=senate_count,
            filing_years=filing_years,
            relative_artifact_count=relative_artifact_count,
            absolute_artifact_count=absolute_artifact_count,
            resolved_artifact_root=str(artifact_root) if artifact_root is not None else None,
            checked_artifact_count=checked_artifact_count,
            missing_artifacts=missing_artifacts,
            sha256_mismatches=sha256_mismatches,
        ),
        blockers,
        warnings,
    )


def check_history_backfill_inputs(
    congress_archive: Path,
    disclosures_bundle: Path,
    *,
    artifact_root: Path | None = None,
    chamber: Literal["house", "senate", "both"] = "both",
    limit: int | None = None,
) -> HistoryBackfillInputReadinessPayload:
    archive_payload, archive_blockers, archive_warnings = _check_congress_archive_inputs(
        congress_archive
    )
    bundle_payload, bundle_blockers, bundle_warnings = _check_disclosures_bundle_inputs(
        disclosures_bundle,
        artifact_root=artifact_root,
    )
    blockers = [*archive_blockers, *bundle_blockers]
    warnings = [*archive_warnings, *bundle_warnings]
    if chamber in {"house", "both"} and bundle_payload.house_count == 0:
        blockers.append("requested house disclosures are missing from bundle")
    if chamber in {"senate", "both"} and bundle_payload.senate_count == 0:
        blockers.append("requested senate disclosures are missing from bundle")

    readiness_status: Literal["ready", "partial", "blocked"]
    if blockers:
        readiness_status = "blocked"
    elif warnings:
        readiness_status = "partial"
    else:
        readiness_status = "ready"
    return HistoryBackfillInputReadinessPayload(
        congress_archive=archive_payload,
        disclosures_bundle=bundle_payload,
        requested_chamber=chamber,
        requested_limit=limit,
        ready_to_replay=not blockers,
        readiness_status=readiness_status,
        blockers=blockers,
        warnings=warnings,
    )


def congress_term_bounds(congress: int) -> tuple[dt.date, dt.date]:
    """Return the inclusive date bounds for a numbered Congress."""
    start_year = 1789 + ((congress - 1) * 2)
    return (dt.date(start_year, 1, 3), dt.date(start_year + 2, 1, 2))


def resolve_congress_date_window(
    congress: int,
    *,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    today: dt.date | None = None,
) -> CongressDateWindow:
    """Resolve a truthful, bounded date window for historical replay.

    Explicit start/end dates are clamped to the Congress term. For the active
    Congress, the effective end date is capped at ``today`` so planners do not
    fabricate future snapshots.
    """
    term_start, term_end = congress_term_bounds(congress)
    current = today if today is not None else dt.date.today()
    active_congress = current_congress_for_date(current)

    effective_start = max(term_start, start_date) if start_date is not None else term_start
    effective_end = min(term_end, end_date) if end_date is not None else term_end
    bounded_by_today = congress >= active_congress
    if bounded_by_today:
        effective_end = min(effective_end, current)

    if effective_end < effective_start:
        raise ValueError(
            "Resolved history window is empty: "
            f"start={effective_start.isoformat()} end={effective_end.isoformat()}"
        )

    return CongressDateWindow(
        congress=congress,
        start_date=effective_start,
        end_date=effective_end,
        bounded_by_today=bounded_by_today,
    )


def derive_weekly_snapshot_dates(
    start_date: dt.date,
    end_date: dt.date,
    *,
    weekday: int = 0,
) -> list[dt.date]:
    """Return all weekly snapshot dates on ``weekday`` within an inclusive range."""
    if weekday not in _WEEKDAY_NAMES:
        raise ValueError(f"weekday must be 0-6, got {weekday}")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    offset = (weekday - start_date.weekday()) % 7
    current = start_date + dt.timedelta(days=offset)
    dates: list[dt.date] = []
    while current <= end_date:
        dates.append(current)
        current += dt.timedelta(days=7)
    return dates


def plan_congress_history_backfill(
    congress: int,
    *,
    target_root: Path,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    today: dt.date | None = None,
    weekday: int = 0,
) -> HistoryBackfillPlan:
    """Return per-snapshot publish targets for a Congress history backfill."""
    window = resolve_congress_date_window(
        congress,
        start_date=start_date,
        end_date=end_date,
        today=today,
    )
    snapshot_dates = derive_weekly_snapshot_dates(
        window.start_date,
        window.end_date,
        weekday=weekday,
    )
    if not snapshot_dates:
        raise ValueError(
            "Resolved history window contains no weekly snapshot dates: "
            f"start={window.start_date.isoformat()} end={window.end_date.isoformat()} "
            f"cadence=weekly:{_WEEKDAY_NAMES[weekday]}"
        )
    targets = [
        HistoricalSnapshotTarget(
            congress=congress,
            snapshot_date=snapshot_date,
            snapshot_id=snapshot_date.isoformat(),
            publish_root=target_root / snapshot_date.isoformat(),
        )
        for snapshot_date in snapshot_dates
    ]
    return HistoryBackfillPlan(
        congress=congress,
        date_window=window,
        cadence=f"weekly:{_WEEKDAY_NAMES[weekday]}",
        targets=targets,
    )


ReplaySnapshot = Callable[[HistoricalSnapshotTarget], None]


class HistoryBackfillReplayError(RuntimeError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def execute_history_backfill(
    plan: HistoryBackfillPlan,
    replay_snapshot: ReplaySnapshot,
    *,
    overwrite: bool = False,
    continue_on_error: bool = False,
) -> HistoryBackfillExecutionResult:
    """Execute or skip the targets in a backfill plan.

    Existing publish roots with a matching manifest are skipped by default.
    Existing directories without the expected manifest are treated as failed
    partial trees unless ``overwrite=True`` explicitly allows replacement.
    """
    attempts: list[HistoryBackfillAttempt] = []

    for target in plan.targets:
        manifest_file = target.publish_root / manifest_path(target.snapshot_id)
        target_issue = _unsafe_overwrite_target_issue(target)
        if target_issue is not None:
            attempts.append(
                HistoryBackfillAttempt(
                    snapshot_id=target.snapshot_id,
                    snapshot_date=target.snapshot_date,
                    publish_root=target.publish_root,
                    status="failed",
                    reason="unsafe_overwrite_target",
                    error=target_issue,
                )
            )
            if not continue_on_error:
                break
            continue

        if manifest_file.exists() and not overwrite:
            attempts.append(
                HistoryBackfillAttempt(
                    snapshot_id=target.snapshot_id,
                    snapshot_date=target.snapshot_date,
                    publish_root=target.publish_root,
                    status="skipped_existing",
                    reason="existing_manifest",
                )
            )
            continue

        if target.publish_root.exists() and not overwrite and not manifest_file.exists():
            attempts.append(
                HistoryBackfillAttempt(
                    snapshot_id=target.snapshot_id,
                    snapshot_date=target.snapshot_date,
                    publish_root=target.publish_root,
                    status="failed",
                    reason="partial_existing_tree",
                    error="existing publish root is missing the expected manifest",
                )
            )
            if not continue_on_error:
                break
            continue

        if overwrite and target.publish_root.exists():
            shutil.rmtree(target.publish_root, ignore_errors=True)

        try:
            replay_snapshot(target)
        except Exception as exc:  # noqa: BLE001
            attempts.append(
                HistoryBackfillAttempt(
                    snapshot_id=target.snapshot_id,
                    snapshot_date=target.snapshot_date,
                    publish_root=target.publish_root,
                    status="failed",
                    reason=getattr(exc, "reason", "replay_exception"),
                    error=str(exc),
                )
            )
            if not continue_on_error:
                break
            continue

        attempts.append(
            HistoryBackfillAttempt(
                snapshot_id=target.snapshot_id,
                snapshot_date=target.snapshot_date,
                publish_root=target.publish_root,
                status="completed",
            )
        )

    return HistoryBackfillExecutionResult(plan=plan, attempts=attempts)


def _unsafe_overwrite_target_issue(target: HistoricalSnapshotTarget) -> str | None:
    if target.publish_root.name != target.snapshot_id:
        return "publish root name must match snapshot id before overwrite cleanup"
    if target.publish_root == target.publish_root.parent:
        return "publish root must not be a filesystem root"
    return None


def _close_connection(conn: Any) -> None:
    close = getattr(conn, "close", None)
    if callable(close):
        close()


def _oracle_result_ok(result: LocalOracleRunResult) -> bool:
    return (
        result.congress.load_ok
        and bool(result.disclosures.get("load_ok", True))
        and bool(result.publish.get("succeeded", True))
        and result.verify.ok
        and result.roundtrip.ok
    )


def _oracle_failure_reason(result: LocalOracleRunResult) -> str:
    failures: list[str] = []
    if not result.congress.load_ok:
        failures.append("congress.load_ok=false")
    if not bool(result.disclosures.get("load_ok", True)):
        failures.append("disclosures.load_ok=false")
    if not bool(result.publish.get("succeeded", True)):
        failures.append("publish.succeeded=false")
    if not result.verify.ok:
        failures.append(f"verify.ok=false ({result.verify.total_errors} errors)")
    if not result.roundtrip.ok:
        failures.append(f"roundtrip.ok=false ({result.roundtrip.total_errors} errors)")
    if not failures:
        return "local oracle run failed for an unknown reason"
    return "local oracle run failed: " + ", ".join(failures)


def _aggregate_summary(
    result: HistoryAggregateResult,
    *,
    verify: HistoryVerifyResult | None = None,
) -> LocalHistoryAggregateSummary:
    try:
        coverage = load_history_coverage(result.target_root)
    except Exception:
        coverage = None
    return LocalHistoryAggregateSummary(
        latest_snapshot_id=result.latest_snapshot_id,
        snapshot_count=len(result.snapshot_index.snapshots),
        member_history_count=result.member_history_count,
        target_root=result.target_root,
        coverage_path=result.target_root / history_coverage_path(),
        coverage=coverage,
        verify=verify,
    )


def build_history_backfill_report(
    result: LocalHistoryBackfillResult,
) -> HistoryBackfillReportPayload:
    def _summarize_verify(
        verify: HistoryVerifyResult | None,
    ) -> HistoryBackfillVerifyPayload | None:
        if verify is None:
            return None
        return HistoryBackfillVerifyPayload(
            ok=verify.ok,
            total_checked=verify.total_checked,
            total_errors=verify.total_errors,
            total_warnings=verify.total_warnings,
            stages=[
                HistoryBackfillVerifyStagePayload(
                    stage=stage.stage,
                    checked=stage.checked,
                    ok=stage.ok,
                    errors=stage.error_count,
                    warnings=stage.warning_count,
                )
                for stage in verify.stages
            ],
        )

    attempts: list[HistoryBackfillAttemptPayload] = []
    for attempt in result.attempts:
        oracle_result = result.snapshot_results.get(attempt.snapshot_id)
        attempts.append(
            HistoryBackfillAttemptPayload(
                snapshot_id=attempt.snapshot_id,
                snapshot_date=attempt.snapshot_date,
                publish_root=str(attempt.publish_root),
                status=attempt.status,
                reason=attempt.reason,
                error=attempt.error,
                ok=_oracle_result_ok(oracle_result) if oracle_result is not None else None,
            )
        )

    aggregate_out: HistoryBackfillAggregatePayload | None = None
    if result.aggregate is not None:
        aggregate_out = HistoryBackfillAggregatePayload(
            latest_snapshot_id=result.aggregate.latest_snapshot_id,
            snapshot_count=result.aggregate.snapshot_count,
            member_history_count=result.aggregate.member_history_count,
            target_root=str(result.aggregate.target_root),
            coverage_path=str(result.aggregate.coverage_path)
            if result.aggregate.coverage_path is not None
            else None,
            coverage=result.aggregate.coverage,
            verify=_summarize_verify(result.aggregate.verify),
        )

    return HistoryBackfillReportPayload(
        congress=result.plan.congress,
        cadence=result.plan.cadence,
        target_root=str(result.target_root),
        report_path=str(result.report_path) if result.report_path is not None else None,
        overwrite=result.overwrite,
        continue_on_error=result.continue_on_error,
        date_window=HistoryBackfillDateWindowPayload(
            start_date=result.plan.date_window.start_date,
            end_date=result.plan.date_window.end_date,
            bounded_by_today=result.plan.date_window.bounded_by_today,
        ),
        planned_count=len(result.plan.targets),
        attempted_count=result.attempted_count,
        completed_count=result.completed_count,
        skipped_count=result.skipped_count,
        failed_count=result.failed_count,
        remaining_count=result.remaining_count,
        aggregate_source_count=len(result.aggregate_source_roots),
        aggregate_error=result.aggregate_error,
        input_readiness=result.input_readiness,
        attempts=attempts,
        aggregate=aggregate_out,
    )


def load_history_backfill_report(target_root: Path) -> HistoryBackfillReportPayload:
    report_path = history_backfill_report_path(target_root)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return HistoryBackfillReportPayload.model_validate(data)


def run_local_history_backfill(
    ctx: RuntimeContext,
    congress_archive: Path,
    disclosures_bundle: DisclosuresBundle,
    *,
    congress: int,
    target_root: Path,
    aggregate_root: Path | None = None,
    chamber: str | None = None,
    limit: int | None = None,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    overwrite: bool = False,
    continue_on_error: bool = False,
    artifact_root: Path | None = None,
) -> LocalHistoryBackfillResult:
    """Replay the local oracle across a planned weekly Congress history window."""
    plan = plan_congress_history_backfill(
        congress,
        target_root=target_root,
        start_date=start_date,
        end_date=end_date,
    )
    snapshot_results: dict[str, LocalOracleRunResult] = {}

    def _replay(target: HistoricalSnapshotTarget) -> None:
        conn = open_connection(ctx)
        try:
            result = run_oracle_local(
                conn,
                congress_archive,
                disclosures_bundle,
                LocalOracleOptions(
                    congress_options=CongressOracleOptions(
                        congress=congress,
                        chamber=chamber,
                        limit=limit,
                        congress_source="explicit-arg",
                    ),
                    snapshot_date=target.snapshot_date,
                    target_dir=target.publish_root,
                    snapshot_id=target.snapshot_id,
                    artifact_root=artifact_root,
                ),
            )
            snapshot_results[target.snapshot_id] = result
            if not _oracle_result_ok(result):
                raise HistoryBackfillReplayError(
                    _oracle_failure_reason(result),
                    reason="oracle_stage_failure",
                )
        finally:
            _close_connection(conn)

    execution = execute_history_backfill(
        plan,
        _replay,
        overwrite=overwrite,
        continue_on_error=continue_on_error,
    )

    aggregate_source_roots = [
        attempt.publish_root
        for attempt in execution.attempts
        if attempt.status in {"completed", "skipped_existing"}
    ]

    aggregate: LocalHistoryAggregateSummary | None = None
    aggregate_error: str | None = None
    if aggregate_root is not None:
        if not aggregate_source_roots:
            aggregate_error = "no successful or existing snapshot roots available to aggregate"
        else:
            try:
                from src.pipeline.history_aggregate_run import write_history_aggregate

                aggregate_result = write_history_aggregate(aggregate_source_roots, aggregate_root)
                verify_result = verify_history_aggregate_local(aggregate_root)
                aggregate = _aggregate_summary(
                    aggregate_result,
                    verify=verify_result,
                )
            except Exception as exc:  # noqa: BLE001
                aggregate_error = str(exc)

    report_path = history_backfill_report_path(target_root)
    result = LocalHistoryBackfillResult(
        execution=execution,
        snapshot_results=snapshot_results,
        aggregate_source_roots=aggregate_source_roots,
        target_root=target_root,
        overwrite=overwrite,
        continue_on_error=continue_on_error,
        report_path=report_path,
        aggregate=aggregate,
        aggregate_error=aggregate_error,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(
        report_path,
        json.dumps(
            build_history_backfill_report(result).model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        ),
    )
    return result


def _write_text_atomic(path: Path, text: str) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
