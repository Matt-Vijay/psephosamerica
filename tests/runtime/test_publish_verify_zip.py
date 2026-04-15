"""Tests for src/runtime/publish_verify_zip.py.

Uses real temp publish trees — no mocks, no binary fixtures.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from src.export.contracts import ZipFeedPayload
from src.export.filesystem import write_planned_files
from src.export.manifest import ManifestEntry, SnapshotManifest
from src.export.writer import PlannedFile, zip_path
from src.runtime.publish_verify_zip import verify_local_zip_feeds

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = date(2026, 4, 14)
_SNAPSHOT_ID = "2026-04-14"


def _feed(zip_code: str = "90210", members: list | None = None) -> ZipFeedPayload:
    return ZipFeedPayload(
        zip_code=zip_code,
        congressional_district=None,
        ambiguity_note=None,
        members=members if members is not None else [],
        snapshot_date=_SNAPSHOT_DATE,
    )


def _serialise(payload: ZipFeedPayload) -> bytes:
    return json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")


def _planned(feed: ZipFeedPayload) -> PlannedFile:
    return PlannedFile.from_bytes(zip_path(feed.zip_code), _serialise(feed))


def _manifest(files: list[PlannedFile]) -> SnapshotManifest:
    return SnapshotManifest(
        snapshot_id=_SNAPSHOT_ID,
        created_at=datetime(2026, 4, 14, 0, 0, 0),
        entries=[
            ManifestEntry(path=f.path, sha256=f.sha256, size_bytes=f.size_bytes)
            for f in files
        ],
        total_files=len(files),
        total_bytes=sum(f.size_bytes for f in files),
    )


def _write_feeds(root: Path, feeds: list[ZipFeedPayload]) -> SnapshotManifest:
    """Write feed files to *root* and return a matching manifest."""
    planned = [_planned(f) for f in feeds]
    write_planned_files(planned, root)
    return _manifest(planned)


def _empty_manifest() -> SnapshotManifest:
    return SnapshotManifest(
        snapshot_id=_SNAPSHOT_ID,
        created_at=datetime(2026, 4, 14, 0, 0, 0),
        entries=[],
        total_files=0,
        total_bytes=0,
    )


def _manifest_with_non_zip_entries() -> SnapshotManifest:
    """Manifest with only member/evidence entries — no zip entries."""
    entry = ManifestEntry(
        path="members/alice-smith.json",
        sha256="a" * 64,
        size_bytes=100,
    )
    return SnapshotManifest(
        snapshot_id=_SNAPSHOT_ID,
        created_at=datetime(2026, 4, 14, 0, 0, 0),
        entries=[entry],
        total_files=1,
        total_bytes=100,
    )


# ---------------------------------------------------------------------------
# No zip entries — optional, should be ok
# ---------------------------------------------------------------------------


class TestNoZipEntries:
    def test_empty_manifest_returns_ok(self, tmp_path: Path) -> None:
        result = verify_local_zip_feeds(tmp_path, _empty_manifest())
        assert result.ok is True

    def test_empty_manifest_checked_is_zero(self, tmp_path: Path) -> None:
        result = verify_local_zip_feeds(tmp_path, _empty_manifest())
        assert result.checked == 0

    def test_empty_manifest_no_issues(self, tmp_path: Path) -> None:
        result = verify_local_zip_feeds(tmp_path, _empty_manifest())
        assert result.issues == ()

    def test_non_zip_entries_only_returns_ok(self, tmp_path: Path) -> None:
        result = verify_local_zip_feeds(tmp_path, _manifest_with_non_zip_entries())
        assert result.ok is True
        assert result.checked == 0

    def test_stage_name_is_zip(self, tmp_path: Path) -> None:
        result = verify_local_zip_feeds(tmp_path, _empty_manifest())
        assert result.stage == "zip"


# ---------------------------------------------------------------------------
# Single valid zip feed
# ---------------------------------------------------------------------------


class TestSingleValidFeed:
    def test_ok(self, tmp_path: Path) -> None:
        manifest = _write_feeds(tmp_path, [_feed("90210")])
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.ok is True

    def test_checked_count(self, tmp_path: Path) -> None:
        manifest = _write_feeds(tmp_path, [_feed("90210")])
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.checked == 1

    def test_no_issues(self, tmp_path: Path) -> None:
        manifest = _write_feeds(tmp_path, [_feed("90210")])
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.issues == ()


# ---------------------------------------------------------------------------
# Multiple valid zip feeds
# ---------------------------------------------------------------------------


class TestMultipleValidFeeds:
    def test_ok(self, tmp_path: Path) -> None:
        manifest = _write_feeds(tmp_path, [_feed("10001"), _feed("94102"), _feed("73301")])
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.ok is True

    def test_checked_count(self, tmp_path: Path) -> None:
        manifest = _write_feeds(tmp_path, [_feed("10001"), _feed("94102"), _feed("73301")])
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.checked == 3

    def test_no_issues(self, tmp_path: Path) -> None:
        manifest = _write_feeds(tmp_path, [_feed("10001"), _feed("94102"), _feed("73301")])
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.issues == ()


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


class TestMissingFile:
    def _manifest_for_missing(self) -> SnapshotManifest:
        """Manifest that claims a zip feed exists but file is not written."""
        entry = ManifestEntry(
            path="zip/99999.json",
            sha256="b" * 64,
            size_bytes=50,
        )
        return SnapshotManifest(
            snapshot_id=_SNAPSHOT_ID,
            created_at=datetime(2026, 4, 14, 0, 0, 0),
            entries=[entry],
            total_files=1,
            total_bytes=50,
        )

    def test_not_ok(self, tmp_path: Path) -> None:
        manifest = self._manifest_for_missing()
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.ok is False

    def test_error_issue(self, tmp_path: Path) -> None:
        manifest = self._manifest_for_missing()
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.error_count == 1
        issue = result.issues[0]
        assert issue.severity == "error"
        assert "99999" in issue.message
        assert issue.path == "zip/99999.json"

    def test_checked_still_counts_attempted(self, tmp_path: Path) -> None:
        manifest = self._manifest_for_missing()
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.checked == 1


# ---------------------------------------------------------------------------
# Invalid JSON content
# ---------------------------------------------------------------------------


class TestInvalidJson:
    def _write_bad_json(self, root: Path, zip_code: str) -> SnapshotManifest:
        bad_path = root / zip_path(zip_code)
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_bytes = b"not valid json {"
        bad_path.write_bytes(bad_bytes)
        import hashlib
        sha = hashlib.sha256(bad_bytes).hexdigest()
        entry = ManifestEntry(path=zip_path(zip_code), sha256=sha, size_bytes=len(bad_bytes))
        return SnapshotManifest(
            snapshot_id=_SNAPSHOT_ID,
            created_at=datetime(2026, 4, 14, 0, 0, 0),
            entries=[entry],
            total_files=1,
            total_bytes=len(bad_bytes),
        )

    def test_not_ok(self, tmp_path: Path) -> None:
        manifest = self._write_bad_json(tmp_path, "12345")
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.ok is False

    def test_error_issue_references_path(self, tmp_path: Path) -> None:
        manifest = self._write_bad_json(tmp_path, "12345")
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.error_count == 1
        issue = result.issues[0]
        assert issue.severity == "error"
        assert "12345" in issue.path or "12345" in issue.message


# ---------------------------------------------------------------------------
# Partial failure — one good, one missing
# ---------------------------------------------------------------------------


class TestPartialFailure:
    def test_one_error_one_ok(self, tmp_path: Path) -> None:
        good = _feed("10001")
        good_planned = _planned(good)
        write_planned_files([good_planned], tmp_path)

        missing_entry = ManifestEntry(
            path="zip/99998.json",
            sha256="c" * 64,
            size_bytes=50,
        )
        manifest = SnapshotManifest(
            snapshot_id=_SNAPSHOT_ID,
            created_at=datetime(2026, 4, 14, 0, 0, 0),
            entries=[
                ManifestEntry(
                    path=good_planned.path,
                    sha256=good_planned.sha256,
                    size_bytes=good_planned.size_bytes,
                ),
                missing_entry,
            ],
            total_files=2,
            total_bytes=good_planned.size_bytes + 50,
        )

        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.ok is False
        assert result.checked == 2
        assert result.error_count == 1
        assert result.warning_count == 0

    def test_error_path_identifies_missing_file(self, tmp_path: Path) -> None:
        good = _feed("10001")
        good_planned = _planned(good)
        write_planned_files([good_planned], tmp_path)

        missing_entry = ManifestEntry(
            path="zip/99998.json",
            sha256="c" * 64,
            size_bytes=50,
        )
        manifest = SnapshotManifest(
            snapshot_id=_SNAPSHOT_ID,
            created_at=datetime(2026, 4, 14, 0, 0, 0),
            entries=[
                ManifestEntry(
                    path=good_planned.path,
                    sha256=good_planned.sha256,
                    size_bytes=good_planned.size_bytes,
                ),
                missing_entry,
            ],
            total_files=2,
            total_bytes=good_planned.size_bytes + 50,
        )

        result = verify_local_zip_feeds(tmp_path, manifest)
        error_paths = [i.path for i in result.issues if i.severity == "error"]
        assert "zip/99998.json" in error_paths


# ---------------------------------------------------------------------------
# Path confinement
# ---------------------------------------------------------------------------


class TestPathConfinement:
    def test_dotdot_path_is_error(self, tmp_path: Path) -> None:
        entry = ManifestEntry(
            path="zip/../../etc/passwd.json",
            sha256="a" * 64,
            size_bytes=50,
        )
        manifest = SnapshotManifest(
            snapshot_id=_SNAPSHOT_ID,
            created_at=datetime(2026, 4, 14, 0, 0, 0),
            entries=[entry],
            total_files=1,
            total_bytes=50,
        )
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.ok is False
        assert any("escapes" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# zip_code mismatch
# ---------------------------------------------------------------------------


class TestZipCodeMismatch:
    def _write_mismatched(self, root: Path) -> SnapshotManifest:
        """Write a feed with zip_code=90210 but store it at zip/11111.json."""
        feed = _feed("90210")
        content = _serialise(feed)
        wrong_path = root / "zip" / "11111.json"
        wrong_path.parent.mkdir(parents=True, exist_ok=True)
        wrong_path.write_bytes(content)
        import hashlib
        sha = hashlib.sha256(content).hexdigest()
        entry = ManifestEntry(path="zip/11111.json", sha256=sha, size_bytes=len(content))
        return SnapshotManifest(
            snapshot_id=_SNAPSHOT_ID,
            created_at=datetime(2026, 4, 14, 0, 0, 0),
            entries=[entry],
            total_files=1,
            total_bytes=len(content),
        )

    def test_not_ok(self, tmp_path: Path) -> None:
        manifest = self._write_mismatched(tmp_path)
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.ok is False

    def test_error_mentions_both_codes(self, tmp_path: Path) -> None:
        manifest = self._write_mismatched(tmp_path)
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.error_count == 1
        issue = result.issues[0]
        assert "11111" in issue.message
        assert "90210" in issue.message


# ---------------------------------------------------------------------------
# Stage result properties
# ---------------------------------------------------------------------------


class TestStageResultProperties:
    def test_ok_true_when_no_errors(self, tmp_path: Path) -> None:
        manifest = _write_feeds(tmp_path, [_feed("10001")])
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.ok is True
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_ok_false_when_errors(self, tmp_path: Path) -> None:
        entry = ManifestEntry(path="zip/55555.json", sha256="d" * 64, size_bytes=10)
        manifest = SnapshotManifest(
            snapshot_id=_SNAPSHOT_ID,
            created_at=datetime(2026, 4, 14, 0, 0, 0),
            entries=[entry],
            total_files=1,
            total_bytes=10,
        )
        result = verify_local_zip_feeds(tmp_path, manifest)
        assert result.ok is False
