"""Generic publish pipeline: plan → write → verify.

Orchestrates :mod:`src.export.writer` and :mod:`src.export.filesystem` over
an injected planner callable so the pipeline is reusable outside the demo
contexts.

No DB, no network.  All filesystem writes go to an injected ``target_dir``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from src.export.filesystem import verify_written_files, write_planned_files
from src.export.writer import PlannedFile
from src.pipeline.stages import (
    NullEmitter,
    PipelineResult,
    ProvenanceEmitter,
    StageDefinition,
    run_pipeline,
)


# ---------------------------------------------------------------------------
# Configuration and result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishConfig:
    """Immutable configuration for one publish run.

    Args:
        snapshot_id:      Identifier for this snapshot (e.g. ``"2026-04-14"``).
        target_dir:       Root directory where files will be written.
        stop_on_failure:  If ``True`` (default), halt after the first failed
                          stage instead of continuing.
    """

    snapshot_id: str
    target_dir: Path
    stop_on_failure: bool = True


@dataclass
class PublishResult:
    """Structured outcome of a :func:`run_publish` call.

    Attributes:
        snapshot_id:             The snapshot identifier from :class:`PublishConfig`.
        planned_count:           Number of files produced by the planner stage.
        written_count:           Number of files handed to the write stage
                                 (may be 0 if planning failed or was skipped).
        verification_failures:   Relative paths whose on-disk hash did not match
                                 the planned hash.  Empty on success.
        pipeline_result:         Raw :class:`~src.pipeline.stages.PipelineResult`
                                 with per-stage detail.
    """

    snapshot_id: str
    planned_count: int
    written_count: int
    verification_failures: list[str] = field(default_factory=list)
    pipeline_result: PipelineResult = field(default_factory=PipelineResult)

    @property
    def succeeded(self) -> bool:
        """``True`` iff all pipeline stages succeeded and no hash mismatches."""
        return self.pipeline_result.succeeded and not self.verification_failures


# ---------------------------------------------------------------------------
# Stage builders
# ---------------------------------------------------------------------------

#: Callable that produces the list of :class:`~src.export.writer.PlannedFile`
#: objects for a snapshot.  Receives no arguments; all closure state should be
#: captured before passing it to :func:`run_publish`.
Planner = Callable[[], list[PlannedFile]]


def _make_plan_stage(planner: Planner) -> StageDefinition:
    """Return a stage that calls *planner* and stores results in context."""

    def _fn(context: dict[str, Any], logs: list[str]) -> list[PlannedFile]:
        planned = planner()
        context["planned_files"] = planned
        logs.append(f"planned {len(planned)} file(s)")
        return planned

    return StageDefinition(
        name="plan",
        fn=_fn,
        description="Invoke the planner to produce the set of files to write.",
    )


def _make_write_stage(target_dir: Path) -> StageDefinition:
    """Return a stage that writes planned files to *target_dir*."""

    def _fn(context: dict[str, Any], logs: list[str]) -> int:
        planned: list[PlannedFile] = context["planned_files"]
        write_planned_files(planned, target_dir)
        count = len(planned)
        context["written_count"] = count
        logs.append(f"wrote {count} file(s) to {target_dir}")
        return count

    return StageDefinition(
        name="write",
        fn=_fn,
        description="Write planned files to the target directory.",
    )


def _make_verify_stage(target_dir: Path) -> StageDefinition:
    """Return a stage that verifies on-disk hashes match planned hashes."""

    def _fn(context: dict[str, Any], logs: list[str]) -> list[str]:
        planned: list[PlannedFile] = context["planned_files"]
        failures = verify_written_files(planned, target_dir)
        context["verification_failures"] = failures
        if failures:
            logs.append(f"hash mismatch for {len(failures)} file(s): {failures}")
            raise ValueError(
                f"Verification failed for {len(failures)} file(s): {failures}"
            )
        logs.append(f"all {len(planned)} file(s) verified")
        return failures

    return StageDefinition(
        name="verify",
        fn=_fn,
        description="Verify that written files match their planned SHA-256 hashes.",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_publish(
    config: PublishConfig,
    planner: Planner,
    emitter: Optional[ProvenanceEmitter] = None,
) -> PublishResult:
    """Run the three-stage publish pipeline for a snapshot.

    Stages:

    1. **plan**   — call *planner()* to produce :class:`~src.export.writer.PlannedFile` list.
    2. **write**  — write files to ``config.target_dir``.
    3. **verify** — re-read files and compare SHA-256 digests.

    The *planner* is intentionally a zero-argument callable so the caller
    controls all snapshot inputs via closure — keeping this function generic
    and side-effect-free apart from the filesystem write in stage 2.

    Args:
        config:   Publish configuration (snapshot ID, target directory, etc.).
        planner:  Zero-argument callable that returns a list of
                  :class:`~src.export.writer.PlannedFile` objects.
        emitter:  Optional :class:`~src.pipeline.stages.ProvenanceEmitter` for
                  lifecycle hooks.  Defaults to :class:`~src.pipeline.stages.NullEmitter`.

    Returns:
        A :class:`PublishResult` with outcome details.
    """
    _emitter: ProvenanceEmitter = emitter or NullEmitter()

    stages = [
        _make_plan_stage(planner),
        _make_write_stage(config.target_dir),
        _make_verify_stage(config.target_dir),
    ]

    context: dict[str, Any] = {}
    pipeline_result = run_pipeline(
        stages,
        context=context,
        emitter=_emitter,
        stop_on_failure=config.stop_on_failure,
    )

    return PublishResult(
        snapshot_id=config.snapshot_id,
        planned_count=len(context.get("planned_files", [])),
        written_count=context.get("written_count", 0),
        verification_failures=context.get("verification_failures", []),
        pipeline_result=pipeline_result,
    )
