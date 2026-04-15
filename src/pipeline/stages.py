"""Generic pipeline stage runner — typed structures and pure orchestration.

No DB, no network, no I/O.  Inject a ProvenanceEmitter for lifecycle hooks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Typed structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StageDefinition:
    """Immutable description of one pipeline stage.

    ``fn(context, logs)`` — *context* is the shared mutable dict; *logs* is a
    fresh ``list[str]`` the function may append to.  Return a value on success
    or raise on failure.
    """

    name: str
    fn: Callable[..., Any]
    description: str = ""


@dataclass
class StageResult:
    """Outcome of a single stage execution."""

    stage_name: str
    status: str          # "succeeded" | "failed" | "skipped"
    output: Any = None
    error: Optional[BaseException] = None
    logs: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def summary(self) -> dict[str, Any]:
        return {
            "stage": self.stage_name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "log_count": len(self.logs),
            "error": str(self.error) if self.error else None,
        }


@dataclass
class PipelineResult:
    """Aggregate outcome of an ordered sequence of stages."""

    stage_results: list[StageResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(r.failed for r in self.stage_results):
            return "failed"
        return "succeeded"

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stage_count": len(self.stage_results),
            "succeeded": sum(1 for r in self.stage_results if r.succeeded),
            "failed": sum(1 for r in self.stage_results if r.failed),
            "skipped": sum(1 for r in self.stage_results if r.status == "skipped"),
            "stages": [r.summary() for r in self.stage_results],
        }

    def logs_for(self, stage_name: str) -> list[str]:
        """Log lines accumulated by the named stage, or [] if absent."""
        for r in self.stage_results:
            if r.stage_name == stage_name:
                return r.logs
        return []


# ---------------------------------------------------------------------------
# Provenance emitter protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ProvenanceEmitter(Protocol):
    """Receives pipeline lifecycle events.  Inject one or omit for NullEmitter."""

    def on_stage_start(self, stage_name: str) -> None: ...  # noqa: E704
    def on_stage_end(self, result: StageResult) -> None: ...  # noqa: E704


class NullEmitter:

    def on_stage_start(self, stage_name: str) -> None:
        pass

    def on_stage_end(self, result: StageResult) -> None:
        pass


# ---------------------------------------------------------------------------
# Stage runner
# ---------------------------------------------------------------------------

def run_stage(
    stage: StageDefinition,
    context: dict[str, Any],
    emitter: Optional[ProvenanceEmitter] = None,
) -> StageResult:
    """Run one stage.  Exceptions are captured in the result; nothing is re-raised."""
    _emitter: ProvenanceEmitter = emitter or NullEmitter()
    _emitter.on_stage_start(stage.name)

    logs: list[str] = []
    started = time.monotonic()
    output: Any = None
    error: Optional[BaseException] = None
    status = "failed"

    try:
        output = stage.fn(context, logs)
        status = "succeeded"
    except Exception as exc:
        error = exc
        logs.append(f"ERROR: {type(exc).__name__}: {exc}")

    duration_ms = (time.monotonic() - started) * 1_000.0

    result = StageResult(
        stage_name=stage.name,
        status=status,
        output=output,
        error=error,
        logs=logs,
        duration_ms=duration_ms,
    )
    _emitter.on_stage_end(result)
    return result


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(
    stages: list[StageDefinition],
    context: dict[str, Any],
    emitter: Optional[ProvenanceEmitter] = None,
    *,
    stop_on_failure: bool = True,
) -> PipelineResult:
    """Run stages in order.  On failure with stop_on_failure=True, remaining stages
    are recorded as ``"skipped"`` and the emitter is not called for them.
    """
    pipeline_result = PipelineResult()
    halted = False

    for stage in stages:
        if halted and stop_on_failure:
            pipeline_result.stage_results.append(
                StageResult(stage_name=stage.name, status="skipped")
            )
            continue

        result = run_stage(stage, context, emitter=emitter)
        pipeline_result.stage_results.append(result)

        if result.failed:
            halted = True

    return pipeline_result
