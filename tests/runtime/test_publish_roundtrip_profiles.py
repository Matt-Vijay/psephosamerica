"""Tests for src/runtime/publish_roundtrip_profiles.py.

Strategy
--------
- Use real temp publish trees (write actual files via write_planned_files).
- Patch only the four irreducible DB fetch boundaries:
    fetch_member_row_by_slug, fetch_member_score_snapshot_rows,
    fetch_member_rule_fire_rows, fetch_member_committee_rows.
- Compare typed payloads, not raw JSON bytes.
- No network calls.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.export.contracts import MemberProfilePayload
from src.export.filesystem import write_planned_files
from src.export.manifest import ManifestEntry, SnapshotManifest
from src.export.writer import PlannedFile, member_path, serialize_payload
from src.runtime.publish_roundtrip_profiles import verify_published_member_profiles_roundtrip
from src.runtime.publish_roundtrip_types import PublishRoundtripStageResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SNAPSHOT_DATE = date(2026, 4, 14)

_DB_MODULE = "src.runtime.publish_roundtrip_profiles"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _profile(
    *,
    bioguide_id: str = "P000197",
    name: str = "Nancy Pelosi",
    slug: str = "nancy-pelosi",
    state: str = "CA",
    district: str | None = None,
    chamber: str = "house",
    party: str = "Democrat",
    snapshot_date: date = SNAPSHOT_DATE,
) -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id=bioguide_id,
        name=name,
        slug=slug,
        state=state,
        district=district,
        chamber=chamber,  # type: ignore[arg-type]
        party=party,
        scores=[],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=0,
        snapshot_date=snapshot_date,
    )


def _member_row(profile: MemberProfilePayload, *, member_id: int = 1) -> dict:
    """Build a DB member-row dict matching *profile*.

    ``full_name`` maps to ``name``; ``district`` is stored as int (or None)
    because ``_normalize_member`` calls ``str()`` on it.
    """
    return {
        "id": member_id,
        "bioguide_id": profile.bioguide_id,
        "full_name": profile.name,
        "slug": profile.slug,
        "state": profile.state,
        "district": int(profile.district) if profile.district is not None else None,
        "chamber": profile.chamber,
        "party": profile.party,
    }


def _score_snapshot_row(*, member_id: int = 1, snapshot_at: date = SNAPSHOT_DATE) -> dict:
    """Minimal score_snapshot row that pins snapshot_date."""
    return {
        "id": 1,
        "member_id": member_id,
        "snapshot_at": snapshot_at,
        "score_total": 0.0,
        "dimension_scores": {},
        "published_at": None,
    }


def _manifest_from_files(
    files: list[PlannedFile], *, snapshot_id: str = "2026-04-14"
) -> SnapshotManifest:
    return SnapshotManifest(
        snapshot_id=snapshot_id,
        created_at=datetime(2026, 4, 14, 0, 0, 0),
        entries=[
            ManifestEntry(path=f.path, sha256=f.sha256, size_bytes=f.size_bytes)
            for f in files
        ],
        total_files=len(files),
        total_bytes=sum(f.size_bytes for f in files),
    )


def _write_profile(root: Path, profile: MemberProfilePayload) -> PlannedFile:
    planned = PlannedFile.from_bytes(member_path(profile.slug), serialize_payload(profile))
    write_planned_files([planned], root)
    return planned


def _run_roundtrip(
    root: Path,
    manifest: SnapshotManifest,
    *,
    member_row_val: dict | None,
    score_rows: list | None = None,
    fire_rows: list | None = None,
    committee_rows: list | None = None,
) -> PublishRoundtripStageResult:
    """Run the function under test with all four DB fetches patched."""
    conn = MagicMock()
    with ExitStack() as stack:
        stack.enter_context(
            patch(f"{_DB_MODULE}.fetch_member_row_by_slug", return_value=member_row_val)
        )
        stack.enter_context(
            patch(f"{_DB_MODULE}.fetch_member_score_snapshot_rows", return_value=score_rows or [])
        )
        stack.enter_context(
            patch(f"{_DB_MODULE}.fetch_member_rule_fire_rows", return_value=fire_rows or [])
        )
        stack.enter_context(
            patch(f"{_DB_MODULE}.fetch_member_committee_rows", return_value=committee_rows or [])
        )
        return verify_published_member_profiles_roundtrip(conn, root, manifest)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


def test_returns_stage_result_type(tmp_path: Path) -> None:
    profile = _profile()
    pf = _write_profile(tmp_path, profile)
    manifest = _manifest_from_files([pf])
    result = _run_roundtrip(
        tmp_path,
        manifest,
        member_row_val=_member_row(profile),
        score_rows=[_score_snapshot_row()],
    )
    assert isinstance(result, PublishRoundtripStageResult)
    assert result.stage == "profiles"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_single_matching_profile_passes(tmp_path: Path) -> None:
    profile = _profile()
    pf = _write_profile(tmp_path, profile)
    manifest = _manifest_from_files([pf])
    result = _run_roundtrip(
        tmp_path,
        manifest,
        member_row_val=_member_row(profile),
        score_rows=[_score_snapshot_row()],
    )
    assert result.ok is True
    assert result.checked == 1
    assert result.issues == ()


def test_multiple_matching_profiles_pass(tmp_path: Path) -> None:
    profiles = [
        _profile(bioguide_id="P000197", name="Nancy Pelosi", slug="nancy-pelosi"),
        _profile(
            bioguide_id="S000033",
            name="Bernie Sanders",
            slug="bernie-sanders",
            chamber="senate",
        ),
    ]
    pfs = [_write_profile(tmp_path, p) for p in profiles]
    manifest = _manifest_from_files(pfs)
    conn = MagicMock()

    def _fake_member_row(_, slug):
        for p in profiles:
            if p.slug == slug:
                return _member_row(p)
        return None

    def _fake_snapshot_rows(_, member_id):
        return [_score_snapshot_row(member_id=member_id)]

    with ExitStack() as stack:
        stack.enter_context(
            patch(f"{_DB_MODULE}.fetch_member_row_by_slug", side_effect=_fake_member_row)
        )
        stack.enter_context(
            patch(
                f"{_DB_MODULE}.fetch_member_score_snapshot_rows",
                side_effect=_fake_snapshot_rows,
            )
        )
        stack.enter_context(
            patch(f"{_DB_MODULE}.fetch_member_rule_fire_rows", return_value=[])
        )
        stack.enter_context(
            patch(f"{_DB_MODULE}.fetch_member_committee_rows", return_value=[])
        )
        result = verify_published_member_profiles_roundtrip(conn, tmp_path, manifest)

    assert result.ok is True
    assert result.checked == 2
    assert result.issues == ()


def test_empty_manifest_passes(tmp_path: Path) -> None:
    manifest = _manifest_from_files([])
    conn = MagicMock()
    result = verify_published_member_profiles_roundtrip(conn, tmp_path, manifest)
    assert result.ok is True
    assert result.checked == 0
    assert result.issues == ()


def test_non_member_entries_are_skipped(tmp_path: Path) -> None:
    """evidence/ and zip/ entries must not be counted or checked."""
    profile = _profile()
    pf = _write_profile(tmp_path, profile)
    other = PlannedFile.from_bytes("evidence/ec-001.json", b'{"x": 1}')
    manifest = _manifest_from_files([pf, other])
    result = _run_roundtrip(
        tmp_path,
        manifest,
        member_row_val=_member_row(profile),
        score_rows=[_score_snapshot_row()],
    )
    assert result.ok is True
    assert result.checked == 1  # only the member profile


def test_profile_with_district_passes(tmp_path: Path) -> None:
    profile = _profile(district="5")
    pf = _write_profile(tmp_path, profile)
    manifest = _manifest_from_files([pf])
    row = _member_row(profile)  # district stored as int 5
    result = _run_roundtrip(
        tmp_path,
        manifest,
        member_row_val=row,
        score_rows=[_score_snapshot_row()],
    )
    assert result.ok is True


# ---------------------------------------------------------------------------
# Missing published file
# ---------------------------------------------------------------------------


def test_missing_published_file_is_error(tmp_path: Path) -> None:
    # Manifest references a file that was never written to disk.
    fake = PlannedFile.from_bytes(member_path("ghost-member"), b"{}")
    manifest = _manifest_from_files([fake])
    # DB fetch should not be reached, but patch for safety.
    result = _run_roundtrip(tmp_path, manifest, member_row_val=None)
    assert result.ok is False
    assert result.checked == 1
    assert result.error_count == 1
    assert any("cannot load" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Member not in DB
# ---------------------------------------------------------------------------


def test_member_not_found_in_db_is_error(tmp_path: Path) -> None:
    profile = _profile()
    pf = _write_profile(tmp_path, profile)
    manifest = _manifest_from_files([pf])
    result = _run_roundtrip(tmp_path, manifest, member_row_val=None)
    assert result.ok is False
    assert result.error_count == 1
    assert any("not found in DB" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Field mismatches
# ---------------------------------------------------------------------------


def test_name_mismatch_is_error(tmp_path: Path) -> None:
    profile = _profile(name="Nancy Pelosi")
    pf = _write_profile(tmp_path, profile)
    manifest = _manifest_from_files([pf])
    # DB has a different name
    row = _member_row(profile)
    row["full_name"] = "Nancy P. Pelosi"
    result = _run_roundtrip(
        tmp_path, manifest, member_row_val=row, score_rows=[_score_snapshot_row()]
    )
    assert result.ok is False
    assert any("name" in i.message for i in result.issues)


def test_bioguide_id_mismatch_is_error(tmp_path: Path) -> None:
    profile = _profile(bioguide_id="P000197")
    pf = _write_profile(tmp_path, profile)
    manifest = _manifest_from_files([pf])
    row = _member_row(profile)
    row["bioguide_id"] = "P999999"
    result = _run_roundtrip(
        tmp_path, manifest, member_row_val=row, score_rows=[_score_snapshot_row()]
    )
    assert result.ok is False
    assert any("bioguide_id" in i.message for i in result.issues)


def test_state_mismatch_is_error(tmp_path: Path) -> None:
    profile = _profile(state="CA")
    pf = _write_profile(tmp_path, profile)
    manifest = _manifest_from_files([pf])
    row = _member_row(profile)
    row["state"] = "NY"
    result = _run_roundtrip(
        tmp_path, manifest, member_row_val=row, score_rows=[_score_snapshot_row()]
    )
    assert result.ok is False
    assert any("state" in i.message for i in result.issues)


def test_party_mismatch_is_error(tmp_path: Path) -> None:
    profile = _profile(party="Democrat")
    pf = _write_profile(tmp_path, profile)
    manifest = _manifest_from_files([pf])
    row = _member_row(profile)
    row["party"] = "Republican"
    result = _run_roundtrip(
        tmp_path, manifest, member_row_val=row, score_rows=[_score_snapshot_row()]
    )
    assert result.ok is False
    assert any("party" in i.message for i in result.issues)


def test_snapshot_date_mismatch_is_error(tmp_path: Path) -> None:
    profile = _profile(snapshot_date=SNAPSHOT_DATE)
    pf = _write_profile(tmp_path, profile)
    manifest = _manifest_from_files([pf])
    # DB has a different snapshot date
    different_date = date(2026, 3, 1)
    result = _run_roundtrip(
        tmp_path,
        manifest,
        member_row_val=_member_row(profile),
        score_rows=[_score_snapshot_row(snapshot_at=different_date)],
    )
    assert result.ok is False
    assert any("snapshot_date" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Multiple issues
# ---------------------------------------------------------------------------


def test_multiple_field_mismatches_all_reported(tmp_path: Path) -> None:
    profile = _profile(name="Nancy Pelosi", state="CA", party="Democrat")
    pf = _write_profile(tmp_path, profile)
    manifest = _manifest_from_files([pf])
    row = _member_row(profile)
    row["full_name"] = "N. Pelosi"
    row["state"] = "NY"
    row["party"] = "Republican"
    result = _run_roundtrip(
        tmp_path, manifest, member_row_val=row, score_rows=[_score_snapshot_row()]
    )
    assert result.ok is False
    assert result.error_count >= 3


def test_one_passing_one_failing_profile(tmp_path: Path) -> None:
    good = _profile(
        bioguide_id="P000197", name="Nancy Pelosi", slug="nancy-pelosi"
    )
    bad = _profile(
        bioguide_id="S000033", name="Bernie Sanders", slug="bernie-sanders", chamber="senate"
    )
    good_pf = _write_profile(tmp_path, good)
    bad_pf = _write_profile(tmp_path, bad)
    manifest = _manifest_from_files([good_pf, bad_pf])
    conn = MagicMock()

    good_row = _member_row(good, member_id=1)
    bad_row = _member_row(bad, member_id=2)
    # Corrupt the DB row for the bad profile
    bad_row["state"] = "VT"  # published profile has CA, DB says VT

    def _fake_member_row(_, slug):
        if slug == "nancy-pelosi":
            return good_row
        if slug == "bernie-sanders":
            return bad_row
        return None

    def _fake_snapshot_rows(_, member_id):
        return [_score_snapshot_row(member_id=member_id)]

    with ExitStack() as stack:
        stack.enter_context(
            patch(f"{_DB_MODULE}.fetch_member_row_by_slug", side_effect=_fake_member_row)
        )
        stack.enter_context(
            patch(
                f"{_DB_MODULE}.fetch_member_score_snapshot_rows",
                side_effect=_fake_snapshot_rows,
            )
        )
        stack.enter_context(
            patch(f"{_DB_MODULE}.fetch_member_rule_fire_rows", return_value=[])
        )
        stack.enter_context(
            patch(f"{_DB_MODULE}.fetch_member_committee_rows", return_value=[])
        )
        result = verify_published_member_profiles_roundtrip(conn, tmp_path, manifest)

    assert result.checked == 2
    assert result.ok is False
    assert result.error_count >= 1
    # Good profile produced no issues; only bad profile did.
    assert any("bernie-sanders" in (i.path or "") for i in result.issues)
    assert not any("nancy-pelosi" in (i.path or "") for i in result.issues)
