"""Tests for publish_verify_types — pure model behaviour, no I/O."""

from __future__ import annotations

import pytest

from src.runtime.publish_verify_types import (
    PUBLISH_STAGES,
    PublishVerifyIssue,
    PublishVerifyResult,
    PublishVerifyStageResult,
    path_is_confined,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _issue(
    stage: str = "manifest",
    message: str = "something wrong",
    severity: str = "error",
    path: str | None = None,
) -> PublishVerifyIssue:
    return PublishVerifyIssue(stage=stage, message=message, severity=severity, path=path)  # type: ignore[arg-type]


def _stage(
    stage: str = "manifest",
    checked: int = 1,
    *issues: PublishVerifyIssue,
) -> PublishVerifyStageResult:
    return PublishVerifyStageResult(stage=stage, checked=checked, issues=tuple(issues))


# ---------------------------------------------------------------------------
# PublishVerifyIssue
# ---------------------------------------------------------------------------


class TestPublishVerifyIssue:
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


# ---------------------------------------------------------------------------
# PublishVerifyStageResult
# ---------------------------------------------------------------------------


class TestPublishVerifyStageResult:
    def test_ok_when_no_issues(self) -> None:
        result = _stage("manifest", 5)
        assert result.ok is True

    def test_ok_when_only_warnings(self) -> None:
        w = _issue(severity="warning")
        result = _stage("manifest", 3, w)
        assert result.ok is True

    def test_not_ok_when_error(self) -> None:
        e = _issue(severity="error")
        result = _stage("manifest", 3, e)
        assert result.ok is False

    def test_error_count(self) -> None:
        issues = (_issue(severity="error"), _issue(severity="warning"), _issue(severity="error"))
        result = PublishVerifyStageResult(stage="evidence", checked=10, issues=issues)
        assert result.error_count == 2

    def test_warning_count(self) -> None:
        issues = (_issue(severity="warning"), _issue(severity="error"))
        result = PublishVerifyStageResult(stage="zip", checked=4, issues=issues)
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


# ---------------------------------------------------------------------------
# PublishVerifyResult
# ---------------------------------------------------------------------------


class TestPublishVerifyResult:
    def _build(self, *stages: PublishVerifyStageResult) -> PublishVerifyResult:
        return PublishVerifyResult(stages=tuple(stages))

    def test_ok_all_clean(self) -> None:
        result = self._build(
            _stage("manifest", 1),
            _stage("profiles", 10),
        )
        assert result.ok is True

    def test_not_ok_one_error_stage(self) -> None:
        result = self._build(
            _stage("manifest", 1),
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
            _stage("manifest", 3),
            _stage("profiles", 7),
            _stage("evidence", 20),
        )
        assert result.total_checked == 30

    def test_total_errors(self) -> None:
        result = self._build(
            _stage("manifest", 1, _issue(severity="error")),
            _stage("profiles", 5, _issue(severity="error"), _issue(severity="warning")),
        )
        assert result.total_errors == 2

    def test_total_warnings(self) -> None:
        result = self._build(
            _stage("manifest", 1, _issue(severity="warning")),
            _stage("profiles", 5, _issue(severity="warning")),
        )
        assert result.total_warnings == 2

    def test_all_issues_order(self) -> None:
        e1 = _issue("manifest", "m1")
        e2 = _issue("profiles", "p1")
        e3 = _issue("profiles", "p2")
        result = self._build(
            _stage("manifest", 1, e1),
            _stage("profiles", 2, e2, e3),
        )
        assert result.all_issues() == [e1, e2, e3]

    def test_all_issues_empty(self) -> None:
        result = self._build(_stage("manifest", 0))
        assert result.all_issues() == []

    def test_stage_result_found(self) -> None:
        s = _stage("zip", 8)
        result = self._build(s)
        assert result.stage_result("zip") is s

    def test_stage_result_missing(self) -> None:
        result = self._build(_stage("manifest", 1))
        assert result.stage_result("zip") is None

    def test_stage_result_first_match(self) -> None:
        s1 = _stage("evidence", 3)
        result = self._build(s1, _stage("zip", 1))
        assert result.stage_result("evidence") is s1

    def test_frozen(self) -> None:
        result = self._build()
        with pytest.raises(Exception):
            result.stages = ()  # type: ignore[misc]

    def test_empty_stages(self) -> None:
        result = PublishVerifyResult(stages=())
        assert result.ok is True
        assert result.total_checked == 0
        assert result.total_errors == 0
        assert result.all_issues() == []


# ---------------------------------------------------------------------------
# PUBLISH_STAGES constant
# ---------------------------------------------------------------------------


class TestPublishStages:
    def test_contains_all_six(self) -> None:
        assert set(PUBLISH_STAGES) == {
            "manifest",
            "profiles",
            "evidence",
            "ontology",
            "prediction",
            "zip",
        }

    def test_is_tuple(self) -> None:
        assert isinstance(PUBLISH_STAGES, tuple)


# ---------------------------------------------------------------------------
# path_is_confined
# ---------------------------------------------------------------------------


class TestPathIsConfined:
    def test_simple_relative_path(self) -> None:
        assert path_is_confined("members/nancy-pelosi.json") is True

    def test_nested_relative_path(self) -> None:
        assert path_is_confined("evidence/ec-001.json") is True

    def test_dotdot_rejected(self) -> None:
        assert path_is_confined("../etc/passwd") is False

    def test_embedded_dotdot_rejected(self) -> None:
        assert path_is_confined("members/../../etc/passwd") is False

    def test_absolute_path_rejected(self) -> None:
        assert path_is_confined("/etc/passwd") is False

    def test_bare_filename(self) -> None:
        assert path_is_confined("manifest.json") is True

    def test_empty_string(self) -> None:
        assert path_is_confined("") is True
