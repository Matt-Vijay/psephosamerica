"""Tests for src/export/local_store.py — local artifact reader."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from src.export.contracts import (
    ConfidenceLabel,
    EvidenceCardPayload,
    MemberProfilePayload,
    ZipFeedPayload,
)
from src.export.filesystem import write_planned_files
from src.export.local_store import (
    list_artifact_paths,
    load_evidence_card,
    load_manifest,
    load_member_profile,
    load_zip_feed,
)
from src.export.manifest import ManifestEntry, SnapshotManifest
from src.export.writer import (
    PlannedFile,
    evidence_path,
    manifest_path,
    member_path,
    zip_path,
)


# ── Fixture payloads ───────────────────────────────────────────────


SNAPSHOT_DATE = date(2026, 4, 13)
SNAPSHOT_ID = "2026-04-13"


def _member_payload() -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id="S000148",
        name="Charles Schumer",
        slug="charles-schumer",
        state="NY",
        district=None,
        chamber="senate",
        party="Democrat",
        scores=[],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=0,
        snapshot_date=SNAPSHOT_DATE,
    )


def _evidence_payload() -> EvidenceCardPayload:
    return EvidenceCardPayload(
        evidence_card_id="ec-001",
        member_bioguide_id="S000148",
        member_name="Charles Schumer",
        member_slug="charles-schumer",
        dimension="conflict_of_interest_risk",
        rule_id="committee_sector_trade",
        rule_version=1,
        score_delta=-5.0,
        short_explanation="Trade overlapping committee jurisdiction.",
        blocks=[],
        source_anchors=[],
        confidence=ConfidenceLabel.HIGH,
        snapshot_date=SNAPSHOT_DATE,
        created_at=datetime(2026, 4, 13, 12, 0, 0),
    )


def _zip_payload() -> ZipFeedPayload:
    return ZipFeedPayload(
        zip_code="10001",
        congressional_district="NY-12",
        ambiguity_note=None,
        members=[],
        snapshot_date=SNAPSHOT_DATE,
    )


def _manifest(files: list[PlannedFile]) -> SnapshotManifest:
    return SnapshotManifest(
        snapshot_id=SNAPSHOT_ID,
        created_at=datetime(2026, 4, 13, 0, 0, 0),
        entries=[
            ManifestEntry(path=f.path, sha256=f.sha256, size_bytes=f.size_bytes)
            for f in files
        ],
        total_files=len(files),
        total_bytes=sum(f.size_bytes for f in files),
    )


# ── Helpers ────────────────────────────────────────────────────────


def _write_json(root: Path, rel_path: str, data: object) -> None:
    dest = root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, default=str), encoding="utf-8")


# ── load_member_profile ────────────────────────────────────────────


def test_load_member_profile_roundtrip(tmp_path: Path) -> None:
    payload = _member_payload()
    pf = PlannedFile.from_bytes(member_path(payload.slug), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_member_profile(tmp_path, payload.slug)
    assert result.bioguide_id == "S000148"
    assert result.slug == "charles-schumer"
    assert result.chamber == "senate"
    assert result.snapshot_date == SNAPSHOT_DATE


def test_load_member_profile_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_member_profile(tmp_path, "nonexistent-member")


def test_load_member_profile_invalid_json(tmp_path: Path) -> None:
    dest = tmp_path / member_path("bad-member")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"not valid json {{{")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_member_profile(tmp_path, "bad-member")


def test_load_member_profile_schema_mismatch(tmp_path: Path) -> None:
    _write_json(tmp_path, member_path("bad-schema"), {"completely": "wrong"})
    with pytest.raises(Exception):
        load_member_profile(tmp_path, "bad-schema")


# ── load_evidence_card ─────────────────────────────────────────────


def test_load_evidence_card_roundtrip(tmp_path: Path) -> None:
    payload = _evidence_payload()
    pf = PlannedFile.from_bytes(evidence_path(payload.evidence_card_id), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_evidence_card(tmp_path, "ec-001")
    assert result.evidence_card_id == "ec-001"
    assert result.member_bioguide_id == "S000148"
    assert result.confidence == ConfidenceLabel.HIGH
    assert result.score_delta == -5.0


def test_load_evidence_card_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_evidence_card(tmp_path, "ec-999")


def test_load_evidence_card_invalid_json(tmp_path: Path) -> None:
    dest = tmp_path / evidence_path("ec-bad")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"[[[")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_evidence_card(tmp_path, "ec-bad")


# ── load_zip_feed ──────────────────────────────────────────────────


def test_load_zip_feed_roundtrip(tmp_path: Path) -> None:
    payload = _zip_payload()
    pf = PlannedFile.from_bytes(zip_path(payload.zip_code), _serialise(payload))
    write_planned_files([pf], tmp_path)

    result = load_zip_feed(tmp_path, "10001")
    assert result.zip_code == "10001"
    assert result.congressional_district == "NY-12"
    assert result.ambiguity_note is None
    assert result.members == []


def test_load_zip_feed_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_zip_feed(tmp_path, "99999")


def test_load_zip_feed_schema_mismatch(tmp_path: Path) -> None:
    _write_json(tmp_path, zip_path("00000"), {"bad": "data"})
    with pytest.raises(Exception):
        load_zip_feed(tmp_path, "00000")


# ── load_manifest ──────────────────────────────────────────────────


def test_load_manifest_roundtrip(tmp_path: Path) -> None:
    data_file = PlannedFile.from_bytes("members/test.json", b'{"key":"value"}')
    manifest = _manifest([data_file])
    pf = PlannedFile.from_bytes(manifest_path(SNAPSHOT_ID), _serialise(manifest))
    write_planned_files([pf], tmp_path)

    result = load_manifest(tmp_path, SNAPSHOT_ID)
    assert result.snapshot_id == SNAPSHOT_ID
    assert result.total_files == 1
    assert result.entries[0].path == "members/test.json"
    assert result.verify_counts() is True


def test_load_manifest_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path, "1970-01-01")


def test_load_manifest_invalid_json(tmp_path: Path) -> None:
    dest = tmp_path / manifest_path("2000-01-01")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"not json")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_manifest(tmp_path, "2000-01-01")


# ── list_artifact_paths ────────────────────────────────────────────


def test_list_artifact_paths_order(tmp_path: Path) -> None:
    files = [
        PlannedFile.from_bytes("members/alice.json", b"{}"),
        PlannedFile.from_bytes("zip/10001.json", b"{}"),
        PlannedFile.from_bytes("evidence/ec-1.json", b"{}"),
    ]
    manifest = _manifest(files)
    paths = list_artifact_paths(manifest)
    assert paths == ["members/alice.json", "zip/10001.json", "evidence/ec-1.json"]


def test_list_artifact_paths_empty() -> None:
    manifest = SnapshotManifest(
        snapshot_id=SNAPSHOT_ID,
        created_at=datetime(2026, 4, 13),
        entries=[],
        total_files=0,
        total_bytes=0,
    )
    assert list_artifact_paths(manifest) == []


# ── Integration: write then read back all artifact types ───────────


def test_full_roundtrip_via_write_and_read(tmp_path: Path) -> None:
    """Write member, evidence, zip, and manifest; read each back and validate."""
    member = _member_payload()
    evidence = _evidence_payload()
    feed = _zip_payload()

    files = [
        PlannedFile.from_bytes(member_path(member.slug), _serialise(member)),
        PlannedFile.from_bytes(evidence_path(evidence.evidence_card_id), _serialise(evidence)),
        PlannedFile.from_bytes(zip_path(feed.zip_code), _serialise(feed)),
    ]
    manifest = _manifest(files)
    files.append(PlannedFile.from_bytes(manifest_path(SNAPSHOT_ID), _serialise(manifest)))
    write_planned_files(files, tmp_path)

    assert load_member_profile(tmp_path, "charles-schumer").bioguide_id == "S000148"
    assert load_evidence_card(tmp_path, "ec-001").evidence_card_id == "ec-001"
    assert load_zip_feed(tmp_path, "10001").zip_code == "10001"
    loaded_manifest = load_manifest(tmp_path, SNAPSHOT_ID)
    assert loaded_manifest.verify_counts() is True
    paths = list_artifact_paths(loaded_manifest)
    assert len(paths) == 3  # manifest itself not in the entry list


# ── Serialisation helper ───────────────────────────────────────────


def _serialise(model: object) -> bytes:
    """Serialise a Pydantic model to JSON bytes (mirrors writer.serialize_payload)."""
    import json as _json
    from pydantic import BaseModel as _BM
    assert isinstance(model, _BM)
    return _json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode("utf-8")
