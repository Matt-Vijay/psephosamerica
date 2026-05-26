"""Typed contracts for publish roundtrip verification.

Pure models/helpers only — no filesystem or DB I/O.

A roundtrip check walks the full DB→publish pipeline and confirms that
each output stage produced coherent, non-empty artefacts.  Seven named
stages are defined:

    snapshot   – the snapshot row and manifest were written correctly
    profiles   – per-member profile JSON was emitted for every member
    evidence   – evidence-card JSON was emitted for every card
    ontology   – ontology graph JSON matches canonical ontology DB rows
    prediction – prediction/simulation JSON matches DB-derived inputs
    zip        – per-ZIP feed JSON was emitted for every mapped ZIP
    homepage   – the homepage/feed JSON matches the read model
    lookup     – the compact current-member lookup matches member profiles

These types surface issue counts and ok/failure state at both the stage
and aggregate levels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

IssueSeverity = Literal["error", "warning"]

# Ordered stage names for the publish roundtrip pipeline.
ROUNDTRIP_STAGES = (
    "snapshot",
    "profiles",
    "evidence",
    "ontology",
    "prediction",
    "zip",
    "homepage",
    "lookup",
)


# ---------------------------------------------------------------------------
# PublishRoundtripIssue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishRoundtripIssue:
    """A single problem found during one roundtrip stage.

    Attributes:
        stage:    Which stage produced this issue (snapshot/profiles/evidence/zip/homepage/lookup).
        message:  Human-readable description of the problem.
        severity: ``"error"`` blocks ok; ``"warning"`` is informational only.
        path:     Relative path within the publish tree, when applicable.
    """

    stage: str
    message: str
    severity: IssueSeverity
    path: str | None = field(default=None)


# ---------------------------------------------------------------------------
# PublishRoundtripStageResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishRoundtripStageResult:
    """Roundtrip outcome for one named stage.

    Attributes:
        stage:   Stage name (e.g. ``"snapshot"``, ``"profiles"``).
        checked: Number of individual items verified (rows, files, etc.).
        issues:  All issues raised during this stage.
    """

    stage: str
    checked: int
    issues: tuple[PublishRoundtripIssue, ...]

    @property
    def ok(self) -> bool:
        """True when no error-severity issues were found."""
        return not any(i.severity == "error" for i in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")


# ---------------------------------------------------------------------------
# PublishRoundtripResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishRoundtripResult:
    """Aggregated roundtrip outcome across all pipeline stages.

    Attributes:
        stages: One result per stage, in the order they were run.
    """

    stages: tuple[PublishRoundtripStageResult, ...]

    @property
    def ok(self) -> bool:
        """True when every stage passed (no error-severity issues)."""
        return all(s.ok for s in self.stages)

    @property
    def total_checked(self) -> int:
        """Sum of checked counts across all stages."""
        return sum(s.checked for s in self.stages)

    @property
    def total_errors(self) -> int:
        return sum(s.error_count for s in self.stages)

    @property
    def total_warnings(self) -> int:
        return sum(s.warning_count for s in self.stages)

    def all_issues(self) -> list[PublishRoundtripIssue]:
        """Return every issue across all stages in stage order."""
        result: list[PublishRoundtripIssue] = []
        for s in self.stages:
            result.extend(s.issues)
        return result

    def stage_result(self, stage: str) -> PublishRoundtripStageResult | None:
        """Look up a stage result by name; returns None when not present."""
        for s in self.stages:
            if s.stage == stage:
                return s
        return None
