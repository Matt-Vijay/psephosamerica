"""Generic publish pipeline: plan → write → verify.

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
    snapshot_id: str
    target_dir: Path
    stop_on_failure: bool = True


@dataclass
class PublishResult:
    snapshot_id: str
    planned_count: int
    written_count: int
    verification_failures: list[str] = field(default_factory=list)
    pipeline_result: PipelineResult = field(default_factory=PipelineResult)

    @property
    def succeeded(self) -> bool:
        return self.pipeline_result.succeeded and not self.verification_failures


# ---------------------------------------------------------------------------
# Stage builders
# ---------------------------------------------------------------------------

#: Zero-argument callable; all inputs must be captured in the closure before
#: passing to run_publish so this module stays side-effect-free.
Planner = Callable[[], list[PlannedFile]]


def _make_plan_stage(planner: Planner) -> StageDefinition:
    def _fn(context: dict[str, Any], logs: list[str]) -> list[PlannedFile]:
        planned = planner()
        context["planned_files"] = planned
        logs.append(f"planned {len(planned)} file(s)")
        return planned

    return StageDefinition(name="plan", fn=_fn)


def _make_write_stage(target_dir: Path) -> StageDefinition:
    def _fn(context: dict[str, Any], logs: list[str]) -> int:
        planned: list[PlannedFile] = context["planned_files"]
        write_planned_files(planned, target_dir)
        count = len(planned)
        context["written_count"] = count
        logs.append(f"wrote {count} file(s) to {target_dir}")
        return count

    return StageDefinition(name="write", fn=_fn)


def _make_verify_stage(target_dir: Path) -> StageDefinition:
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

    return StageDefinition(name="verify", fn=_fn)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_publish(
    config: PublishConfig,
    planner: Planner,
    emitter: Optional[ProvenanceEmitter] = None,
) -> PublishResult:
    """Run the three-stage publish pipeline: plan → write → verify SHA-256 digests."""
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
