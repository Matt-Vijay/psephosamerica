"""Tests for src/ingest/congress/archive_client.py.

All tests are fully offline: they write small JSON fixtures to a tmp_path
directory and exercise the real archive → normalize path.  No network calls,
no mocking of normalize helpers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingest.congress.archive import CongressArchive
from src.ingest.congress.archive_client import CongressArchiveClient
from src.ingest.congress.models import BillRecord, CommitteeRecord, CosponsorRecord, MemberRecord

# ---------------------------------------------------------------------------
# Shared raw fixture dicts (API-response shape)
# ---------------------------------------------------------------------------

_RAW_MEMBER = {
    "bioguideId": "A000001",
    "firstName": "Ada",
    "lastName": "Lovelace",
    "directOrderName": "Ada Lovelace",
    "partyName": "D",
    "state": "CA",
    "currentMember": True,
    "terms": {"item": [{"chamber": "House of Representatives", "startYear": "2025-01-03"}]},
}

_RAW_MEMBER_2 = {
    "bioguideId": "B000002",
    "firstName": "Bob",
    "lastName": "Smith",
    "directOrderName": "Bob Smith",
    "partyName": "R",
    "state": "TX",
    "currentMember": True,
    "terms": {"item": [{"chamber": "Senate", "startYear": "2023-01-03"}]},
}

_RAW_COMMITTEE_HOUSE = {
    "systemCode": "HJUD00",
    "name": "Committee on the Judiciary",
    "chamber": "House of Representatives",
    "committeeTypeCode": "standing",
}

_RAW_COMMITTEE_SENATE = {
    "systemCode": "SSJU00",
    "name": "Committee on the Judiciary",
    "chamber": "Senate",
    "committeeTypeCode": "standing",
}

_RAW_BILL = {
    "congress": 119,
    "type": "HR",
    "number": 42,
    "title": "A test bill",
    "introducedDate": "2025-01-15",
    "latestAction": {"actionDate": "2025-03-01", "text": "Referred to committee"},
}

_RAW_BILL_S = {
    "congress": 119,
    "type": "S",
    "number": 7,
    "title": "A Senate bill",
    "introducedDate": "2025-02-01",
    "latestAction": {"actionDate": "2025-03-15", "text": "Passed Senate"},
}

_RAW_COSPONSOR = {
    "bioguideId": "B000002",
    "isOriginalCosponsor": False,
    "sponsorshipDate": "2025-01-20",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_client(root: Path, congress: int = 119) -> CongressArchiveClient:
    return CongressArchiveClient(CongressArchive(root, congress))


# ---------------------------------------------------------------------------
# iter_members
# ---------------------------------------------------------------------------


class TestIterMembers:
    def test_yields_member_records(self, tmp_path: Path) -> None:
        _write(tmp_path / "members.json", {"members": [_RAW_MEMBER]})
        client = _make_client(tmp_path)
        result = list(client.iter_members())
        assert len(result) == 1
        assert isinstance(result[0], MemberRecord)
        assert result[0].bioguide_id == "A000001"

    def test_normalizes_chamber(self, tmp_path: Path) -> None:
        _write(tmp_path / "members.json", {"members": [_RAW_MEMBER]})
        result = list(_make_client(tmp_path).iter_members())
        assert result[0].chamber == "house"

    def test_accepts_congress_parameter(self, tmp_path: Path) -> None:
        """Congress parameter is accepted without error (archive is already scoped)."""
        _write(tmp_path / "members.json", {"members": [_RAW_MEMBER]})
        result = list(_make_client(tmp_path).iter_members(congress=119))
        assert len(result) == 1

    def test_returns_all_members(self, tmp_path: Path) -> None:
        _write(tmp_path / "members.json", {"members": [_RAW_MEMBER, _RAW_MEMBER_2]})
        result = list(_make_client(tmp_path).iter_members())
        assert len(result) == 2
        assert {r.bioguide_id for r in result} == {"A000001", "B000002"}

    def test_empty_members_file(self, tmp_path: Path) -> None:
        _write(tmp_path / "members.json", {"members": []})
        assert list(_make_client(tmp_path).iter_members()) == []

    def test_missing_members_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            list(_make_client(tmp_path).iter_members())


# ---------------------------------------------------------------------------
# iter_committees
# ---------------------------------------------------------------------------


class TestIterCommittees:
    def test_yields_committee_records(self, tmp_path: Path) -> None:
        _write(tmp_path / "committees.json", {"committees": [_RAW_COMMITTEE_HOUSE]})
        result = list(_make_client(tmp_path).iter_committees(119))
        assert len(result) == 1
        assert isinstance(result[0], CommitteeRecord)
        assert result[0].committee_code == "HJUD00"

    def test_congress_applied_to_normalize(self, tmp_path: Path) -> None:
        _write(tmp_path / "committees.json", {"committees": [_RAW_COMMITTEE_HOUSE]})
        result = list(_make_client(tmp_path).iter_committees(119))
        assert result[0].congress == 119

    def test_chamber_filter_house(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "committees.json",
            {"committees": [_RAW_COMMITTEE_HOUSE, _RAW_COMMITTEE_SENATE]},
        )
        result = list(_make_client(tmp_path).iter_committees(119, chamber="house"))
        assert len(result) == 1
        assert result[0].committee_code == "HJUD00"

    def test_chamber_filter_senate(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "committees.json",
            {"committees": [_RAW_COMMITTEE_HOUSE, _RAW_COMMITTEE_SENATE]},
        )
        result = list(_make_client(tmp_path).iter_committees(119, chamber="senate"))
        assert len(result) == 1
        assert result[0].committee_code == "SSJU00"

    def test_no_chamber_filter_yields_all(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "committees.json",
            {"committees": [_RAW_COMMITTEE_HOUSE, _RAW_COMMITTEE_SENATE]},
        )
        result = list(_make_client(tmp_path).iter_committees(119))
        assert len(result) == 2

    def test_empty_committees_file(self, tmp_path: Path) -> None:
        _write(tmp_path / "committees.json", {"committees": []})
        assert list(_make_client(tmp_path).iter_committees(119)) == []


# ---------------------------------------------------------------------------
# iter_bills
# ---------------------------------------------------------------------------


class TestIterBills:
    def test_yields_bill_records(self, tmp_path: Path) -> None:
        _write(tmp_path / "bills.json", {"bills": [_RAW_BILL]})
        result = list(_make_client(tmp_path).iter_bills(119))
        assert len(result) == 1
        assert isinstance(result[0], BillRecord)
        assert result[0].bill_number == 42

    def test_filters_by_congress(self, tmp_path: Path) -> None:
        other_bill = {**_RAW_BILL, "congress": 118}
        _write(tmp_path / "bills.json", {"bills": [_RAW_BILL, other_bill]})
        result = list(_make_client(tmp_path).iter_bills(119))
        assert len(result) == 1
        assert result[0].congress == 119

    def test_filters_by_bill_type(self, tmp_path: Path) -> None:
        _write(tmp_path / "bills.json", {"bills": [_RAW_BILL, _RAW_BILL_S]})
        result = list(_make_client(tmp_path).iter_bills(119, bill_type="hr"))
        assert len(result) == 1
        assert result[0].bill_type == "hr"

    def test_bill_type_filter_is_case_insensitive(self, tmp_path: Path) -> None:
        _write(tmp_path / "bills.json", {"bills": [_RAW_BILL]})
        result = list(_make_client(tmp_path).iter_bills(119, bill_type="HR"))
        assert len(result) == 1

    def test_no_bill_type_filter_yields_all_in_congress(self, tmp_path: Path) -> None:
        _write(tmp_path / "bills.json", {"bills": [_RAW_BILL, _RAW_BILL_S]})
        result = list(_make_client(tmp_path).iter_bills(119))
        assert len(result) == 2

    def test_normalizes_bill_type_to_lowercase(self, tmp_path: Path) -> None:
        _write(tmp_path / "bills.json", {"bills": [_RAW_BILL]})
        result = list(_make_client(tmp_path).iter_bills(119))
        assert result[0].bill_type == "hr"

    def test_empty_bills_file(self, tmp_path: Path) -> None:
        _write(tmp_path / "bills.json", {"bills": []})
        assert list(_make_client(tmp_path).iter_bills(119)) == []


# ---------------------------------------------------------------------------
# iter_cosponsors
# ---------------------------------------------------------------------------


class TestIterCosponsors:
    def test_yields_cosponsor_records(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "cosponsors" / "119_hr_42.json",
            {"cosponsors": [_RAW_COSPONSOR]},
        )
        result = list(_make_client(tmp_path).iter_cosponsors(119, "hr", 42))
        assert len(result) == 1
        assert isinstance(result[0], CosponsorRecord)
        assert result[0].bioguide_id == "B000002"

    def test_congress_bill_type_bill_number_on_record(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "cosponsors" / "119_hr_42.json",
            {"cosponsors": [_RAW_COSPONSOR]},
        )
        result = list(_make_client(tmp_path).iter_cosponsors(119, "hr", 42))
        r = result[0]
        assert r.congress == 119
        assert r.bill_type == "hr"
        assert r.bill_number == 42

    def test_missing_file_yields_nothing(self, tmp_path: Path) -> None:
        result = list(_make_client(tmp_path).iter_cosponsors(119, "hr", 42))
        assert result == []

    def test_empty_cosponsors_list(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "cosponsors" / "119_hr_42.json",
            {"cosponsors": []},
        )
        assert list(_make_client(tmp_path).iter_cosponsors(119, "hr", 42)) == []

    def test_multiple_cosponsors(self, tmp_path: Path) -> None:
        second = {**_RAW_COSPONSOR, "bioguideId": "C000003"}
        _write(
            tmp_path / "cosponsors" / "119_hr_42.json",
            {"cosponsors": [_RAW_COSPONSOR, second]},
        )
        result = list(_make_client(tmp_path).iter_cosponsors(119, "hr", 42))
        assert len(result) == 2
        assert [r.bioguide_id for r in result] == ["B000002", "C000003"]


# ---------------------------------------------------------------------------
# get_member_detail
# ---------------------------------------------------------------------------


class TestGetMemberDetail:
    def _write_detail(self, root: Path, raw: dict) -> None:
        bid = raw["bioguideId"]
        _write(root / "member_details" / f"{bid}.json", {"member": raw})

    def test_returns_member_record(self, tmp_path: Path) -> None:
        self._write_detail(tmp_path, _RAW_MEMBER)
        result = _make_client(tmp_path).get_member_detail("A000001")
        assert isinstance(result, MemberRecord)
        assert result.bioguide_id == "A000001"

    def test_normalizes_party_and_state(self, tmp_path: Path) -> None:
        self._write_detail(tmp_path, _RAW_MEMBER)
        result = _make_client(tmp_path).get_member_detail("A000001")
        assert result.party == "D"
        assert result.state == "CA"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _make_client(tmp_path).get_member_detail("Z999999")

    def test_different_members_resolve_independently(self, tmp_path: Path) -> None:
        self._write_detail(tmp_path, _RAW_MEMBER)
        self._write_detail(tmp_path, _RAW_MEMBER_2)
        r1 = _make_client(tmp_path).get_member_detail("A000001")
        r2 = _make_client(tmp_path).get_member_detail("B000002")
        assert r1.bioguide_id == "A000001"
        assert r2.bioguide_id == "B000002"


# ---------------------------------------------------------------------------
# get_bill_detail
# ---------------------------------------------------------------------------


class TestGetBillDetail:
    def _write_detail(self, root: Path, raw: dict) -> None:
        key = f"{raw['congress']}_{raw['type'].lower()}_{raw['number']}"
        _write(root / "bill_details" / f"{key}.json", {"bill": raw})

    def test_returns_bill_record(self, tmp_path: Path) -> None:
        self._write_detail(tmp_path, _RAW_BILL)
        result = _make_client(tmp_path).get_bill_detail(119, "hr", 42)
        assert isinstance(result, BillRecord)
        assert result.bill_number == 42

    def test_normalizes_bill_type_to_lowercase(self, tmp_path: Path) -> None:
        self._write_detail(tmp_path, _RAW_BILL)
        result = _make_client(tmp_path).get_bill_detail(119, "hr", 42)
        assert result.bill_type == "hr"

    def test_title_and_status_populated(self, tmp_path: Path) -> None:
        self._write_detail(tmp_path, _RAW_BILL)
        result = _make_client(tmp_path).get_bill_detail(119, "hr", 42)
        assert result.title == "A test bill"
        assert result.current_status == "Referred to committee"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _make_client(tmp_path).get_bill_detail(119, "hr", 999)

    def test_different_bills_resolve_independently(self, tmp_path: Path) -> None:
        self._write_detail(tmp_path, _RAW_BILL)
        self._write_detail(tmp_path, _RAW_BILL_S)
        r_hr = _make_client(tmp_path).get_bill_detail(119, "hr", 42)
        r_s = _make_client(tmp_path).get_bill_detail(119, "s", 7)
        assert r_hr.bill_type == "hr"
        assert r_s.bill_type == "s"
