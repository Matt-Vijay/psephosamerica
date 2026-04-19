"""Tests for src/runtime/publish_verify_manifest.py.

Uses real temp publish trees — no I/O mocking.
No network calls, no DB.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.export.filesystem import write_planned_files
from src.export.manifest import ManifestEntry, SnapshotManifest, manifest_root_sha256
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
        root_sha256=manifest_root_sha256(entries),
    )


def _write_manifest(root: Path, manifest: SnapshotManifest) -> None:
    """Write a valid serialized manifest into the publish tree."""
    planned = PlannedFile.from_bytes(
        manifest_path(manifest.snapshot_id),
        serialize_payload(manifest),
    )
    write_planned_files([planned], root)


def _write_planned(root: Path, planned: PlannedFile) -> None:
    write_planned_files([planned], root)


def _member_file(slug: str, content: bytes) -> PlannedFile:
    return PlannedFile.from_bytes(member_path(slug), content)


def _root_file(path: str, content: bytes) -> PlannedFile:
    return PlannedFile.from_bytes(path, content)


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
        planned = _member_file("test-member", b'{"member":"ok"}')
        _write_planned(tmp_path, planned)
        entry = ManifestEntry(path=planned.path, sha256=planned.sha256, size_bytes=planned.size_bytes)
        _write_manifest(tmp_path, _minimal_manifest(entries=[entry]))
        result = verify_local_manifest(tmp_path)
        assert result.ok is True
        assert result.checked == 1

    def test_error_for_multiple_valid_snapshots(self, tmp_path: Path) -> None:
        for sid in ("2026-04-01", "2026-04-14"):
            _write_manifest(tmp_path, _minimal_manifest(snapshot_id=sid))
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert result.checked == 2
        assert any("expected exactly one manifest" in issue.message for issue in result.issues)


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
            "root_sha256": manifest_root_sha256([]),
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

    def test_missing_root_sha256(self, tmp_path: Path) -> None:
        data = self._base()
        del data["root_sha256"]
        _write_raw(tmp_path, _SNAP_ID, data)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("root_sha256" in i.message for i in result.issues)


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
            "root_sha256": manifest_root_sha256([]),
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
            "root_sha256": "0" * 64,
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
            "root_sha256": "0" * 64,
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
            "root_sha256": "0" * 64,
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

    def test_error_when_total_bytes_does_not_match_sum(self, tmp_path: Path) -> None:
        self._tamper(tmp_path, total_bytes=9999)
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("mismatch" in i.message for i in result.issues)


class TestManifestEntryIntegrity:
    def test_error_when_manifest_entry_file_missing(self, tmp_path: Path) -> None:
        planned = _member_file("ghost-member", b'{"member":"ghost"}')
        entry = ManifestEntry(path=planned.path, sha256=planned.sha256, size_bytes=planned.size_bytes)
        _write_manifest(tmp_path, _minimal_manifest(entries=[entry]))

        result = verify_local_manifest(tmp_path)

        assert result.ok is False
        assert any("missing" in i.message for i in result.issues)

    def test_error_when_manifest_entry_hash_mismatch(self, tmp_path: Path) -> None:
        planned = _member_file("tampered-member", b'{"member":"before"}')
        _write_planned(tmp_path, planned)
        _write_manifest(
            tmp_path,
            _minimal_manifest(
                entries=[
                    ManifestEntry(
                        path=planned.path,
                        sha256=planned.sha256,
                        size_bytes=planned.size_bytes,
                    )
                ]
            ),
        )
        (tmp_path / planned.path).write_bytes(b'{"member":"after"}')

        result = verify_local_manifest(tmp_path)

        assert result.ok is False
        assert any("sha256 mismatch" in i.message for i in result.issues)

    def test_error_when_manifest_entry_size_mismatch(self, tmp_path: Path) -> None:
        planned = _member_file("size-mismatch", b'{"member":"size"}')
        _write_planned(tmp_path, planned)
        _write_manifest(
            tmp_path,
            _minimal_manifest(
                entries=[
                    ManifestEntry(
                        path=planned.path,
                        sha256=planned.sha256,
                        size_bytes=planned.size_bytes + 1,
                    )
                ]
            ),
        )

        result = verify_local_manifest(tmp_path)

        assert result.ok is False
        assert any("size mismatch" in i.message for i in result.issues)

    def test_error_when_manifest_contains_duplicate_entry_paths(self, tmp_path: Path) -> None:
        planned = _member_file("duplicate-member", b'{"member":"dup"}')
        _write_planned(tmp_path, planned)
        entry = ManifestEntry(path=planned.path, sha256=planned.sha256, size_bytes=planned.size_bytes)
        _write_manifest(tmp_path, _minimal_manifest(entries=[entry, entry]))

        result = verify_local_manifest(tmp_path)

        assert result.ok is False
        assert any("duplicate" in i.message for i in result.issues)

    def test_error_when_managed_file_missing_from_manifest(self, tmp_path: Path) -> None:
        manifest_member = _member_file("listed-member", b'{"member":"listed"}')
        extra_member = _member_file("extra-member", b'{"member":"extra"}')
        _write_planned(tmp_path, manifest_member)
        _write_planned(tmp_path, extra_member)
        _write_manifest(
            tmp_path,
            _minimal_manifest(
                entries=[
                    ManifestEntry(
                        path=manifest_member.path,
                        sha256=manifest_member.sha256,
                        size_bytes=manifest_member.size_bytes,
                    )
                ]
            ),
        )

        result = verify_local_manifest(tmp_path)

        assert result.ok is False
        assert any("not listed in manifest" in i.message for i in result.issues)

    def test_warning_when_root_public_file_is_outside_local_verification_scope(
        self, tmp_path: Path
    ) -> None:
        manifest_member = _member_file("listed-member", b'{"member":"listed"}')
        robots = _root_file("robots.txt", b"User-agent: *\nAllow: /\n")
        _write_planned(tmp_path, manifest_member)
        _write_planned(tmp_path, robots)
        _write_manifest(
            tmp_path,
            _minimal_manifest(
                entries=[
                    ManifestEntry(
                        path=manifest_member.path,
                        sha256=manifest_member.sha256,
                        size_bytes=manifest_member.size_bytes,
                    )
                ]
            ),
        )

        result = verify_local_manifest(tmp_path)

        assert result.ok is True
        assert any(i.path == "robots.txt" and i.severity == "warning" for i in result.issues)
        assert any("outside local verification coverage" in i.message for i in result.issues)

    def test_warning_when_homepage_file_is_outside_local_verification_scope(
        self, tmp_path: Path
    ) -> None:
        manifest_member = _member_file("listed-member", b'{"member":"listed"}')
        homepage = _root_file("homepage/feed.json", b'{"items":[]}')
        _write_planned(tmp_path, manifest_member)
        _write_planned(tmp_path, homepage)
        _write_manifest(
            tmp_path,
            _minimal_manifest(
                entries=[
                    ManifestEntry(
                        path=manifest_member.path,
                        sha256=manifest_member.sha256,
                        size_bytes=manifest_member.size_bytes,
                    )
                ]
            ),
        )

        result = verify_local_manifest(tmp_path)

        assert result.ok is True
        assert any(i.path == "homepage/feed.json" and i.severity == "warning" for i in result.issues)
        assert not any("root_sha256 mismatch" in i.message for i in result.issues)

    def test_error_when_root_sha256_does_not_match_entries(self, tmp_path: Path) -> None:
        planned = _member_file("tampered-root", b'{"member":"ok"}')
        _write_planned(tmp_path, planned)
        manifest = _minimal_manifest(
            entries=[
                ManifestEntry(
                    path=planned.path,
                    sha256=planned.sha256,
                    size_bytes=planned.size_bytes,
                )
            ]
        )
        raw = json.loads(serialize_payload(manifest))
        raw["root_sha256"] = "f" * 64
        _write_raw(tmp_path, manifest.snapshot_id, raw)

        result = verify_local_manifest(tmp_path)

        assert result.ok is False
        assert any("root_sha256 mismatch" in i.message for i in result.issues)

# ---------------------------------------------------------------------------
# Issue attributes
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Path confinement
# ---------------------------------------------------------------------------


class TestPathConfinement:
    def _base_with_entry(self, entry: dict) -> dict:
        return {
            "snapshot_id": _SNAP_ID,
            "created_at": "2026-04-14T00:00:00",
            "entries": [entry],
            "total_files": 1,
            "total_bytes": 10,
            "root_sha256": "0" * 64,
        }

    def test_error_when_entry_path_escapes_root(self, tmp_path: Path) -> None:
        _write_raw(
            tmp_path,
            _SNAP_ID,
            self._base_with_entry(
                {"path": "../../etc/passwd", "sha256": "a" * 64, "size_bytes": 10}
            ),
        )
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("escapes" in i.message for i in result.issues)

    def test_error_when_entry_path_is_absolute(self, tmp_path: Path) -> None:
        _write_raw(
            tmp_path,
            _SNAP_ID,
            self._base_with_entry(
                {"path": "/etc/passwd", "sha256": "a" * 64, "size_bytes": 10}
            ),
        )
        result = verify_local_manifest(tmp_path)
        assert result.ok is False
        assert any("escapes" in i.message for i in result.issues)


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
