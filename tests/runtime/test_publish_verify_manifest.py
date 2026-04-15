"""Tests for src/runtime/publish_verify_manifest.py.

Uses real temp publish trees — no I/O mocking.
No network calls, no DB.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.export.filesystem import write_planned_files
from src.export.manifest import ManifestEntry, SnapshotManifest
from src.export.writer import (
    PlannedFile,
    manifest_path,
    member_path,
    serialize_payload,
)
from src.runtime.publish_verify_manifest import verify_local_manifest
from src.runtime.publish_verify_types import PublishVerifyStageResult

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SNAP_ID = "2026-04-14"
_CREATED_AT = datetime(2026, 4, 14, 0, 0, 0)


def _minimal_manifest(
    snapshot_id: str = _SNAP_ID,
    entries: list[ManifestEntry] | None = None,
) -> SnapshotManifest:
    if entries is None:
        entries = []
    return SnapshotManifest(
        snapshot_id=snapshot_id,
        created_at=_CREATED_AT,
        entries=entries,
        total_files=len(entries),
        total_bytes=sum(e.size_bytes for e in entries),
    )


def _write_manifest(root: Path, manifest: SnapshotManifest) -> None:
    """Write a valid serialized manifest into the publish tree."""
    planned = PlannedFile.from_bytes(
        manifest_path(manifest.snapshot_id),
        serialize_payload(manifest),
    )
    write_planned_files([planned], root)


def _write_raw(root: Path, snapshot_id: str, data: object) -> None:
    """Write arbitrary JSON as a manifest file (for error-case tests)."""
    dest = root / manifest_path(snapshot_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"))


# ---------------------------------------------------------------------------
# No snapshots directory / empty tree
# ---------------------------------------------------------------------------


class TestNoManifests:
    def test_ok_when_no_snapshots_dir(self, tmp_path: Path) -> None:
        result = verify_local_manifest(tmp_path)
        assert isinstance(result, PublishVerifyStageResult)
        assert result.ok is True
        assert result.checked == 0
        assert result.issues == ()

    def test_ok_when_snapshots_dir_exists_but_is_empty(self, tmp_path: Path) -> None:
        (tmp_path / "snapshots").mkdir()
        result = verify_local_manifest(tmp_path)
        assert result.ok is True
        assert result.checked == 0


# ---------------------------------------------------------------------------
# Valid manifests
# ---------------------------------------------------------------------------


class TestValidManifest:
    def test_ok_for_empty_entries_manifest(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, _minimal_manifest())
        result = verify_local_manifest(tmp_path)
        assert result.ok is True
        assert result.checked == 1
        assert result.issues == ()

    def test_stage_name_is_manifest(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, _minimal_manifest())
        result = verify_local_manifest(tmp_path)
        assert result.stage == "manifest"

    def test_ok_for_manifest_with_one_entry(self, tmp_path: Path) -> None:
        entry = ManifestEntry(
            path=member_path("test-member"),
            sha256="a" * 64,
            size_bytes=8,
        )
        _write_manifest(tmp_path, _minimal_manifest(entries=[entry]))
        result = verify_local_manifest(tmp_path)
        assert result.ok is True
        assert result.checked == 1

    def test_ok_for_multiple_valid_snapshots(self, tmp_path: Path) -> None:
        for sid in ("2026-04-01", "2026-04-14"):
            _write_manifest(tmp_path, _minimal_manifest(snapshot_id=sid))
        result = verify_local_manifest(tmp_path)
        assert result.ok is True
        assert result.checked == 2
        assert result.issues == ()


# ---------------------------------------------------------------------------
# Invalid JSON
# ---------------------------------------------------------------------------


class TestInvalidJson:
    def test_error_on_non_json_content(self, tmp_path: Path) -> None:
        dest = tmp_path / manifest_path(_SNAP_ID)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"not { valid json !!!")
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert result.checked == 1
        assert any("not valid JSON" in i.message for i in result.issues)

    def test_error_when_root_is_a_list_not_object(self, tmp_path: Path) -> None:
        _write_raw(tmp_path, _SNAP_ID, [1, 2, 3])
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("not a JSON object" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Missing required keys
# ---------------------------------------------------------------------------


class TestMissingRequiredKeys:
    def _base(self) -> dict:
        return {
            "snapshot_id": _SNAP_ID,
            "created_at": "2026-04-14T00:00:00",
            "entries": [],
            "total_files": 0,
            "total_bytes": 0,
        }

    def test_missing_snapshot_id(self, tmp_path: Path) -> None:
        data = self._base()
        del data["snapshot_id"]
        _write_raw(tmp_path, _SNAP_ID, data)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("snapshot_id" in i.message for i in result.issues)

    def test_missing_created_at(self, tmp_path: Path) -> None:
        data = self._base()
        del data["created_at"]
        _write_raw(tmp_path, _SNAP_ID, data)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("created_at" in i.message for i in result.issues)

    def test_missing_entries(self, tmp_path: Path) -> None:
        data = self._base()
        del data["entries"]
        _write_raw(tmp_path, _SNAP_ID, data)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("entries" in i.message for i in result.issues)

    def test_missing_total_files(self, tmp_path: Path) -> None:
        data = self._base()
        del data["total_files"]
        _write_raw(tmp_path, _SNAP_ID, data)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("total_files" in i.message for i in result.issues)

    def test_missing_total_bytes(self, tmp_path: Path) -> None:
        data = self._base()
        del data["total_bytes"]
        _write_raw(tmp_path, _SNAP_ID, data)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("total_bytes" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Empty identifiers
# ---------------------------------------------------------------------------


class TestEmptyIdentifiers:
    def _base(self) -> dict:
        return {
            "snapshot_id": _SNAP_ID,
            "created_at": "2026-04-14T00:00:00",
            "entries": [],
            "total_files": 0,
            "total_bytes": 0,
        }

    def test_error_when_snapshot_id_empty_string(self, tmp_path: Path) -> None:
        data = {**self._base(), "snapshot_id": ""}
        _write_raw(tmp_path, _SNAP_ID, data)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("snapshot_id" in i.message for i in result.issues)

    def test_error_when_created_at_empty_string(self, tmp_path: Path) -> None:
        data = {**self._base(), "created_at": ""}
        _write_raw(tmp_path, _SNAP_ID, data)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("created_at" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Entries shape
# ---------------------------------------------------------------------------


class TestEntriesShape:
    def _base_with_entry(self, entry: dict) -> dict:
        return {
            "snapshot_id": _SNAP_ID,
            "created_at": "2026-04-14T00:00:00",
            "entries": [entry],
            "total_files": 1,
            "total_bytes": 10,
        }

    def test_error_when_entry_missing_sha256(self, tmp_path: Path) -> None:
        _write_raw(
            tmp_path,
            _SNAP_ID,
            self._base_with_entry({"path": "members/foo.json", "size_bytes": 10}),
        )
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("sha256" in i.message for i in result.issues)

    def test_error_when_entry_missing_path(self, tmp_path: Path) -> None:
        _write_raw(
            tmp_path,
            _SNAP_ID,
            self._base_with_entry({"sha256": "a" * 64, "size_bytes": 10}),
        )
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("path" in i.message for i in result.issues)

    def test_error_when_entry_missing_size_bytes(self, tmp_path: Path) -> None:
        _write_raw(
            tmp_path,
            _SNAP_ID,
            self._base_with_entry({"path": "members/foo.json", "sha256": "a" * 64}),
        )
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("size_bytes" in i.message for i in result.issues)

    def test_error_when_entry_is_not_an_object(self, tmp_path: Path) -> None:
        data = {
            "snapshot_id": _SNAP_ID,
            "created_at": "2026-04-14T00:00:00",
            "entries": ["not-an-object"],
            "total_files": 1,
            "total_bytes": 0,
        }
        _write_raw(tmp_path, _SNAP_ID, data)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("not an object" in i.message for i in result.issues)

    def test_error_when_entries_is_not_a_list(self, tmp_path: Path) -> None:
        data = {
            "snapshot_id": _SNAP_ID,
            "created_at": "2026-04-14T00:00:00",
            "entries": "not-a-list",
            "total_files": 0,
            "total_bytes": 0,
        }
        _write_raw(tmp_path, _SNAP_ID, data)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("entries" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Count mismatch
# ---------------------------------------------------------------------------


class TestCountMismatch:
    def _tamper(self, root: Path, **overrides: object) -> None:
        """Write a valid manifest then tamper with specified top-level fields."""
        entry = ManifestEntry(path=member_path("p"), sha256="c" * 64, size_bytes=5)
        manifest = _minimal_manifest(entries=[entry])
        raw = json.loads(serialize_payload(manifest))
        raw.update(overrides)
        dest = root / manifest_path(_SNAP_ID)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(json.dumps(raw).encode("utf-8"))

    def test_error_when_total_files_does_not_match_entry_count(self, tmp_path: Path) -> None:
        self._tamper(tmp_path, total_files=99)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("mismatch" in i.message for i in result.issues)

    def test_error_when_total_bytes_does_not_match_sum(self, tmp_path: Path) -> None:
        self._tamper(tmp_path, total_bytes=9999)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("mismatch" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Issue attributes
# ---------------------------------------------------------------------------


class TestIssueAttributes:
    def test_issues_have_correct_stage(self, tmp_path: Path) -> None:
        dest = tmp_path / manifest_path(_SNAP_ID)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"bad json {{{")
        result = verify_local_manifest(tmp_path)
        for issue in result.issues:
            assert issue.stage == "manifest"

    def test_errors_are_error_severity(self, tmp_path: Path) -> None:
        dest = tmp_path / manifest_path(_SNAP_ID)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"bad json {{{")
        result = verify_local_manifest(tmp_path)
        assert all(i.severity == "error" for i in result.issues)
