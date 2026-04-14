"""Pipeline stage runner — pure orchestration, no I/O."""

from .stages import (
    NullEmitter,
    PipelineResult,
    ProvenanceEmitter,
    StageDefinition,
    StageResult,
    run_pipeline,
    run_stage,
)

__all__ = [
    "NullEmitter",
    "PipelineResult",
    "ProvenanceEmitter",
    "StageDefinition",
    "StageResult",
    "run_pipeline",
    "run_stage",
]
