from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.export.filesystem import (
    read_manifest,
    verify_written_files,
    write_planned_files,
)
from src.export.writer import PlannedFile


# ── Helpers ────────────────────────────────────────────────────────────────────


def _pf(path: str, content: bytes) -> PlannedFile:
    return PlannedFile.from_bytes(path, content)


# ── write_planned_files ────────────────────────────────────────────────────────


def test_write_creates_file(tmp_path: Path) -> None:
    files = [_pf("members/alice.json", b'{"name":"alice"}')]
    write_planned_files(files, tmp_path)
    assert (tmp_path / "members" / "alice.json").is_file()


def test_write_content_matches(tmp_path: Path) -> None:
    content = b'{"key":"value"}'
    write_planned_files([_pf("out/data.json", content)], tmp_path)
    assert (tmp_path / "out" / "data.json").read_bytes() == content


def test_write_creates_nested_parents(tmp_path: Path) -> None:
    files = [_pf("a/b/c/deep.json", b"{}")]
    write_planned_files(files, tmp_path)
    assert (tmp_path / "a" / "b" / "c" / "deep.json").exists()


def test_write_multiple_files(tmp_path: Path) -> None:
    files = [
        _pf("members/alice.json", b'{"name":"alice"}'),
        _pf("zip/10001.json", b'{"zip":"10001"}'),
        _pf("evidence/ec-1.json", b'{"id":"ec-1"}'),
    ]
    write_planned_files(files, tmp_path)
    for f in files:
        assert (tmp_path / f.path).is_file()


def test_write_empty_list_is_noop(tmp_path: Path) -> None:
    write_planned_files([], tmp_path)
    # tmp_path itself exists but nothing was written inside
    assert list(tmp_path.iterdir()) == []


def test_write_overwrites_existing_file(tmp_path: Path) -> None:
    dest = tmp_path / "x.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"old")
    write_planned_files([_pf("x.json", b"new")], tmp_path)
    assert dest.read_bytes() == b"new"


def test_write_empty_content(tmp_path: Path) -> None:
    write_planned_files([_pf("empty.json", b"")], tmp_path)
    assert (tmp_path / "empty.json").read_bytes() == b""


# ── read_manifest ──────────────────────────────────────────────────────────────


def _write_manifest(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _minimal_manifest_dict(snapshot_id: str = "2026-04-13") -> dict:
    return {
        "snapshot_id": snapshot_id,
        "created_at": "2026-04-13T12:00:00",
        "entries": [
            {"path": "members/alice.json", "sha256": "a" * 64, "size_bytes": 128},
        ],
        "total_files": 1,
        "total_bytes": 128,
    }


def test_read_manifest_returns_snapshot_manifest(tmp_path: Path) -> None:
    from src.export.manifest import SnapshotManifest

    mfile = _write_manifest(tmp_path / "manifest.json", _minimal_manifest_dict())
    result = read_manifest(mfile)
    assert isinstance(result, SnapshotManifest)


def test_read_manifest_snapshot_id(tmp_path: Path) -> None:
    mfile = _write_manifest(tmp_path / "manifest.json", _minimal_manifest_dict("2025-12-31"))
    result = read_manifest(mfile)
    assert result.snapshot_id == "2025-12-31"


def test_read_manifest_entry_count(tmp_path: Path) -> None:
    data = _minimal_manifest_dict()
    data["entries"].append({"path": "zip/10001.json", "sha256": "b" * 64, "size_bytes": 64})
    data["total_files"] = 2
    data["total_bytes"] = 192
    mfile = _write_manifest(tmp_path / "manifest.json", data)
    result = read_manifest(mfile)
    assert len(result.entries) == 2


def test_read_manifest_verify_counts(tmp_path: Path) -> None:
    mfile = _write_manifest(tmp_path / "manifest.json", _minimal_manifest_dict())
    result = read_manifest(mfile)
    assert result.verify_counts() is True


def test_read_manifest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_manifest(tmp_path / "nonexistent.json")


def test_read_manifest_invalid_json_raises(tmp_path: Path) -> None:
    mfile = tmp_path / "bad.json"
    mfile.write_text("not json", encoding="utf-8")
    with pytest.raises(Exception):
        read_manifest(mfile)


# ── verify_written_files ───────────────────────────────────────────────────────


def test_verify_all_match(tmp_path: Path) -> None:
    files = [
        _pf("members/alice.json", b'{"name":"alice"}'),
        _pf("zip/10001.json", b'{"zip":"10001"}'),
    ]
    write_planned_files(files, tmp_path)
    failures = verify_written_files(files, tmp_path)
    assert failures == []


def test_verify_empty_files_list(tmp_path: Path) -> None:
    assert verify_written_files([], tmp_path) == []


def test_verify_missing_file_reported(tmp_path: Path) -> None:
    files = [_pf("missing/data.json", b"{}")]
    # Do not write — file is absent
    failures = verify_written_files(files, tmp_path)
    assert "missing/data.json" in failures


def test_verify_corrupted_file_reported(tmp_path: Path) -> None:
    files = [_pf("data.json", b'{"original":true}')]
    write_planned_files(files, tmp_path)
    # Corrupt the on-disk content
    (tmp_path / "data.json").write_bytes(b"tampered")
    failures = verify_written_files(files, tmp_path)
    assert "data.json" in failures


def test_verify_returns_only_failed_paths(tmp_path: Path) -> None:
    good = _pf("good.json", b'{"ok":true}')
    bad = _pf("bad.json", b'{"ok":true}')
    write_planned_files([good, bad], tmp_path)
    (tmp_path / "bad.json").write_bytes(b"corrupted")
    failures = verify_written_files([good, bad], tmp_path)
    assert failures == ["bad.json"]
    assert "good.json" not in failures


def test_verify_empty_content_file(tmp_path: Path) -> None:
    files = [_pf("empty.json", b"")]
    write_planned_files(files, tmp_path)
    assert verify_written_files(files, tmp_path) == []


def test_verify_roundtrip_with_plan_snapshot(tmp_path: Path) -> None:
    """write + verify round-trip using plan_snapshot output."""
    from datetime import date

    from src.export.contracts import MemberProfilePayload, ScoreSummary
    from src.export.writer import plan_snapshot

    profile = MemberProfilePayload(
        bioguide_id="S000148",
        name="Charles Schumer",
        slug="charles-schumer",
        state="NY",
        chamber="senate",
        party="Democrat",
        scores=[ScoreSummary(dimension="conflict_of_interest_risk", current_score=50.0, rule_fire_count=1)],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=1,
        snapshot_date=date(2026, 4, 13),
    )
    files = plan_snapshot("2026-04-13", [profile], [], [])
    write_planned_files(files, tmp_path)
    failures = verify_written_files(files, tmp_path)
    assert failures == []
