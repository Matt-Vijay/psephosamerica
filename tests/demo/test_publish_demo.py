"""End-to-end tests for the local publish demo.

Validates that the full generate-and-publish stack works against synthetic
data: payloads are built, files are planned, written to a temp directory, and
every on-disk file passes hash verification.

No network calls, no database, no external I/O beyond the temp dir.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.demo.publish_demo import PublishDemoResult, main, run_publish_demo
from src.export.filesystem import read_manifest, verify_written_files
from src.export.writer import PlannedFile


# ---------------------------------------------------------------------------
# Module-scoped fixture: run once, share across all test classes
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tmp_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("publish_demo")


@pytest.fixture(scope="module")
def result(tmp_root: Path) -> PublishDemoResult:
    """Run the publish demo once and share it across all tests in this module."""
    return run_publish_demo(tmp_root)


# ---------------------------------------------------------------------------
# PublishDemoResult structure
# ---------------------------------------------------------------------------


class TestPublishDemoResult:
    def test_returns_publish_demo_result(self, result: PublishDemoResult) -> None:
        assert isinstance(result, PublishDemoResult)

    def test_success_flag(self, result: PublishDemoResult) -> None:
        assert result.success, f"Hash failures: {result.failures}"

    def test_no_failures(self, result: PublishDemoResult) -> None:
        assert result.failures == []

    def test_target_dir_exists(self, result: PublishDemoResult) -> None:
        assert result.target_dir.is_dir()

    def test_planned_count_positive(self, result: PublishDemoResult) -> None:
        assert result.planned_count >= 4  # member + zip + evidence + manifest

    def test_planned_is_list_of_planned_files(self, result: PublishDemoResult) -> None:
        assert all(isinstance(f, PlannedFile) for f in result.planned)


# ---------------------------------------------------------------------------
# Planned file integrity
# ---------------------------------------------------------------------------


class TestPlannedFiles:
    def test_all_paths_nonempty(self, result: PublishDemoResult) -> None:
        for f in result.planned:
            assert f.path, f"Empty path in planned file: {f!r}"

    def test_all_content_nonempty(self, result: PublishDemoResult) -> None:
        for f in result.planned:
            assert f.size_bytes > 0, f"Zero-byte planned file: {f.path}"

    def test_sha256_length(self, result: PublishDemoResult) -> None:
        for f in result.planned:
            assert len(f.sha256) == 64, f"Bad sha256 for {f.path}"

    def test_size_bytes_matches_content(self, result: PublishDemoResult) -> None:
        for f in result.planned:
            assert f.size_bytes == len(f.content), f"Size mismatch for {f.path}"

    def test_paths_unique(self, result: PublishDemoResult) -> None:
        paths = [f.path for f in result.planned]
        assert len(paths) == len(set(paths)), "Duplicate paths in planned files"

    def test_member_profile_planned(self, result: PublishDemoResult) -> None:
        paths = {f.path for f in result.planned}
        assert any("members/" in p and p.endswith(".json") for p in paths)

    def test_zip_feed_planned(self, result: PublishDemoResult) -> None:
        paths = {f.path for f in result.planned}
        assert any("zip/" in p and p.endswith(".json") for p in paths)

    def test_evidence_card_planned(self, result: PublishDemoResult) -> None:
        paths = {f.path for f in result.planned}
        assert any("evidence/" in p and p.endswith(".json") for p in paths)

    def test_manifest_planned_last(self, result: PublishDemoResult) -> None:
        last = result.planned[-1]
        assert "manifest.json" in last.path

    def test_all_content_is_valid_json(self, result: PublishDemoResult) -> None:
        for f in result.planned:
            try:
                json.loads(f.content)
            except json.JSONDecodeError as exc:
                pytest.fail(f"Invalid JSON in {f.path}: {exc}")


# ---------------------------------------------------------------------------
# On-disk writes
# ---------------------------------------------------------------------------


class TestWrittenFiles:
    def test_all_files_exist_on_disk(self, result: PublishDemoResult) -> None:
        for f in result.planned:
            dest = result.target_dir / f.path
            assert dest.exists(), f"Missing on disk: {f.path}"

    def test_all_files_are_regular_files(self, result: PublishDemoResult) -> None:
        for f in result.planned:
            dest = result.target_dir / f.path
            assert dest.is_file(), f"Not a regular file: {f.path}"

    def test_on_disk_sizes_match_planned(self, result: PublishDemoResult) -> None:
        for f in result.planned:
            dest = result.target_dir / f.path
            assert dest.stat().st_size == f.size_bytes, f"Size mismatch on disk: {f.path}"

    def test_on_disk_content_matches_planned(self, result: PublishDemoResult) -> None:
        for f in result.planned:
            dest = result.target_dir / f.path
            assert dest.read_bytes() == f.content, f"Content mismatch on disk: {f.path}"

    def test_verify_written_files_returns_empty(self, result: PublishDemoResult) -> None:
        failures = verify_written_files(result.planned, result.target_dir)
        assert failures == []


# ---------------------------------------------------------------------------
# Manifest on disk
# ---------------------------------------------------------------------------


class TestManifestOnDisk:
    def test_manifest_file_exists(self, result: PublishDemoResult) -> None:
        manifest_file = result.target_dir / "snapshots" / "2026-04-13" / "manifest.json"
        assert manifest_file.exists()

    def test_manifest_parses_correctly(self, result: PublishDemoResult) -> None:
        manifest_file = result.target_dir / "snapshots" / "2026-04-13" / "manifest.json"
        manifest = read_manifest(manifest_file)
        assert manifest.snapshot_id == "2026-04-13"

    def test_manifest_counts_consistent(self, result: PublishDemoResult) -> None:
        manifest_file = result.target_dir / "snapshots" / "2026-04-13" / "manifest.json"
        manifest = read_manifest(manifest_file)
        assert manifest.verify_counts()

    def test_manifest_entries_have_valid_sha256(self, result: PublishDemoResult) -> None:
        manifest_file = result.target_dir / "snapshots" / "2026-04-13" / "manifest.json"
        manifest = read_manifest(manifest_file)
        for entry in manifest.entries:
            assert len(entry.sha256) == 64, f"Bad sha256 for {entry.path}"

    def test_manifest_total_bytes_positive(self, result: PublishDemoResult) -> None:
        manifest_file = result.target_dir / "snapshots" / "2026-04-13" / "manifest.json"
        manifest = read_manifest(manifest_file)
        assert manifest.total_bytes > 0

    def test_manifest_covers_data_files(self, result: PublishDemoResult) -> None:
        # Manifest entries should cover all data files (all planned files except manifest itself)
        manifest_file = result.target_dir / "snapshots" / "2026-04-13" / "manifest.json"
        manifest = read_manifest(manifest_file)
        entry_paths = {e.path for e in manifest.entries}
        data_files = [f for f in result.planned if "manifest.json" not in f.path]
        for f in data_files:
            assert f.path in entry_paths, f"Data file missing from manifest: {f.path}"


# ---------------------------------------------------------------------------
# Idempotency: second write over same dir must still verify cleanly
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_write_verifies_cleanly(self, result: PublishDemoResult) -> None:
        from src.export.filesystem import write_planned_files

        write_planned_files(result.planned, result.target_dir)
        failures = verify_written_files(result.planned, result.target_dir)
        assert failures == [], f"Second-write verification failed: {failures}"


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_runs_without_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        main()
        captured = capsys.readouterr()
        assert captured.out.strip()

    def test_main_outputs_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        main()
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, dict)

    def test_main_status_ok(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        main()
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "ok"

    def test_main_no_failures(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        main()
        data = json.loads(capsys.readouterr().out)
        assert data["failures"] == []

    def test_main_planned_files_positive(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        main()
        data = json.loads(capsys.readouterr().out)
        assert data["planned_files"] >= 4

    def test_main_includes_bioguide_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        main()
        data = json.loads(capsys.readouterr().out)
        assert data["bioguide_id"] == "S000999"
