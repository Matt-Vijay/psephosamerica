"""Typed contracts for history aggregate verification results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


IssueSeverity = Literal["error", "warning"]

HISTORY_VERIFY_STAGES = (
    "snapshot_index",
    "bootstrap",
    "current_aggregates",
    "snapshot_presets",
    "members",
    "member_pages",
)


@dataclass(frozen=True)
class HistoryVerifyIssue:
    stage: str
    message: str
    severity: IssueSeverity
    path: str | None = field(default=None)


@dataclass(frozen=True)
class HistoryVerifyStageResult:
    stage: str
    checked: int
    issues: tuple[HistoryVerifyIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")


@dataclass(frozen=True)
class HistoryVerifyResult:
    stages: tuple[HistoryVerifyStageResult, ...]

    @property
    def ok(self) -> bool:
        return all(stage.ok for stage in self.stages)

    @property
    def total_checked(self) -> int:
        return sum(stage.checked for stage in self.stages)

    @property
    def total_errors(self) -> int:
        return sum(stage.error_count for stage in self.stages)

    @property
    def total_warnings(self) -> int:
        return sum(stage.warning_count for stage in self.stages)

    def all_issues(self) -> list[HistoryVerifyIssue]:
        issues: list[HistoryVerifyIssue] = []
        for stage in self.stages:
            issues.extend(stage.issues)
        return issues

    def stage_result(self, stage: str) -> HistoryVerifyStageResult | None:
        for result in self.stages:
            if result.stage == stage:
                return result
        return None
