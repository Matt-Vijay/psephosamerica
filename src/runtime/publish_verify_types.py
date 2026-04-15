"""Typed contracts for publish verification results.

Pure models only — no filesystem I/O.  Each published snapshot passes through
four named stages (manifest, profiles, evidence, zip); these types surface
counts and ok/failure state at both the stage and aggregate levels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

IssueSeverity = Literal["error", "warning"]

# Valid stage names for the publish verification pipeline.
PUBLISH_STAGES = ("manifest", "profiles", "evidence", "zip")


def path_is_confined(path: str) -> bool:
    """True when *path* is relative and does not escape the publish root.

    Rejects absolute paths and any path containing ``..`` components.
    """
    p = PurePosixPath(path)
    return not p.is_absolute() and ".." not in p.parts


# ---------------------------------------------------------------------------
# PublishVerifyIssue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishVerifyIssue:
    """A single problem found during one verification stage.

    Attributes:
        stage:    Which stage produced this issue (manifest/profiles/evidence/zip).
        message:  Human-readable description of the problem.
        severity: "error" blocks ok; "warning" is informational only.
        path:     Relative path within the publish tree, when applicable.
    """

    stage: str
    message: str
    severity: IssueSeverity
    path: str | None = field(default=None)


# ---------------------------------------------------------------------------
# PublishVerifyStageResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishVerifyStageResult:
    """Verification outcome for one named stage.

    Attributes:
        stage:   Stage name (e.g. "manifest", "profiles", "evidence", "zip").
        checked: Number of individual items inspected (files, entries, etc.).
        issues:  All issues raised during this stage.
    """

    stage: str
    checked: int
    issues: tuple[PublishVerifyIssue, ...]

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
# PublishVerifyResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishVerifyResult:
    """Aggregated verification outcome across all stages.

    Attributes:
        stages: One result per stage, in the order they were run.
    """

    stages: tuple[PublishVerifyStageResult, ...]

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

    def all_issues(self) -> list[PublishVerifyIssue]:
        """Return every issue across all stages in stage order."""
        result: list[PublishVerifyIssue] = []
        for s in self.stages:
            result.extend(s.issues)
        return result

    def stage_result(self, stage: str) -> PublishVerifyStageResult | None:
        """Look up a stage result by name; returns None when not present."""
        for s in self.stages:
            if s.stage == stage:
                return s
        return None
