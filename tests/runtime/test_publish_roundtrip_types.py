"""Tests for publish_roundtrip_types — pure model behaviour, no I/O."""

from __future__ import annotations

import pytest

from src.runtime.publish_roundtrip_types import (
    ROUNDTRIP_STAGES,
    PublishRoundtripIssue,
    PublishRoundtripResult,
    PublishRoundtripStageResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _issue(
    stage: str = "snapshot",
    message: str = "something wrong",
    severity: str = "error",
    path: str | None = None,
) -> PublishRoundtripIssue:
    return PublishRoundtripIssue(stage=stage, message=message, severity=severity, path=path)  # type: ignore[arg-type]


def _stage(
    stage: str = "snapshot",
    checked: int = 1,
    *issues: PublishRoundtripIssue,
) -> PublishRoundtripStageResult:
    return PublishRoundtripStageResult(stage=stage, checked=checked, issues=tuple(issues))


# ---------------------------------------------------------------------------
# PublishRoundtripIssue
# ---------------------------------------------------------------------------


class TestPublishRoundtripIssue:
    def test_frozen(self) -> None:
        issue = _issue()
        with pytest.raises(Exception):
            issue.message = "mutated"  # type: ignore[misc]

    def test_path_defaults_to_none(self) -> None:
        issue = _issue()
        assert issue.path is None

    def test_path_set(self) -> None:
        issue = _issue(path="members/a-b.json")
        assert issue.path == "members/a-b.json"

    def test_warning_severity(self) -> None:
        issue = _issue(severity="warning")
        assert issue.severity == "warning"

    def test_error_severity(self) -> None:
        issue = _issue(severity="error")
        assert issue.severity == "error"

    def test_stage_stored(self) -> None:
        issue = _issue(stage="homepage")
        assert issue.stage == "homepage"

    def test_message_stored(self) -> None:
        issue = _issue(message="missing file")
        assert issue.message == "missing file"


# ---------------------------------------------------------------------------
# PublishRoundtripStageResult
# ---------------------------------------------------------------------------


class TestPublishRoundtripStageResult:
    def test_ok_when_no_issues(self) -> None:
        result = _stage("snapshot", 5)
        assert result.ok is True

    def test_ok_when_only_warnings(self) -> None:
        w = _issue(severity="warning")
        result = _stage("profiles", 3, w)
        assert result.ok is True

    def test_not_ok_when_error(self) -> None:
        e = _issue(severity="error")
        result = _stage("evidence", 3, e)
        assert result.ok is False

    def test_error_count(self) -> None:
        issues = (_issue(severity="error"), _issue(severity="warning"), _issue(severity="error"))
        result = PublishRoundtripStageResult(stage="zip", checked=10, issues=issues)
        assert result.error_count == 2

    def test_warning_count(self) -> None:
        issues = (_issue(severity="warning"), _issue(severity="error"))
        result = PublishRoundtripStageResult(stage="homepage", checked=4, issues=issues)
        assert result.warning_count == 1

    def test_zero_counts_when_empty(self) -> None:
        result = _stage("profiles", 0)
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_frozen(self) -> None:
        result = _stage()
        with pytest.raises(Exception):
            result.checked = 99  # type: ignore[misc]

    def test_checked_stored(self) -> None:
        result = _stage("zip", 42)
        assert result.checked == 42

    def test_stage_name_stored(self) -> None:
        result = _stage("homepage", 7)
        assert result.stage == "homepage"


# ---------------------------------------------------------------------------
# PublishRoundtripResult
# ---------------------------------------------------------------------------


class TestPublishRoundtripResult:
    def _build(self, *stages: PublishRoundtripStageResult) -> PublishRoundtripResult:
        return PublishRoundtripResult(stages=tuple(stages))

    def test_ok_all_clean(self) -> None:
        result = self._build(
            _stage("snapshot", 1),
            _stage("profiles", 10),
            _stage("evidence", 5),
            _stage("zip", 3),
            _stage("homepage", 1),
        )
        assert result.ok is True

    def test_not_ok_one_error_stage(self) -> None:
        result = self._build(
            _stage("snapshot", 1),
            _stage("profiles", 10, _issue(severity="error")),
        )
        assert result.ok is False

    def test_ok_warnings_only(self) -> None:
        result = self._build(
            _stage("evidence", 5, _issue(severity="warning")),
        )
        assert result.ok is True

    def test_total_checked(self) -> None:
        result = self._build(
            _stage("snapshot", 1),
            _stage("profiles", 8),
            _stage("evidence", 20),
            _stage("zip", 3),
            _stage("homepage", 1),
        )
        assert result.total_checked == 33

    def test_total_errors(self) -> None:
        result = self._build(
            _stage("snapshot", 1, _issue(severity="error")),
            _stage("profiles", 5, _issue(severity="error"), _issue(severity="warning")),
        )
        assert result.total_errors == 2

    def test_total_warnings(self) -> None:
        result = self._build(
            _stage("snapshot", 1, _issue(severity="warning")),
            _stage("homepage", 1, _issue(severity="warning")),
        )
        assert result.total_warnings == 2

    def test_all_issues_order(self) -> None:
        e1 = _issue("snapshot", "s1")
        e2 = _issue("profiles", "p1")
        e3 = _issue("profiles", "p2")
        result = self._build(
            _stage("snapshot", 1, e1),
            _stage("profiles", 2, e2, e3),
        )
        assert result.all_issues() == [e1, e2, e3]

    def test_all_issues_empty(self) -> None:
        result = self._build(_stage("snapshot", 0))
        assert result.all_issues() == []

    def test_stage_result_found(self) -> None:
        s = _stage("homepage", 8)
        result = self._build(s)
        assert result.stage_result("homepage") is s

    def test_stage_result_missing(self) -> None:
        result = self._build(_stage("snapshot", 1))
        assert result.stage_result("homepage") is None

    def test_stage_result_first_match(self) -> None:
        s1 = _stage("evidence", 3)
        result = self._build(s1, _stage("zip", 1))
        assert result.stage_result("evidence") is s1

    def test_frozen(self) -> None:
        result = self._build()
        with pytest.raises(Exception):
            result.stages = ()  # type: ignore[misc]

    def test_empty_stages(self) -> None:
        result = PublishRoundtripResult(stages=())
        assert result.ok is True
        assert result.total_checked == 0
        assert result.total_errors == 0
        assert result.all_issues() == []

    def test_not_ok_homepage_error(self) -> None:
        result = self._build(
            _stage("snapshot", 1),
            _stage("profiles", 5),
            _stage("evidence", 10),
            _stage("zip", 3),
            _stage("homepage", 1, _issue("homepage", "feed empty", "error")),
        )
        assert result.ok is False

    def test_total_errors_across_all_five_stages(self) -> None:
        result = self._build(
            _stage("snapshot", 1, _issue("snapshot", "bad", "error")),
            _stage("profiles", 2, _issue("profiles", "bad", "error")),
            _stage("evidence", 3, _issue("evidence", "bad", "error")),
            _stage("zip", 4, _issue("zip", "bad", "error")),
            _stage("homepage", 5, _issue("homepage", "bad", "error")),
        )
        assert result.total_errors == 5
        assert result.total_checked == 15


# ---------------------------------------------------------------------------
# ROUNDTRIP_STAGES constant
# ---------------------------------------------------------------------------


class TestRoundtripStages:
    def test_contains_all_five(self) -> None:
        assert set(ROUNDTRIP_STAGES) == {"snapshot", "profiles", "evidence", "zip", "homepage"}

    def test_is_tuple(self) -> None:
        assert isinstance(ROUNDTRIP_STAGES, tuple)

    def test_order(self) -> None:
        assert ROUNDTRIP_STAGES == ("snapshot", "profiles", "evidence", "zip", "homepage")

    def test_length(self) -> None:
        assert len(ROUNDTRIP_STAGES) == 5
