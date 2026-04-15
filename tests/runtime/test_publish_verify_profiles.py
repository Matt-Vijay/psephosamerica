"""Tests for src/runtime/publish_verify_profiles.py."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from src.export.contracts import MemberProfilePayload
from src.export.filesystem import write_planned_files
from src.export.manifest import ManifestEntry, SnapshotManifest
from src.export.writer import PlannedFile, member_path, serialize_payload
from src.runtime.publish_verify_profiles import verify_local_member_profiles
from src.runtime.publish_verify_types import PublishVerifyStageResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SNAPSHOT_DATE = date(2026, 4, 14)


def _profile(
    *,
    bioguide_id: str = "P000197",
    name: str = "Nancy Pelosi",
    slug: str = "nancy-pelosi",
    state: str = "CA",
    chamber: str = "house",
    party: str = "Democrat",
) -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id=bioguide_id,
        name=name,
        slug=slug,
        state=state,
        chamber=chamber,  # type: ignore[arg-type]
        party=party,
        scores=[],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=0,
        snapshot_date=SNAPSHOT_DATE,
    )


def _manifest_from_files(files: list[PlannedFile], *, snapshot_id: str = "2026-04-14") -> SnapshotManifest:
    return SnapshotManifest(
        snapshot_id=snapshot_id,
        created_at=datetime(2026, 4, 14, 0, 0, 0),
        entries=[ManifestEntry(path=f.path, sha256=f.sha256, size_bytes=f.size_bytes) for f in files],
        total_files=len(files),
        total_bytes=sum(f.size_bytes for f in files),
    )


def _write_profile(root: Path, profile: MemberProfilePayload) -> PlannedFile:
    planned = PlannedFile.from_bytes(member_path(profile.slug), serialize_payload(profile))
    write_planned_files([planned], root)
    return planned


# ---------------------------------------------------------------------------
# Returns PublishVerifyStageResult
# ---------------------------------------------------------------------------


def test_returns_stage_result_type(tmp_path: Path) -> None:
    p = _profile()
    pf = _write_profile(tmp_path, p)
    manifest = _manifest_from_files([pf])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert isinstance(result, PublishVerifyStageResult)
    assert result.stage == "profiles"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_single_valid_profile_passes(tmp_path: Path) -> None:
    p = _profile()
    pf = _write_profile(tmp_path, p)
    manifest = _manifest_from_files([pf])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is True
    assert result.checked == 1
    assert result.issues == ()


def test_multiple_valid_profiles_pass(tmp_path: Path) -> None:
    profiles = [
        _profile(bioguide_id="P000197", name="Nancy Pelosi", slug="nancy-pelosi"),
        _profile(bioguide_id="S000033", name="Bernie Sanders", slug="bernie-sanders", chamber="senate"),
    ]
    files = [_write_profile(tmp_path, p) for p in profiles]
    manifest = _manifest_from_files(files)
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is True
    assert result.checked == 2


def test_empty_manifest_passes(tmp_path: Path) -> None:
    manifest = _manifest_from_files([])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is True
    assert result.checked == 0


def test_non_member_entries_are_skipped(tmp_path: Path) -> None:
    """Entries like evidence/ and zip/ should not be counted or checked."""
    p = _profile()
    pf = _write_profile(tmp_path, p)
    other = PlannedFile.from_bytes("evidence/ec-001.json", b'{"x": 1}')
    manifest = _manifest_from_files([pf, other])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is True
    assert result.checked == 1  # only the member profile counted


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


def test_missing_profile_file_is_error(tmp_path: Path) -> None:
    # Put the path in the manifest but don't write the file.
    fake = PlannedFile.from_bytes(member_path("ghost-member"), b'{}')
    manifest = _manifest_from_files([fake])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is False
    assert result.error_count == 1
    assert any("missing" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Invalid JSON / corrupt file
# ---------------------------------------------------------------------------


def test_corrupt_json_is_error(tmp_path: Path) -> None:
    dest = tmp_path / member_path("bad-member")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"not json {{{{")
    fake = PlannedFile.from_bytes(member_path("bad-member"), dest.read_bytes())
    manifest = _manifest_from_files([fake])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is False
    assert result.error_count >= 1


def test_invalid_payload_schema_is_error(tmp_path: Path) -> None:
    dest = tmp_path / member_path("schema-fail")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(json.dumps({"bioguide_id": "X"}).encode())
    fake = PlannedFile.from_bytes(member_path("schema-fail"), dest.read_bytes())
    manifest = _manifest_from_files([fake])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is False
    assert result.error_count >= 1


# ---------------------------------------------------------------------------
# Identity coherence: slug
# ---------------------------------------------------------------------------


def test_slug_mismatch_is_error(tmp_path: Path) -> None:
    # Write a profile whose slug field differs from the file path.
    p = _profile(slug="real-slug")
    # Write it under a different path key.
    wrong_path = member_path("wrong-slug")
    planned = PlannedFile.from_bytes(wrong_path, serialize_payload(p))
    write_planned_files([planned], tmp_path)
    manifest = _manifest_from_files([planned])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is False
    assert any("slug" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Identity coherence: bioguide_id
# ---------------------------------------------------------------------------


def test_empty_bioguide_id_is_error(tmp_path: Path) -> None:
    p = _profile(bioguide_id="")
    pf = _write_profile(tmp_path, p)
    manifest = _manifest_from_files([pf])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is False
    assert any("bioguide_id" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Identity coherence: name shape
# ---------------------------------------------------------------------------


def test_single_token_name_is_error(tmp_path: Path) -> None:
    p = _profile(name="Madonna")
    pf = _write_profile(tmp_path, p)
    manifest = _manifest_from_files([pf])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is False
    assert any("name" in i.message for i in result.issues)


def test_two_part_name_passes(tmp_path: Path) -> None:
    p = _profile(name="Joe Biden")
    pf = _write_profile(tmp_path, p)
    manifest = _manifest_from_files([pf])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is True


def test_three_part_name_passes(tmp_path: Path) -> None:
    p = _profile(name="Nancy Patricia Pelosi", slug="nancy-pelosi")
    pf = _write_profile(tmp_path, p)
    manifest = _manifest_from_files([pf])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.ok is True


# ---------------------------------------------------------------------------
# Multiple issues in one run
# ---------------------------------------------------------------------------


def test_multiple_failures_are_all_reported(tmp_path: Path) -> None:
    good = _profile()
    bad_slug_profile = _profile(slug="real-slug", bioguide_id="X000001", name="One Two")
    missing_path = member_path("ghost")

    good_pf = _write_profile(tmp_path, good)
    # bad_slug: file written under wrong-slug, payload says real-slug
    wrong = PlannedFile.from_bytes(member_path("wrong-slug"), serialize_payload(bad_slug_profile))
    write_planned_files([wrong], tmp_path)
    missing_pf = PlannedFile.from_bytes(missing_path, b'{}')  # not written to disk

    manifest = _manifest_from_files([good_pf, wrong, missing_pf])
    result = verify_local_member_profiles(tmp_path, manifest)
    assert result.checked == 3
    assert result.ok is False
    # At minimum: slug mismatch + missing file
    assert result.error_count >= 2
