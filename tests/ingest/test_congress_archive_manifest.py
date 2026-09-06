"""Tests for src/ingest/congress/archive_manifest.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingest.congress.archive import (
    BillDetailSource,
    BillsSource,
    CommitteesSource,
    CongressArchiveManifest,
    CosponsorsSource,
    HouseVoteSource,
    MemberDetailSource,
    MembersSource,
    SenateVoteSource,
)
from src.ingest.congress.archive_manifest import load_manifest, write_manifest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, data: dict) -> Path:
    """Write *data* as JSON and return the manifest path."""
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


_MINIMAL = {
    "congress": 119,
    "members": "members.json",
    "committees": "committees.json",
    "bills": "bills.json",
    "cosponsors": [],
    "member_details": [],
    "bill_details": [],
}


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestLoadManifestHappyPath:
    def test_returns_congress_archive_manifest(self, tmp_path: Path) -> None:
        p = _write_manifest(tmp_path, _MINIMAL)
        result = load_manifest(p)
        assert isinstance(result, CongressArchiveManifest)

    def test_congress_number(self, tmp_path: Path) -> None:
        p = _write_manifest(tmp_path, _MINIMAL)
        assert load_manifest(p).congress == 119

    def test_list_source_paths_resolve_against_manifest_dir(self, tmp_path: Path) -> None:
        p = _write_manifest(tmp_path, _MINIMAL)
        m = load_manifest(p)
        assert m.members.path == tmp_path / "members.json"
        assert m.committees.path == tmp_path / "committees.json"
        assert m.bills.path == tmp_path / "bills.json"

    def test_list_sources_are_typed(self, tmp_path: Path) -> None:
        p = _write_manifest(tmp_path, _MINIMAL)
        m = load_manifest(p)
        assert isinstance(m.members, MembersSource)
        assert isinstance(m.committees, CommitteesSource)
        assert isinstance(m.bills, BillsSource)

    def test_empty_optional_collections(self, tmp_path: Path) -> None:
        p = _write_manifest(tmp_path, _MINIMAL)
        m = load_manifest(p)
        assert m.cosponsors == ()
        assert m.member_details == ()
        assert m.bill_details == ()
        assert m.house_votes == ()
        assert m.senate_votes == ()

    def test_cosponsor_entries(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL,
            "cosponsors": [
                {
                    "congress": 119,
                    "bill_type": "hr",
                    "bill_number": 1,
                    "path": "cosponsors/119_hr_1.json",
                }
            ],
        }
        p = _write_manifest(tmp_path, data)
        m = load_manifest(p)
        assert len(m.cosponsors) == 1
        cs = m.cosponsors[0]
        assert isinstance(cs, CosponsorsSource)
        assert cs.congress == 119
        assert cs.bill_type == "hr"
        assert cs.bill_number == 1
        assert cs.path == tmp_path / "cosponsors/119_hr_1.json"

    def test_member_detail_entries(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL,
            "member_details": [{"bioguide_id": "P000197", "path": "member_details/P000197.json"}],
        }
        p = _write_manifest(tmp_path, data)
        m = load_manifest(p)
        assert len(m.member_details) == 1
        md = m.member_details[0]
        assert isinstance(md, MemberDetailSource)
        assert md.bioguide_id == "P000197"
        assert md.path == tmp_path / "member_details/P000197.json"

    def test_bill_detail_entries(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL,
            "bill_details": [
                {
                    "congress": 119,
                    "bill_type": "s",
                    "bill_number": 42,
                    "path": "bill_details/119_s_42.json",
                }
            ],
        }
        p = _write_manifest(tmp_path, data)
        m = load_manifest(p)
        assert len(m.bill_details) == 1
        bd = m.bill_details[0]
        assert isinstance(bd, BillDetailSource)
        assert bd.congress == 119
        assert bd.bill_type == "s"
        assert bd.bill_number == 42

    def test_house_vote_entries(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL,
            "house_votes": [
                {"year": 2025, "roll_call_number": 7, "path": "house_votes/2025_0007.xml"}
            ],
        }
        p = _write_manifest(tmp_path, data)
        m = load_manifest(p)
        assert len(m.house_votes) == 1
        hv = m.house_votes[0]
        assert isinstance(hv, HouseVoteSource)
        assert hv.year == 2025
        assert hv.roll_call_number == 7
        assert hv.path == tmp_path / "house_votes/2025_0007.xml"

    def test_senate_vote_entries(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL,
            "senate_votes": [
                {
                    "congress": 119,
                    "session_number": 1,
                    "roll_call_number": 3,
                    "path": "senate_votes/119_1_00003.xml",
                }
            ],
        }
        p = _write_manifest(tmp_path, data)
        m = load_manifest(p)
        assert len(m.senate_votes) == 1
        sv = m.senate_votes[0]
        assert isinstance(sv, SenateVoteSource)
        assert sv.congress == 119
        assert sv.session_number == 1
        assert sv.roll_call_number == 3

    def test_house_senate_votes_omitted_defaults_to_empty(self, tmp_path: Path) -> None:
        data = {k: v for k, v in _MINIMAL.items()}
        # _MINIMAL has no house_votes / senate_votes keys
        p = _write_manifest(tmp_path, data)
        m = load_manifest(p)
        assert m.house_votes == ()
        assert m.senate_votes == ()

    def test_manifest_in_subdirectory_resolves_paths(self, tmp_path: Path) -> None:
        subdir = tmp_path / "archive" / "119"
        subdir.mkdir(parents=True)
        p = subdir / "manifest.json"
        p.write_text(json.dumps(_MINIMAL), encoding="utf-8")
        m = load_manifest(p)
        assert m.members.path == subdir / "members.json"

    def test_accepts_path_as_string(self, tmp_path: Path) -> None:
        p = _write_manifest(tmp_path, _MINIMAL)
        # load_manifest should accept a str path, not just Path
        m = load_manifest(str(p))  # type: ignore[arg-type]
        assert isinstance(m, CongressArchiveManifest)


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestLoadManifestErrors:
    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path / "nonexistent.json")

    def test_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "manifest.json"
        p.write_text("not json {{{", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_manifest(p)

    def test_missing_congress_field(self, tmp_path: Path) -> None:
        data = {k: v for k, v in _MINIMAL.items() if k != "congress"}
        p = _write_manifest(tmp_path, data)
        with pytest.raises(ValueError, match="congress"):
            load_manifest(p)

    def test_missing_members_field(self, tmp_path: Path) -> None:
        data = {k: v for k, v in _MINIMAL.items() if k != "members"}
        p = _write_manifest(tmp_path, data)
        with pytest.raises(ValueError, match="members"):
            load_manifest(p)

    def test_top_level_not_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "manifest.json"
        p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(TypeError):
            load_manifest(p)

    def test_congress_wrong_type(self, tmp_path: Path) -> None:
        data = {**_MINIMAL, "congress": "119"}
        p = _write_manifest(tmp_path, data)
        with pytest.raises(ValueError, match="congress"):
            load_manifest(p)

    def test_cosponsor_entry_missing_field(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL,
            "cosponsors": [{"congress": 119, "bill_type": "hr", "path": "x.json"}],
            # bill_number missing
        }
        p = _write_manifest(tmp_path, data)
        with pytest.raises(ValueError, match="bill_number"):
            load_manifest(p)


class TestWriteManifest:
    def _minimal_manifest(self, tmp_path: Path) -> CongressArchiveManifest:
        return CongressArchiveManifest(
            congress=119,
            members=MembersSource(path=tmp_path / "members.json", congress=119),
            committees=CommitteesSource(path=tmp_path / "committees.json", congress=119),
            bills=BillsSource(path=tmp_path / "bills.json", congress=119),
            cosponsors=(),
            member_details=(),
            bill_details=(),
            house_votes=(),
            senate_votes=(),
        )

    def test_write_manifest_roundtrips_through_loader(self, tmp_path: Path) -> None:
        manifest = CongressArchiveManifest(
            congress=119,
            members=MembersSource(path=tmp_path / "members.json", congress=119),
            committees=CommitteesSource(path=tmp_path / "committees.json", congress=119),
            bills=BillsSource(path=tmp_path / "bills.json", congress=119),
            cosponsors=(
                CosponsorsSource(
                    path=tmp_path / "cosponsors" / "119_hr_1.json",
                    congress=119,
                    bill_type="hr",
                    bill_number=1,
                ),
            ),
            member_details=(
                MemberDetailSource(
                    path=tmp_path / "member_details" / "P000197.json",
                    bioguide_id="P000197",
                ),
            ),
            bill_details=(
                BillDetailSource(
                    path=tmp_path / "bill_details" / "119_hr_1.json",
                    congress=119,
                    bill_type="hr",
                    bill_number=1,
                ),
            ),
            house_votes=(
                HouseVoteSource(
                    path=tmp_path / "house" / "2025" / "roll001.xml",
                    year=2025,
                    roll_call_number=1,
                ),
            ),
            senate_votes=(
                SenateVoteSource(
                    path=tmp_path / "senate" / "vote1191" / "vote_119_1_00003.xml",
                    congress=119,
                    session_number=1,
                    roll_call_number=3,
                ),
            ),
        )

        manifest_path = write_manifest(tmp_path / "manifest.json", manifest)
        loaded = load_manifest(manifest_path)

        assert manifest_path == tmp_path / "manifest.json"
        assert loaded == manifest

    def test_write_manifest_uses_unique_temp_file_before_replace(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        manifest = self._minimal_manifest(tmp_path)
        final_path = tmp_path / "manifest.json"
        seen_temp_names: list[str] = []
        original_open = Path.open

        def guarded_open(path: Path, *args: object, **kwargs: object):
            mode = args[0] if args else kwargs.get("mode", "r")
            if path == final_path and isinstance(mode, str) and "w" in mode:
                raise AssertionError("write_manifest wrote directly to final path")
            if path.name.startswith(".manifest.json.") and path.name.endswith(".tmp"):
                seen_temp_names.append(path.name)
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", guarded_open)

        assert write_manifest(final_path, manifest) == final_path

        assert len(seen_temp_names) == 1
        assert load_manifest(final_path) == manifest

    def test_write_manifest_cleans_temp_file_when_replace_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        manifest = self._minimal_manifest(tmp_path)
        final_path = tmp_path / "manifest.json"
        original_replace = Path.replace
        temp_paths: list[Path] = []

        def failing_replace(path: Path, target: Path) -> Path:
            if target == final_path and path.name.startswith(".manifest.json."):
                temp_paths.append(path)
                raise OSError("replace failed")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", failing_replace)

        with pytest.raises(OSError, match="replace failed"):
            write_manifest(final_path, manifest)

        assert not final_path.exists()
        assert temp_paths
        assert all(not path.exists() for path in temp_paths)
