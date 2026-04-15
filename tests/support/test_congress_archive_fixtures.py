"""Tests for tests/support/congress_archive_fixtures.py.

Validates that:
- CongressArchiveBuilder writes the expected directory layout.
- make_archive() produces a readable, correctly-rooted CongressArchive.
- All payload types (members, committees, bills, member_details,
  bill_details, cosponsors, house_votes, senate_votes) round-trip.
- Empty optional groups (no votes) work without side effects.
- Overrides and no_* helpers apply correctly.

No network calls; all I/O is scoped to pytest's tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.ingest.congress.archive import CongressArchive
from src.ingest.congress.archive_loader import (
    load_bills_payload,
    load_bill_detail_payload_map,
    load_committees_payload,
    load_cosponsors_payload_map,
    load_member_detail_payload_map,
    load_members_payload,
)
from tests.support.congress_archive_fixtures import (
    CongressArchiveBuilder,
    make_archive,
)


# ---------------------------------------------------------------------------
# CongressArchiveBuilder — file layout
# ---------------------------------------------------------------------------


class TestCongressArchiveBuilderLayout:
    def test_mandatory_files_exist(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path, congress=119)
        builder.build()
        assert (tmp_path / "members.json").is_file()
        assert (tmp_path / "committees.json").is_file()
        assert (tmp_path / "bills.json").is_file()

    def test_members_json_shape(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.build()
        data = json.loads((tmp_path / "members.json").read_text())
        assert "members" in data
        assert isinstance(data["members"], list)
        assert len(data["members"]) >= 1

    def test_committees_json_shape(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.build()
        data = json.loads((tmp_path / "committees.json").read_text())
        assert "committees" in data
        assert isinstance(data["committees"], list)

    def test_bills_json_shape(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.build()
        data = json.loads((tmp_path / "bills.json").read_text())
        assert "bills" in data
        assert isinstance(data["bills"], list)

    def test_member_details_directory_created(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.build()
        assert (tmp_path / "member_details").is_dir()
        assert (tmp_path / "member_details" / "P000197.json").is_file()

    def test_bill_details_directory_created(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.build()
        assert (tmp_path / "bill_details").is_dir()
        assert (tmp_path / "bill_details" / "119_hr_1.json").is_file()

    def test_cosponsors_directory_created(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.build()
        assert (tmp_path / "cosponsors").is_dir()
        assert (tmp_path / "cosponsors" / "119_hr_1.json").is_file()

    def test_no_vote_dirs_when_not_requested(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.build()
        assert not (tmp_path / "house_votes").exists()
        assert not (tmp_path / "senate_votes").exists()

    def test_house_vote_file_written(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.add_house_vote(2025, 42).build()
        hv_dir = tmp_path / "house_votes"
        assert hv_dir.is_dir()
        assert (hv_dir / "2025_0042.xml").is_file()

    def test_senate_vote_file_written(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.add_senate_vote(119, 1, 7).build()
        sv_dir = tmp_path / "senate_votes"
        assert sv_dir.is_dir()
        assert (sv_dir / "119_1_00007.xml").is_file()

    def test_multiple_house_votes(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.add_house_vote(2025, 1).add_house_vote(2025, 2).build()
        hv_dir = tmp_path / "house_votes"
        assert (hv_dir / "2025_0001.xml").is_file()
        assert (hv_dir / "2025_0002.xml").is_file()

    def test_multiple_senate_votes(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.add_senate_vote(119, 1, 10).add_senate_vote(119, 2, 5).build()
        sv_dir = tmp_path / "senate_votes"
        assert (sv_dir / "119_1_00010.xml").is_file()
        assert (sv_dir / "119_2_00005.xml").is_file()


# ---------------------------------------------------------------------------
# CongressArchiveBuilder — data overrides
# ---------------------------------------------------------------------------


class TestCongressArchiveBuilderOverrides:
    def test_with_members_replaces_default(self, tmp_path: Path) -> None:
        custom = [{"bioguideId": "X000001", "firstName": "Test", "lastName": "User"}]
        builder = CongressArchiveBuilder(tmp_path)
        builder.with_members(custom).build()
        data = json.loads((tmp_path / "members.json").read_text())
        assert len(data["members"]) == 1
        assert data["members"][0]["bioguideId"] == "X000001"

    def test_with_committees_replaces_default(self, tmp_path: Path) -> None:
        custom = [{"systemCode": "ssfi00", "name": "Finance", "chamber": "Senate"}]
        builder = CongressArchiveBuilder(tmp_path)
        builder.with_committees(custom).build()
        data = json.loads((tmp_path / "committees.json").read_text())
        assert data["committees"][0]["systemCode"] == "ssfi00"

    def test_with_bills_replaces_default(self, tmp_path: Path) -> None:
        custom = [{"congress": 119, "type": "S", "number": 99, "title": "Custom Bill"}]
        builder = CongressArchiveBuilder(tmp_path)
        builder.with_bills(custom).build()
        data = json.loads((tmp_path / "bills.json").read_text())
        assert data["bills"][0]["number"] == 99

    def test_with_member_detail_adds_entry(self, tmp_path: Path) -> None:
        detail = {"member": {"bioguideId": "S000148", "firstName": "Chuck"}}
        builder = CongressArchiveBuilder(tmp_path)
        builder.with_member_detail("S000148", detail).build()
        assert (tmp_path / "member_details" / "S000148.json").is_file()

    def test_with_bill_detail_adds_entry(self, tmp_path: Path) -> None:
        detail = {"bill": {"congress": 119, "type": "S", "number": 5, "title": "New Bill"}}
        builder = CongressArchiveBuilder(tmp_path)
        builder.with_bill_detail(119, "s", 5, detail).build()
        assert (tmp_path / "bill_details" / "119_s_5.json").is_file()

    def test_with_cosponsors_adds_entry(self, tmp_path: Path) -> None:
        payload = {"cosponsors": [{"bioguideId": "Z000001"}]}
        builder = CongressArchiveBuilder(tmp_path)
        builder.with_cosponsors(119, "s", 5, payload).build()
        assert (tmp_path / "cosponsors" / "119_s_5.json").is_file()

    def test_no_member_details_skips_dir(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.no_member_details().build()
        assert not (tmp_path / "member_details").exists()

    def test_no_bill_details_skips_dir(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.no_bill_details().build()
        assert not (tmp_path / "bill_details").exists()

    def test_no_cosponsors_skips_dir(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.no_cosponsors().build()
        assert not (tmp_path / "cosponsors").exists()


# ---------------------------------------------------------------------------
# CongressArchiveBuilder — archive() integration with loader
# ---------------------------------------------------------------------------


class TestCongressArchiveBuilderLoader:
    def test_archive_members_loadable(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path, congress=119)
        builder.build()
        archive = builder.archive()
        payload = load_members_payload(archive)
        assert "members" in payload
        assert len(payload["members"]) >= 1

    def test_archive_committees_loadable(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path, congress=119)
        builder.build()
        archive = builder.archive()
        payload = load_committees_payload(archive)
        assert "committees" in payload

    def test_archive_bills_loadable(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path, congress=119)
        builder.build()
        archive = builder.archive()
        payload = load_bills_payload(archive)
        assert "bills" in payload

    def test_member_detail_map_roundtrip(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path, congress=119)
        builder.build()
        archive = builder.archive()
        detail_map = load_member_detail_payload_map(archive)
        assert "P000197" in detail_map
        assert detail_map["P000197"]["bioguideId"] == "P000197"

    def test_bill_detail_map_roundtrip(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path, congress=119)
        builder.build()
        archive = builder.archive()
        bill_map = load_bill_detail_payload_map(archive)
        assert (119, "hr", 1) in bill_map

    def test_cosponsors_map_roundtrip(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path, congress=119)
        builder.build()
        archive = builder.archive()
        cs_map = load_cosponsors_payload_map(archive)
        assert (119, "hr", 1) in cs_map
        assert "cosponsors" in cs_map[(119, "hr", 1)]

    def test_house_vote_xml_is_valid_xml(self, tmp_path: Path) -> None:
        import xml.etree.ElementTree as ET

        builder = CongressArchiveBuilder(tmp_path, congress=119)
        builder.add_house_vote(2025, 42).build()
        xml_path = tmp_path / "house_votes" / "2025_0042.xml"
        root_el = ET.fromstring(xml_path.read_text())
        assert root_el.tag == "rollcall-vote"

    def test_senate_vote_xml_is_valid_xml(self, tmp_path: Path) -> None:
        import xml.etree.ElementTree as ET

        builder = CongressArchiveBuilder(tmp_path, congress=119)
        builder.add_senate_vote(119, 1, 3).build()
        xml_path = tmp_path / "senate_votes" / "119_1_00003.xml"
        root_el = ET.fromstring(xml_path.read_text())
        assert root_el.tag == "roll_call_vote"

    def test_archive_congress_attribute(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path, congress=118)
        builder.build()
        archive = builder.archive()
        assert archive.congress == 118

    def test_empty_member_detail_map_when_no_details(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.no_member_details().build()
        archive = builder.archive()
        detail_map = load_member_detail_payload_map(archive)
        assert detail_map == {}

    def test_empty_bill_detail_map_when_no_details(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.no_bill_details().build()
        archive = builder.archive()
        bill_map = load_bill_detail_payload_map(archive)
        assert bill_map == {}

    def test_empty_cosponsors_map_when_no_cosponsors(self, tmp_path: Path) -> None:
        builder = CongressArchiveBuilder(tmp_path)
        builder.no_cosponsors().build()
        archive = builder.archive()
        cs_map = load_cosponsors_payload_map(archive)
        assert cs_map == {}


# ---------------------------------------------------------------------------
# make_archive convenience wrapper
# ---------------------------------------------------------------------------


class TestMakeArchive:
    def test_returns_congress_archive(self, tmp_path: Path) -> None:
        archive = make_archive(tmp_path)
        assert isinstance(archive, CongressArchive)

    def test_default_congress_number(self, tmp_path: Path) -> None:
        archive = make_archive(tmp_path, congress=119)
        assert archive.congress == 119

    def test_mandatory_files_exist(self, tmp_path: Path) -> None:
        make_archive(tmp_path)
        assert (tmp_path / "members.json").is_file()
        assert (tmp_path / "committees.json").is_file()
        assert (tmp_path / "bills.json").is_file()

    def test_custom_members(self, tmp_path: Path) -> None:
        members = [{"bioguideId": "A000001", "firstName": "Alice", "lastName": "Anderson"}]
        archive = make_archive(tmp_path, members=members)
        payload = load_members_payload(archive)
        assert payload["members"][0]["bioguideId"] == "A000001"

    def test_custom_committees(self, tmp_path: Path) -> None:
        committees = [{"systemCode": "hjud00", "name": "Judiciary", "chamber": "House"}]
        archive = make_archive(tmp_path, committees=committees)
        payload = load_committees_payload(archive)
        assert payload["committees"][0]["systemCode"] == "hjud00"

    def test_custom_bills(self, tmp_path: Path) -> None:
        bills = [{"congress": 119, "type": "S", "number": 42, "title": "Senate Bill"}]
        archive = make_archive(tmp_path, bills=bills)
        payload = load_bills_payload(archive)
        assert payload["bills"][0]["number"] == 42

    def test_empty_member_details_override(self, tmp_path: Path) -> None:
        archive = make_archive(tmp_path, member_details={})
        assert not (tmp_path / "member_details").exists()
        detail_map = load_member_detail_payload_map(archive)
        assert detail_map == {}

    def test_empty_bill_details_override(self, tmp_path: Path) -> None:
        make_archive(tmp_path, bill_details={})
        assert not (tmp_path / "bill_details").exists()

    def test_empty_cosponsors_override(self, tmp_path: Path) -> None:
        make_archive(tmp_path, cosponsors={})
        assert not (tmp_path / "cosponsors").exists()

    def test_house_vote_keys_produce_files(self, tmp_path: Path) -> None:
        make_archive(tmp_path, house_vote_keys=[(2025, 1), (2025, 2)])
        assert (tmp_path / "house_votes" / "2025_0001.xml").is_file()
        assert (tmp_path / "house_votes" / "2025_0002.xml").is_file()

    def test_senate_vote_keys_produce_files(self, tmp_path: Path) -> None:
        make_archive(tmp_path, senate_vote_keys=[(119, 1, 10), (119, 2, 20)])
        assert (tmp_path / "senate_votes" / "119_1_00010.xml").is_file()
        assert (tmp_path / "senate_votes" / "119_2_00020.xml").is_file()

    def test_no_votes_by_default(self, tmp_path: Path) -> None:
        make_archive(tmp_path)
        assert not (tmp_path / "house_votes").exists()
        assert not (tmp_path / "senate_votes").exists()

    def test_custom_member_details(self, tmp_path: Path) -> None:
        detail = {"member": {"bioguideId": "B000001", "firstName": "Bob"}}
        archive = make_archive(tmp_path, member_details={"B000001": detail})
        detail_map = load_member_detail_payload_map(archive)
        assert "B000001" in detail_map

    def test_custom_cosponsors(self, tmp_path: Path) -> None:
        cs = {(119, "s", 99): {"cosponsors": [{"bioguideId": "C000001"}]}}
        archive = make_archive(tmp_path, cosponsors=cs)
        cs_map = load_cosponsors_payload_map(archive)
        assert (119, "s", 99) in cs_map
