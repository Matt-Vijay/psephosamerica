"""Tests for src/ingest/congress/archive.py — typed source contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ingest.congress.archive import (
    BillDetailSource,
    BillsSource,
    CommitteesSource,
    CongressArchive,
    CongressArchiveManifest,
    CosponsorsSource,
    HouseVoteSource,
    MemberDetailSource,
    MembersSource,
    SenateVoteSource,
    congress_archive_manifest_to_dict,
    manifest_from_archive,
    manifest_from_dict,
    manifest_from_existing_archive,
    parse_bill_stem,
)


# ---------------------------------------------------------------------------
# Immutable source type construction
# ---------------------------------------------------------------------------


class TestSourceTypes:
    def test_members_source_frozen(self, tmp_path: Path) -> None:
        src = MembersSource(path=tmp_path / "members.json", congress=119)
        assert src.congress == 119
        assert src.path == tmp_path / "members.json"
        with pytest.raises(AttributeError):
            src.congress = 120  # type: ignore[misc]

    def test_committees_source(self, tmp_path: Path) -> None:
        src = CommitteesSource(path=tmp_path / "committees.json", congress=119)
        assert src.congress == 119

    def test_bills_source(self, tmp_path: Path) -> None:
        src = BillsSource(path=tmp_path / "bills.json", congress=119)
        assert src.congress == 119

    def test_cosponsors_source(self, tmp_path: Path) -> None:
        src = CosponsorsSource(
            path=tmp_path / "cosponsors/119_hr_1.json",
            congress=119,
            bill_type="hr",
            bill_number=1,
        )
        assert src.congress == 119
        assert src.bill_type == "hr"
        assert src.bill_number == 1

    def test_member_detail_source(self, tmp_path: Path) -> None:
        src = MemberDetailSource(
            path=tmp_path / "member_details/P000197.json",
            bioguide_id="P000197",
        )
        assert src.bioguide_id == "P000197"

    def test_bill_detail_source(self, tmp_path: Path) -> None:
        src = BillDetailSource(
            path=tmp_path / "bill_details/119_hr_1.json",
            congress=119,
            bill_type="hr",
            bill_number=1,
        )
        assert src.congress == 119
        assert src.bill_type == "hr"

    def test_house_vote_source(self, tmp_path: Path) -> None:
        src = HouseVoteSource(
            path=tmp_path / "house_votes/2025_0042.xml",
            year=2025,
            roll_call_number=42,
        )
        assert src.year == 2025
        assert src.roll_call_number == 42

    def test_senate_vote_source(self, tmp_path: Path) -> None:
        src = SenateVoteSource(
            path=tmp_path / "senate_votes/119_1_00010.xml",
            congress=119,
            session_number=1,
            roll_call_number=10,
        )
        assert src.congress == 119
        assert src.session_number == 1
        assert src.roll_call_number == 10


# ---------------------------------------------------------------------------
# CongressArchive path resolution
# ---------------------------------------------------------------------------


class TestCongressArchive:
    def test_members_source(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        src = arch.members_source()
        assert isinstance(src, MembersSource)
        assert src.path == tmp_path / "members.json"
        assert src.congress == 119

    def test_committees_source(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        src = arch.committees_source()
        assert isinstance(src, CommitteesSource)
        assert src.path == tmp_path / "committees.json"

    def test_bills_source(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        src = arch.bills_source()
        assert isinstance(src, BillsSource)
        assert src.path == tmp_path / "bills.json"

    def test_member_detail_source(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        src = arch.member_detail_source("P000197")
        assert isinstance(src, MemberDetailSource)
        assert src.bioguide_id == "P000197"
        assert src.path == tmp_path / "member_details" / "P000197.json"

    def test_bill_detail_source(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        src = arch.bill_detail_source(119, "hr", 1)
        assert isinstance(src, BillDetailSource)
        assert src.path == tmp_path / "bill_details" / "119_hr_1.json"
        assert src.bill_type == "hr"
        assert src.bill_number == 1

    def test_cosponsors_source(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        src = arch.cosponsors_source(119, "s", 42)
        assert isinstance(src, CosponsorsSource)
        assert src.path == tmp_path / "cosponsors" / "119_s_42.json"

    def test_house_vote_source(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        src = arch.house_vote_source(2025, 42)
        assert isinstance(src, HouseVoteSource)
        assert src.path == tmp_path / "house_votes" / "2025_0042.xml"
        assert src.year == 2025
        assert src.roll_call_number == 42

    def test_house_vote_source_zero_pads_to_four(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        src = arch.house_vote_source(2025, 5)
        assert src.path.name == "2025_0005.xml"

    def test_senate_vote_source(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        src = arch.senate_vote_source(119, 1, 10)
        assert isinstance(src, SenateVoteSource)
        assert src.path == tmp_path / "senate_votes" / "119_1_00010.xml"

    def test_senate_vote_source_zero_pads_to_five(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        src = arch.senate_vote_source(119, 2, 3)
        assert src.path.name == "119_2_00003.xml"

    def test_directory_accessors(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        assert arch.member_details_dir() == tmp_path / "member_details"
        assert arch.bill_details_dir() == tmp_path / "bill_details"
        assert arch.cosponsors_dir() == tmp_path / "cosponsors"
        assert arch.house_votes_dir() == tmp_path / "house_votes"
        assert arch.senate_votes_dir() == tmp_path / "senate_votes"

    def test_legacy_path_helpers(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        assert arch.members_path() == tmp_path / "members.json"
        assert arch.committees_path() == tmp_path / "committees.json"
        assert arch.bills_path() == tmp_path / "bills.json"
        assert arch.member_detail_path("P000197") == tmp_path / "member_details" / "P000197.json"
        assert arch.bill_detail_path(119, "hr", 1) == tmp_path / "bill_details" / "119_hr_1.json"
        assert arch.cosponsors_path(119, "hr", 1) == tmp_path / "cosponsors" / "119_hr_1.json"


# ---------------------------------------------------------------------------
# manifest_from_archive
# ---------------------------------------------------------------------------


class TestManifestFromArchive:
    def test_minimal_manifest_no_details(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        assert isinstance(manifest, CongressArchiveManifest)
        assert manifest.congress == 119
        assert manifest.cosponsors == ()
        assert manifest.member_details == ()
        assert manifest.bill_details == ()
        assert manifest.house_votes == ()
        assert manifest.senate_votes == ()

    def test_manifest_with_bill_keys(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        bill_keys = [(119, "hr", 1), (119, "s", 2)]
        manifest = manifest_from_archive(arch, bill_keys=bill_keys)
        assert len(manifest.cosponsors) == 2
        assert len(manifest.bill_details) == 2
        assert manifest.cosponsors[0].bill_type == "hr"
        assert manifest.bill_details[1].bill_type == "s"

    def test_manifest_with_bioguide_ids(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch, bioguide_ids=["P000197", "S000148"])
        assert len(manifest.member_details) == 2
        ids = {s.bioguide_id for s in manifest.member_details}
        assert ids == {"P000197", "S000148"}

    def test_manifest_with_house_votes(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch, house_vote_keys=[(2025, 42), (2025, 43)])
        assert len(manifest.house_votes) == 2
        assert manifest.house_votes[0].year == 2025

    def test_manifest_with_senate_votes(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch, senate_vote_keys=[(119, 1, 10), (119, 1, 11)])
        assert len(manifest.senate_votes) == 2
        assert manifest.senate_votes[0].session_number == 1

    def test_manifest_list_source_paths_match_archive(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        assert manifest.members.path == arch.members_path()
        assert manifest.committees.path == arch.committees_path()
        assert manifest.bills.path == arch.bills_path()


class TestManifestSerialization:
    def test_to_dict_relativizes_paths_against_root(self, tmp_path: Path) -> None:
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

        result = congress_archive_manifest_to_dict(manifest, root=tmp_path)

        assert result == {
            "congress": 119,
            "members": "members.json",
            "committees": "committees.json",
            "bills": "bills.json",
            "cosponsors": [
                {
                    "congress": 119,
                    "bill_type": "hr",
                    "bill_number": 1,
                    "path": "cosponsors/119_hr_1.json",
                }
            ],
            "member_details": [
                {
                    "bioguide_id": "P000197",
                    "path": "member_details/P000197.json",
                }
            ],
            "bill_details": [
                {
                    "congress": 119,
                    "bill_type": "hr",
                    "bill_number": 1,
                    "path": "bill_details/119_hr_1.json",
                }
            ],
            "house_votes": [
                {
                    "year": 2025,
                    "roll_call_number": 1,
                    "path": "house/2025/roll001.xml",
                }
            ],
            "senate_votes": [
                {
                    "congress": 119,
                    "session_number": 1,
                    "roll_call_number": 3,
                    "path": "senate/vote1191/vote_119_1_00003.xml",
                }
            ],
        }


class TestManifestFromExistingArchive:
    def test_scans_existing_archive_tree_including_real_vote_layout(self, tmp_path: Path) -> None:
        (tmp_path / "members.json").write_text("{}", encoding="utf-8")
        (tmp_path / "committees.json").write_text("{}", encoding="utf-8")
        (tmp_path / "bills.json").write_text("{}", encoding="utf-8")
        member_details_dir = tmp_path / "member_details"
        member_details_dir.mkdir()
        (member_details_dir / "P000197.json").write_text("{}", encoding="utf-8")
        bill_details_dir = tmp_path / "bill_details"
        bill_details_dir.mkdir()
        (bill_details_dir / "119_hr_1.json").write_text("{}", encoding="utf-8")
        cosponsors_dir = tmp_path / "cosponsors"
        cosponsors_dir.mkdir()
        (cosponsors_dir / "119_hr_1.json").write_text("{}", encoding="utf-8")
        house_year_dir = tmp_path / "house" / "2025"
        house_year_dir.mkdir(parents=True)
        (house_year_dir / "index.xml").write_text("<votes />", encoding="utf-8")
        (house_year_dir / "roll001.xml").write_text("<rollcall-vote />", encoding="utf-8")
        senate_dir = tmp_path / "senate" / "vote1191"
        senate_dir.mkdir(parents=True)
        (senate_dir / "vote_summary.xml").write_text("<votes />", encoding="utf-8")
        (senate_dir / "vote_119_1_00003.xml").write_text("<vote />", encoding="utf-8")

        manifest = manifest_from_existing_archive(CongressArchive(tmp_path, congress=119))

        assert manifest.congress == 119
        assert [source.bioguide_id for source in manifest.member_details] == ["P000197"]
        assert [
            (source.congress, source.bill_type, source.bill_number)
            for source in manifest.bill_details
        ] == [(119, "hr", 1)]
        assert [
            (source.congress, source.bill_type, source.bill_number)
            for source in manifest.cosponsors
        ] == [(119, "hr", 1)]
        assert [
            (source.year, source.roll_call_number, source.path.relative_to(tmp_path).as_posix())
            for source in manifest.house_votes
        ] == [(2025, 1, "house/2025/roll001.xml")]
        assert [
            (
                source.congress,
                source.session_number,
                source.roll_call_number,
                source.path.relative_to(tmp_path).as_posix(),
            )
            for source in manifest.senate_votes
        ] == [(119, 1, 3, "senate/vote1191/vote_119_1_00003.xml")]


# ---------------------------------------------------------------------------
# manifest_from_dict
# ---------------------------------------------------------------------------


_MINIMAL_DICT: dict = {
    "congress": 119,
    "members": "members.json",
    "committees": "committees.json",
    "bills": "bills.json",
    "cosponsors": [],
    "member_details": [],
    "bill_details": [],
}


class TestManifestFromDict:
    def test_minimal_valid_dict(self, tmp_path: Path) -> None:
        manifest = manifest_from_dict(_MINIMAL_DICT, root=tmp_path)
        assert manifest.congress == 119
        assert manifest.members.path == tmp_path / "members.json"
        assert manifest.committees.path == tmp_path / "committees.json"
        assert manifest.bills.path == tmp_path / "bills.json"
        assert manifest.cosponsors == ()
        assert manifest.house_votes == ()
        assert manifest.senate_votes == ()

    def test_paths_resolved_against_root(self, tmp_path: Path) -> None:
        root = tmp_path / "archive"
        manifest = manifest_from_dict(_MINIMAL_DICT, root=root)
        assert manifest.members.path == root / "members.json"

    def test_top_level_paths_must_stay_inside_root(self, tmp_path: Path) -> None:
        data = {**_MINIMAL_DICT, "members": "../members.json"}
        with pytest.raises(ValueError, match="members"):
            manifest_from_dict(data, root=tmp_path)

    def test_top_level_paths_reject_symlink_escape(self, tmp_path: Path) -> None:
        root = tmp_path / "archive"
        outside = tmp_path / "outside"
        outside.mkdir()
        root.mkdir()
        (root / "linked").symlink_to(outside, target_is_directory=True)
        data = {**_MINIMAL_DICT, "members": "linked/members.json"}

        with pytest.raises(ValueError, match="manifest.members"):
            manifest_from_dict(data, root=root)

    def test_nested_paths_must_stay_inside_root(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL_DICT,
            "cosponsors": [
                {
                    "congress": 119,
                    "bill_type": "hr",
                    "bill_number": 1,
                    "path": "/tmp/outside.json",
                }
            ],
        }
        with pytest.raises(ValueError, match="cosponsors"):
            manifest_from_dict(data, root=tmp_path)

    def test_nested_paths_reject_symlink_escape(self, tmp_path: Path) -> None:
        root = tmp_path / "archive"
        outside = tmp_path / "outside"
        outside.mkdir()
        root.mkdir()
        (root / "cosponsors").symlink_to(outside, target_is_directory=True)
        data = {
            **_MINIMAL_DICT,
            "cosponsors": [
                {
                    "congress": 119,
                    "bill_type": "hr",
                    "bill_number": 1,
                    "path": "cosponsors/119_hr_1.json",
                }
            ],
        }

        with pytest.raises(ValueError, match="cosponsors"):
            manifest_from_dict(data, root=root)

    def test_cosponsor_entries_parsed(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL_DICT,
            "cosponsors": [
                {
                    "congress": 119,
                    "bill_type": "hr",
                    "bill_number": 1,
                    "path": "cosponsors/119_hr_1.json",
                }
            ],
        }
        manifest = manifest_from_dict(data, root=tmp_path)
        assert len(manifest.cosponsors) == 1
        cs = manifest.cosponsors[0]
        assert cs.congress == 119
        assert cs.bill_type == "hr"
        assert cs.bill_number == 1
        assert cs.path == tmp_path / "cosponsors/119_hr_1.json"

    def test_member_detail_entries_parsed(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL_DICT,
            "member_details": [{"bioguide_id": "P000197", "path": "member_details/P000197.json"}],
        }
        manifest = manifest_from_dict(data, root=tmp_path)
        assert len(manifest.member_details) == 1
        assert manifest.member_details[0].bioguide_id == "P000197"

    def test_bill_detail_entries_parsed(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL_DICT,
            "bill_details": [
                {
                    "congress": 119,
                    "bill_type": "s",
                    "bill_number": 5,
                    "path": "bill_details/119_s_5.json",
                }
            ],
        }
        manifest = manifest_from_dict(data, root=tmp_path)
        assert len(manifest.bill_details) == 1
        bd = manifest.bill_details[0]
        assert bd.bill_type == "s"
        assert bd.bill_number == 5

    def test_house_votes_optional_omitted(self, tmp_path: Path) -> None:
        manifest = manifest_from_dict(_MINIMAL_DICT, root=tmp_path)
        assert manifest.house_votes == ()

    def test_house_votes_parsed(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL_DICT,
            "house_votes": [
                {"year": 2025, "roll_call_number": 42, "path": "house_votes/2025_0042.xml"}
            ],
        }
        manifest = manifest_from_dict(data, root=tmp_path)
        assert len(manifest.house_votes) == 1
        hv = manifest.house_votes[0]
        assert hv.year == 2025
        assert hv.roll_call_number == 42
        assert hv.path == tmp_path / "house_votes/2025_0042.xml"

    def test_senate_votes_parsed(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL_DICT,
            "senate_votes": [
                {
                    "congress": 119,
                    "session_number": 1,
                    "roll_call_number": 10,
                    "path": "senate_votes/119_1_00010.xml",
                }
            ],
        }
        manifest = manifest_from_dict(data, root=tmp_path)
        assert len(manifest.senate_votes) == 1
        sv = manifest.senate_votes[0]
        assert sv.congress == 119
        assert sv.session_number == 1

    def test_missing_required_key_raises(self, tmp_path: Path) -> None:
        for key in (
            "congress",
            "members",
            "committees",
            "bills",
            "cosponsors",
            "member_details",
            "bill_details",
        ):
            data = {k: v for k, v in _MINIMAL_DICT.items() if k != key}
            with pytest.raises(ValueError, match=key):
                manifest_from_dict(data, root=tmp_path)

    def test_congress_as_bool_rejected(self, tmp_path: Path) -> None:
        data = {**_MINIMAL_DICT, "congress": True}
        with pytest.raises(ValueError):
            manifest_from_dict(data, root=tmp_path)

    def test_non_dict_raises_type_error(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError):
            manifest_from_dict([], root=tmp_path)  # type: ignore[arg-type]

    def test_cosponsors_entry_missing_field_raises(self, tmp_path: Path) -> None:
        data = {
            **_MINIMAL_DICT,
            "cosponsors": [{"congress": 119, "bill_type": "hr"}],  # missing bill_number and path
        }
        with pytest.raises(ValueError):
            manifest_from_dict(data, root=tmp_path)

    def test_member_detail_entry_not_dict_raises(self, tmp_path: Path) -> None:
        data = {**_MINIMAL_DICT, "member_details": ["not-a-dict"]}
        with pytest.raises(ValueError):
            manifest_from_dict(data, root=tmp_path)


# ---------------------------------------------------------------------------
# parse_bill_stem
# ---------------------------------------------------------------------------


class TestParseBillStem:
    def test_basic_hr(self) -> None:
        assert parse_bill_stem("119_hr_1") == (119, "hr", 1)

    def test_senate_bill(self) -> None:
        assert parse_bill_stem("118_s_42") == (118, "s", 42)

    def test_joint_resolution(self) -> None:
        congress, bill_type, bill_number = parse_bill_stem("119_hjres_100")
        assert congress == 119
        assert bill_type == "hjres"
        assert bill_number == 100

    def test_invalid_stem_too_few_parts(self) -> None:
        with pytest.raises(ValueError, match="unexpected bill stem"):
            parse_bill_stem("119_hr")

    def test_invalid_stem_non_integer_congress(self) -> None:
        with pytest.raises(ValueError):
            parse_bill_stem("abc_hr_1")
