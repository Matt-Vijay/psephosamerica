"""Planning and local replay helpers for historical snapshot backfills."""

from __future__ import annotations

import datetime as dt
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

from src.runtime.congress_options import current_congress_for_date
from src.export.writer import manifest_path
from src.runtime.context import RuntimeContext, open_connection
from src.runtime.disclosures_bundle import DisclosuresBundle
from src.runtime.oracle_contracts import CongressOracleOptions, LocalOracleRunResult, LocalOracleOptions
from src.runtime.oracle_local import run_oracle_local
from src.runtime.history_verify import verify_history_aggregate_local
from src.runtime.history_verify_types import HistoryVerifyResult


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
    verify: HistoryVerifyResult | None = None


@dataclass(frozen=True)
class LocalHistoryBackfillResult:
    execution: HistoryBackfillExecutionResult
    snapshot_results: dict[str, LocalOracleRunResult]
    aggregate_source_roots: list[Path]
    target_root: Path
    overwrite: bool
    continue_on_error: bool
    aggregate: LocalHistoryAggregateSummary | None = None
    aggregate_error: str | None = None

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
        aggregate_ok = self.aggregate.verify.ok if self.aggregate is not None and self.aggregate.verify is not None else True
        return self.execution.ok and self.aggregate_error is None and aggregate_ok


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
    return LocalHistoryAggregateSummary(
        latest_snapshot_id=result.latest_snapshot_id,
        snapshot_count=len(result.snapshot_index.snapshots),
        member_history_count=result.member_history_count,
        target_root=result.target_root,
        verify=verify,
    )


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

    return LocalHistoryBackfillResult(
        execution=execution,
        snapshot_results=snapshot_results,
        aggregate_source_roots=aggregate_source_roots,
        target_root=target_root,
        overwrite=overwrite,
        continue_on_error=continue_on_error,
        aggregate=aggregate,
        aggregate_error=aggregate_error,
    )
