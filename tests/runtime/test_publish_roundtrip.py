"""Tests for src/runtime/publish_roundtrip.py.

Strategy
--------
verify_publish_roundtrip() calls one internal stage function (_verify_snapshot)
and four imported stage verifiers.  The four downstream verifiers are patched
at their import paths so these tests exercise only the aggregation and
orchestration logic in publish_roundtrip.py.

The snapshot stage uses real temp publish trees written by
PublishedSnapshotBuilder so the filesystem path through _verify_snapshot is
exercised with genuine files rather than mocked seams.

No network calls, no DB queries.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.runtime.publish_roundtrip import _verify_snapshot, verify_publish_roundtrip
from src.runtime.publish_roundtrip_types import (
    PublishRoundtripIssue,
    PublishRoundtripResult,
    PublishRoundtripStageResult,
)
from tests.support.published_snapshot_fixtures import PublishedSnapshotBuilder

# ---------------------------------------------------------------------------
# Patch targets (downstream stage verifiers imported into publish_roundtrip)
# ---------------------------------------------------------------------------

_VERIFY_PROFILES = (
    "src.runtime.publish_roundtrip.verify_published_member_profiles_roundtrip"
)
_VERIFY_EVIDENCE = (
    "src.runtime.publish_roundtrip.verify_published_evidence_roundtrip"
)
_VERIFY_ZIP = "src.runtime.publish_roundtrip.verify_published_zip_roundtrip"
_VERIFY_HOMEPAGE = "src.runtime.publish_roundtrip.verify_published_homepage_roundtrip"
_VERIFY_SNAPSHOT = "src.runtime.publish_roundtrip._verify_snapshot"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_stage(name: str, checked: int = 3) -> PublishRoundtripStageResult:
    return PublishRoundtripStageResult(stage=name, checked=checked, issues=())


def _error_stage(name: str, message: str = "bad") -> PublishRoundtripStageResult:
    issue = PublishRoundtripIssue(stage=name, message=message, severity="error")
    return PublishRoundtripStageResult(stage=name, checked=1, issues=(issue,))


def _warning_stage(name: str, message: str = "warn") -> PublishRoundtripStageResult:
    issue = PublishRoundtripIssue(stage=name, message=message, severity="warning")
    return PublishRoundtripStageResult(stage=name, checked=2, issues=(issue,))


_FAKE_CONN = object()

# A real SnapshotManifest-shaped sentinel for patching _verify_snapshot to succeed.
# We build one from the support fixtures rather than hardcoding raw dict values.


def _real_manifest(tmp_path: Path):
    """Write a real publish tree and return the manifest loaded from it."""
    from src.export.manifest import SnapshotManifest

    snap = PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build().snapshot()
    manifest_file = next((snap.root / "snapshots").glob("*/manifest.json"))
    return SnapshotManifest.model_validate(json.loads(manifest_file.read_bytes()))


def _manifest_stub(snapshot_id: str = "2026-01-01") -> SimpleNamespace:
    return SimpleNamespace(snapshot_id=snapshot_id, created_at=dt.datetime(2026, 1, 1, 0, 0, 0))


def _all_ok_patches(manifest_obj=None):
    """Return a stack of context managers that make all downstream verifiers succeed."""
    if manifest_obj is None:
        manifest_obj = _manifest_stub()
    return (
        patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), manifest_obj)),
        patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
        patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
        patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
        patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
    )


# ---------------------------------------------------------------------------
# Return type and stage count
# ---------------------------------------------------------------------------


class TestVerifyPublishRoundtripShape:
    def test_returns_publish_roundtrip_result(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert isinstance(result, PublishRoundtripResult)

    def test_stages_tuple_has_five_entries(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert len(result.stages) == 5


# ---------------------------------------------------------------------------
# Stage order is snapshot -> profiles -> evidence -> zip -> homepage
# ---------------------------------------------------------------------------


class TestVerifyPublishRoundtripStageOrder:
    def test_stage_names_in_correct_order(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert [s.stage for s in result.stages] == [
            "snapshot",
            "profiles",
            "evidence",
            "zip",
            "homepage",
        ]

    def test_stage_result_objects_are_preserved(self, tmp_path: Path) -> None:
        snapshot_r = _ok_stage("snapshot", checked=10)
        profiles_r = _ok_stage("profiles", checked=20)
        evidence_r = _ok_stage("evidence", checked=30)
        zip_r = _ok_stage("zip", checked=40)
        homepage_r = _ok_stage("homepage", checked=50)

        with (
            patch(_VERIFY_SNAPSHOT, return_value=(snapshot_r, _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=profiles_r),
            patch(_VERIFY_EVIDENCE, return_value=evidence_r),
            patch(_VERIFY_ZIP, return_value=zip_r),
            patch(_VERIFY_HOMEPAGE, return_value=homepage_r),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert result.stages[0] is snapshot_r
        assert result.stages[1] is profiles_r
        assert result.stages[2] is evidence_r
        assert result.stages[3] is zip_r
        assert result.stages[4] is homepage_r


# ---------------------------------------------------------------------------
# Conn and root forwarding to downstream verifiers
# ---------------------------------------------------------------------------


class TestVerifyPublishRoundtripCallArgs:
    def test_conn_forwarded_to_profiles_verifier(self, tmp_path: Path) -> None:
        sentinel_conn = object()
        sentinel_manifest = _manifest_stub()
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), sentinel_manifest)),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")) as m,
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            verify_publish_roundtrip(sentinel_conn, tmp_path)

        m.assert_called_once_with(sentinel_conn, tmp_path, sentinel_manifest)

    def test_conn_forwarded_to_evidence_verifier(self, tmp_path: Path) -> None:
        sentinel_conn = object()
        sentinel_manifest = _manifest_stub()
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), sentinel_manifest)),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")) as m,
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            verify_publish_roundtrip(sentinel_conn, tmp_path)

        m.assert_called_once_with(sentinel_conn, tmp_path, sentinel_manifest)

    def test_conn_forwarded_to_zip_verifier(self, tmp_path: Path) -> None:
        sentinel_conn = object()
        sentinel_manifest = _manifest_stub("2026-02-03")
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), sentinel_manifest)),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")) as m,
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            verify_publish_roundtrip(sentinel_conn, tmp_path)

        m.assert_called_once_with(
            sentinel_conn,
            tmp_path,
            sentinel_manifest,
            dt.date(2026, 2, 3),
        )

    def test_conn_forwarded_to_homepage_verifier(self, tmp_path: Path) -> None:
        sentinel_conn = object()
        sentinel_manifest = _manifest_stub("2026-02-03")
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), sentinel_manifest)),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")) as m,
        ):
            verify_publish_roundtrip(sentinel_conn, tmp_path)

        m.assert_called_once_with(sentinel_conn, tmp_path, dt.date(2026, 2, 3))

    def test_root_forwarded_to_profiles_verifier(self, tmp_path: Path) -> None:
        sentinel_manifest = _manifest_stub()
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), sentinel_manifest)),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")) as m,
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert m.call_args[0][1] == tmp_path


# ---------------------------------------------------------------------------
# Manifest unavailable: all downstream stages are error results, not skipped
# ---------------------------------------------------------------------------


class TestVerifyPublishRoundtripManifestUnavailable:
    def _run_no_manifest(self, tmp_path: Path) -> PublishRoundtripResult:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_error_stage("snapshot", "no manifest"), None)),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")) as m_prof,
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")) as m_ev,
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")) as m_zip,
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")) as m_home,
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)
        # Downstream verifiers must not be called when manifest is None.
        m_prof.assert_not_called()
        m_ev.assert_not_called()
        m_zip.assert_not_called()
        m_home.assert_not_called()
        return result

    def test_still_returns_five_stages_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert len(result.stages) == 5

    def test_stage_order_preserved_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert [s.stage for s in result.stages] == [
            "snapshot", "profiles", "evidence", "zip", "homepage"
        ]

    def test_profiles_stage_is_error_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert not result.stages[1].ok

    def test_evidence_stage_is_error_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert not result.stages[2].ok

    def test_zip_stage_is_error_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert not result.stages[3].ok

    def test_homepage_stage_is_error_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert not result.stages[4].ok

    def test_aggregate_not_ok_when_manifest_missing(self, tmp_path: Path) -> None:
        result = self._run_no_manifest(tmp_path)
        assert not result.ok


# ---------------------------------------------------------------------------
# Aggregate ok flag
# ---------------------------------------------------------------------------


class TestVerifyPublishRoundtripOkFlag:
    def test_ok_when_all_stages_pass(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert result.ok is True

    def test_not_ok_when_snapshot_stage_has_error(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_error_stage("snapshot"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert result.ok is False

    def test_not_ok_when_homepage_stage_has_error(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_error_stage("homepage")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert result.ok is False

    def test_ok_when_only_warnings_present(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_warning_stage("snapshot"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_warning_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert result.ok is True


# ---------------------------------------------------------------------------
# Aggregate counts
# ---------------------------------------------------------------------------


class TestVerifyPublishRoundtripAggregateCounts:
    def test_total_checked_sums_across_five_stages(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot", checked=5), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles", checked=10)),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence", checked=15)),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip", checked=20)),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage", checked=25)),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert result.total_checked == 75

    def test_total_errors_sums_across_stages(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_error_stage("snapshot"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_error_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert result.total_errors == 2

    def test_total_warnings_sums_across_stages(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_warning_stage("snapshot"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_warning_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_warning_stage("homepage")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert result.total_warnings == 3

    def test_all_issues_in_stage_order(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_error_stage("snapshot", "snap-err"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_warning_stage("evidence", "ev-warn")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_error_stage("homepage", "home-err")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        issues = result.all_issues()
        assert len(issues) == 3
        assert issues[0].stage == "snapshot"
        assert issues[1].stage == "evidence"
        assert issues[2].stage == "homepage"


# ---------------------------------------------------------------------------
# stage_result lookup
# ---------------------------------------------------------------------------


class TestVerifyPublishRoundtripStageLookup:
    def test_stage_result_lookup_by_name(self, tmp_path: Path) -> None:
        evidence_r = _ok_stage("evidence", checked=7)

        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=evidence_r),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert result.stage_result("evidence") is evidence_r

    def test_stage_result_returns_none_for_unknown_name(self, tmp_path: Path) -> None:
        with (
            patch(_VERIFY_SNAPSHOT, return_value=(_ok_stage("snapshot"), _manifest_stub())),
            patch(_VERIFY_PROFILES, return_value=_ok_stage("profiles")),
            patch(_VERIFY_EVIDENCE, return_value=_ok_stage("evidence")),
            patch(_VERIFY_ZIP, return_value=_ok_stage("zip")),
            patch(_VERIFY_HOMEPAGE, return_value=_ok_stage("homepage")),
        ):
            result = verify_publish_roundtrip(_FAKE_CONN, tmp_path)

        assert result.stage_result("nonexistent") is None


# ---------------------------------------------------------------------------
# _verify_snapshot: real temp publish trees
# ---------------------------------------------------------------------------


class TestVerifySnapshot:
    def test_ok_with_valid_publish_tree(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        stage, manifest = _verify_snapshot(tmp_path)
        assert stage.ok
        assert manifest is not None

    def test_error_when_manifest_hashes_do_not_match_tree(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        member_file = next((tmp_path / "members").glob("*.json"))
        member_file.write_bytes(b'{"tampered": true}')

        stage, manifest = _verify_snapshot(tmp_path)

        assert not stage.ok
        assert manifest is None
        assert any("sha256 mismatch" in issue.message for issue in stage.issues)

    def test_checked_equals_artifact_count(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        stage, manifest = _verify_snapshot(tmp_path)
        assert stage.checked > 0
        assert manifest is not None
        assert stage.checked == len(manifest.entries)

    def test_snapshot_stage_ignores_unmanifested_homepage_file(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        homepage = tmp_path / "homepage" / "feed.json"
        homepage.parent.mkdir(parents=True, exist_ok=True)
        homepage.write_text('{"items":[]}', encoding="utf-8")

        stage, manifest = _verify_snapshot(tmp_path)

        assert stage.ok is True
        assert manifest is not None
        assert stage.checked == len(manifest.entries)
        assert all(issue.path != "homepage/feed.json" for issue in stage.issues)

    def test_error_when_no_snapshots_dir(self, tmp_path: Path) -> None:
        stage, manifest = _verify_snapshot(tmp_path)
        assert not stage.ok
        assert manifest is None
        assert stage.stage == "snapshot"

    def test_error_when_no_manifest_file(self, tmp_path: Path) -> None:
        (tmp_path / "snapshots").mkdir()
        stage, manifest = _verify_snapshot(tmp_path)
        assert not stage.ok
        assert manifest is None

    def test_error_when_multiple_manifests(self, tmp_path: Path) -> None:
        for snap_id in ("2026-01-01", "2026-01-02"):
            (tmp_path / "snapshots" / snap_id).mkdir(parents=True)
            (tmp_path / "snapshots" / snap_id / "manifest.json").write_text(
                json.dumps(
                    {
                        "snapshot_id": snap_id,
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "entries": [],
                        "total_files": 0,
                        "total_bytes": 0,
                    }
                )
            )
        stage, manifest = _verify_snapshot(tmp_path)
        assert not stage.ok
        assert manifest is None

    def test_error_when_manifest_is_invalid_json(self, tmp_path: Path) -> None:
        snap_dir = tmp_path / "snapshots" / "2026-01-01"
        snap_dir.mkdir(parents=True)
        (snap_dir / "manifest.json").write_bytes(b"not-json")
        stage, manifest = _verify_snapshot(tmp_path)
        assert not stage.ok
        assert manifest is None

    def test_error_when_manifest_omits_root_sha256(self, tmp_path: Path) -> None:
        snap_dir = tmp_path / "snapshots" / "2026-01-01"
        snap_dir.mkdir(parents=True)
        (snap_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "snapshot_id": "2026-01-01",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "entries": [],
                    "total_files": 0,
                    "total_bytes": 0,
                }
            )
        )

        stage, manifest = _verify_snapshot(tmp_path)

        assert not stage.ok
        assert manifest is None
        assert any("root_sha256" in issue.message for issue in stage.issues)

    def test_error_when_manifest_fails_schema_validation(self, tmp_path: Path) -> None:
        snap_dir = tmp_path / "snapshots" / "2026-01-01"
        snap_dir.mkdir(parents=True)
        (snap_dir / "manifest.json").write_text(json.dumps({"snapshot_id": "bad"}))
        stage, manifest = _verify_snapshot(tmp_path)
        assert not stage.ok
        assert manifest is None

    def test_stage_name_is_snapshot(self, tmp_path: Path) -> None:
        stage, _ = _verify_snapshot(tmp_path)
        assert stage.stage == "snapshot"

    def test_manifest_returned_on_success(self, tmp_path: Path) -> None:
        from src.export.manifest import SnapshotManifest

        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-03-15").build()
        stage, manifest = _verify_snapshot(tmp_path)
        assert isinstance(manifest, SnapshotManifest)
        assert manifest.snapshot_id == "2026-03-15"
