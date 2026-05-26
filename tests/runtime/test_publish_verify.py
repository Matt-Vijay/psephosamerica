"""Tests for src/runtime/publish_verify.py.

Strategy
--------
verify_local_publish() calls four stage verifiers and one internal manifest
loader (_find_and_load_manifest).  All five are mocked at their import paths
so these tests exercise only the aggregation logic in publish_verify.py.

Real tmp_path directories are used — verify_local_publish receives a genuine
Path; no fake seams are introduced in the production code under test.

No network calls, no DB.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.runtime.publish_verify import verify_local_publish
from src.runtime.publish_verify_types import (
    PublishVerifyIssue,
    PublishVerifyResult,
    PublishVerifyStageResult,
)

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_VERIFY_MANIFEST = "src.runtime.publish_verify.verify_local_manifest"
_LOAD_MANIFEST = "src.runtime.publish_verify._find_and_load_manifest"
_VERIFY_PROFILES = "src.runtime.publish_verify.verify_local_member_profiles"
_VERIFY_EVIDENCE = "src.runtime.publish_verify.verify_local_evidence_cards"
_VERIFY_ONTOLOGY = "src.runtime.publish_verify.verify_local_ontology_edges"
_VERIFY_PREDICTION = "src.runtime.publish_verify.verify_local_prediction_artifacts"
_VERIFY_ZIP = "src.runtime.publish_verify.verify_local_zip_feeds"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENTINEL = object()


@pytest.fixture(autouse=True)
def _patch_prediction_stage_by_default():
    with patch(_VERIFY_PREDICTION, return_value=_ok_stage("prediction")):
        yield


def _ok_stage(name: str, checked: int = 3) -> PublishVerifyStageResult:
    return PublishVerifyStageResult(stage=name, checked=checked, issues=())


def _error_stage(name: str, message: str = "bad") -> PublishVerifyStageResult:
    issue = PublishVerifyIssue(stage=name, message=message, severity="error")
    return PublishVerifyStageResult(stage=name, checked=1, issues=(issue,))


def _warning_stage(name: str, message: str = "warn") -> PublishVerifyStageResult:
    issue = PublishVerifyIssue(stage=name, message=message, severity="warning")
    return PublishVerifyStageResult(stage=name, checked=2, issues=(issue,))


def _all_ok():
    """Return patch context managers that make all six callables succeed."""
    return (
        patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
        patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
        patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
        patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
        patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
        patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
    )


# ---------------------------------------------------------------------------
# Return type and stage count
# ---------------------------------------------------------------------------


class TestVerifyLocalPublishShape:
    def test_returns_publish_verify_result(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)

        assert isinstance(result, PublishVerifyResult)

    def test_stages_tuple_has_six_entries(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)

        assert len(result.stages) == 6


# ---------------------------------------------------------------------------
# Stage order is manifest -> profiles -> evidence -> ontology -> prediction -> zip
# ---------------------------------------------------------------------------


class TestVerifyLocalPublishStageOrder:
    def test_stage_names_in_correct_order(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)

        assert [s.stage for s in result.stages] == [
            "manifest",
            "profiles",
            "evidence",
            "ontology",
            "prediction",
            "zip",
        ]

    def test_stage_result_objects_are_preserved(self, tmp_path: Path) -> None:
        manifest_r = _ok_stage("manifest", checked=10)
        profiles_r = _ok_stage("profiles", checked=20)
        evidence_r = _ok_stage("evidence", checked=30)
        ontology_r = _ok_stage("ontology", checked=35)
        zip_r = _ok_stage("zip", checked=40)

        with (
            patch(_VERIFY_MANIFEST, return_value=manifest_r),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=profiles_r),
            patch(_VERIFY_EVIDENCE, return_value=evidence_r),
            patch(_VERIFY_ONTOLOGY, return_value=ontology_r),
            patch(_VERIFY_ZIP, return_value=zip_r),
        ):
            result = verify_local_publish(tmp_path)

        assert result.stages[0] is manifest_r
        assert result.stages[1] is profiles_r
        assert result.stages[2] is evidence_r
        assert result.stages[3] is ontology_r
        assert result.stages[4].stage == "prediction"
        assert result.stages[5] is zip_r


# ---------------------------------------------------------------------------
# Root forwarding and manifest passing
# ---------------------------------------------------------------------------


class TestVerifyLocalPublishCallArgs:
    def test_root_passed_to_verify_manifest(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")) as m,
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            verify_local_publish(tmp_path)

        m.assert_called_once_with(tmp_path)

    def test_root_passed_to_find_and_load_manifest(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")) as m,
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            verify_local_publish(tmp_path)

        m.assert_called_once_with(tmp_path)

    def test_loaded_manifest_passed_to_profiles_verifier(self, tmp_path: Path) -> None:
        sentinel = object()
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(sentinel, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")) as m,
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            verify_local_publish(tmp_path)

        m.assert_called_once_with(tmp_path, sentinel)

    def test_loaded_manifest_passed_to_evidence_verifier(self, tmp_path: Path) -> None:
        sentinel = object()
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(sentinel, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")) as m,
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            verify_local_publish(tmp_path)

        m.assert_called_once_with(tmp_path, sentinel)

    def test_loaded_manifest_passed_to_ontology_verifier(self, tmp_path: Path) -> None:
        sentinel = object()
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(sentinel, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")) as m,
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            verify_local_publish(tmp_path)

        m.assert_called_once_with(tmp_path, sentinel)

    def test_loaded_manifest_passed_to_zip_verifier(self, tmp_path: Path) -> None:
        sentinel = object()
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(sentinel, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")) as m,
        ):
            verify_local_publish(tmp_path)

        m.assert_called_once_with(tmp_path, sentinel)


# ---------------------------------------------------------------------------
# Manifest unavailable: downstream stages are error results, not skipped silently
# ---------------------------------------------------------------------------


class TestVerifyLocalPublishManifestUnavailable:
    _REASON = "no manifest files found"

    def _run_no_manifest(self, tmp_path: Path) -> PublishVerifyResult:
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(None, self._REASON)),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)
        return result

    def test_still_returns_six_stages_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert len(result.stages) == 6

    def test_stage_order_preserved_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert [s.stage for s in result.stages] == [
            "manifest",
            "profiles",
            "evidence",
            "ontology",
            "prediction",
            "zip",
        ]

    def test_profiles_stage_is_error_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert not result.stages[1].ok

    def test_evidence_stage_is_error_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert not result.stages[2].ok

    def test_zip_stage_is_error_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert not result.stages[5].ok

    def test_downstream_verifiers_not_called_when_manifest_missing(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(None, self._REASON)),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")) as m_prof,
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")) as m_ev,
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")) as m_ont,
            patch(_VERIFY_PREDICTION, return_value=_ok_stage("prediction")) as m_pred,
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")) as m_zip,
        ):
            verify_local_publish(tmp_path)

        m_prof.assert_not_called()
        m_ev.assert_not_called()
        m_ont.assert_not_called()
        m_pred.assert_not_called()
        m_zip.assert_not_called()

    def test_aggregate_not_ok_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert not result.ok

    def test_skip_reason_appears_in_downstream_issues(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        for stage in result.stages[1:]:
            assert any(self._REASON in i.message for i in stage.issues)


# ---------------------------------------------------------------------------
# Aggregate ok flag
# ---------------------------------------------------------------------------


class TestVerifyLocalPublishOkFlag:
    def test_ok_when_all_stages_pass(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)

        assert result.ok is True

    def test_not_ok_when_manifest_stage_has_error(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_error_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)

        assert result.ok is False

    def test_not_ok_when_zip_stage_has_error(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_error_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)

        assert result.ok is False

    def test_ok_when_only_warnings_present(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_warning_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_warning_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_warning_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)

        assert result.ok is True


# ---------------------------------------------------------------------------
# Aggregate counts
# ---------------------------------------------------------------------------


class TestVerifyLocalPublishAggregateCounts:
    def test_total_checked_sums_across_stages(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest", checked=5)),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles", checked=10)),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence", checked=15)),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology", checked=2)),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip", checked=20)),
        ):
            result = verify_local_publish(tmp_path)

        assert result.total_checked == 55

    def test_total_errors_sums_across_stages(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_error_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_error_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)

        assert result.total_errors == 2

    def test_total_warnings_sums_across_stages(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_warning_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_warning_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_warning_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)

        assert result.total_warnings == 3

    def test_all_issues_in_stage_order(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_error_stage("manifest", "m-err")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_warning_stage("evidence", "e-warn")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_error_stage("zip", "z-err")),
        ):
            result = verify_local_publish(tmp_path)

        issues = result.all_issues()
        assert len(issues) == 3
        assert issues[0].stage == "manifest"
        assert issues[1].stage == "evidence"
        assert issues[2].stage == "zip"


# ---------------------------------------------------------------------------
# stage_result lookup
# ---------------------------------------------------------------------------


class TestVerifyLocalPublishStageLookup:
    def test_stage_result_lookup_by_name(self, tmp_path: Path) -> None:
        evidence_r = _ok_stage("evidence", checked=7)

        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=evidence_r),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)

        assert result.stage_result("evidence") is evidence_r

    def test_stage_result_returns_none_for_unknown_name(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_MANIFEST, return_value=_ok_stage("manifest")),
            patch(_LOAD_MANIFEST, return_value=(_SENTINEL, "")),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ONTOLOGY, return_value=_ok_stage("ontology")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        ):
            result = verify_local_publish(tmp_path)

        assert result.stage_result("nonexistent") is None
