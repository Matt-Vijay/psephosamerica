"""Tests for src/ingest/congress/archive_loader.py.

All I/O is local — no network, no mocks of the loader itself.
Each test writes real JSON files to a tmp_path fixture directory
so that archive_loader.py exercises its actual file reading code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingest.congress.archive import CongressArchive, parse_bill_stem
from src.ingest.congress.archive_loader import (
    load_bills_payload,
    load_bill_detail_payload_map,
    load_committees_payload,
    load_cosponsors_payload_map,
    load_member_detail_payload_map,
    load_members_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_archive(root: Path) -> CongressArchive:
    return CongressArchive(root)


# ---------------------------------------------------------------------------
# parse_bill_stem (utility used by the loader internals)
# ---------------------------------------------------------------------------


class TestParseBillStem:
    def test_standard_stem(self):
        assert parse_bill_stem("119_hr_1") == (119, "hr", 1)

    def test_senate_bill(self):
        assert parse_bill_stem("118_s_50") == (118, "s", 50)

    def test_joint_resolution(self):
        assert parse_bill_stem("117_hjres_42") == (117, "hjres", 42)

    def test_invalid_stem_raises(self):
        with pytest.raises(ValueError):
            parse_bill_stem("nounderscores")


# ---------------------------------------------------------------------------
# load_members_payload
# ---------------------------------------------------------------------------


class TestLoadMembersPayload:
    def test_returns_members_list_payload(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        payload = {"members": [{"bioguideId": "A000001"}]}
        _write(archive.members_path(), payload)
        result = load_members_payload(archive)
        assert result == payload

    def test_preserves_all_fields(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        payload = {
            "members": [{"bioguideId": "A000001", "firstName": "Ada"}],
            "pagination": {"count": 1},
        }
        _write(archive.members_path(), payload)
        assert load_members_payload(archive)["pagination"]["count"] == 1

    def test_raises_if_file_missing(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        with pytest.raises(FileNotFoundError):
            load_members_payload(archive)

    def test_empty_members_list(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        _write(archive.members_path(), {"members": []})
        assert load_members_payload(archive) == {"members": []}


# ---------------------------------------------------------------------------
# load_committees_payload
# ---------------------------------------------------------------------------


class TestLoadCommitteesPayload:
    def test_returns_committees_payload(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        payload = {"committees": [{"systemCode": "HJUD00"}]}
        _write(archive.committees_path(), payload)
        assert load_committees_payload(archive) == payload

    def test_raises_if_file_missing(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        with pytest.raises(FileNotFoundError):
            load_committees_payload(archive)


# ---------------------------------------------------------------------------
# load_bills_payload
# ---------------------------------------------------------------------------


class TestLoadBillsPayload:
    def test_returns_bills_payload(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        payload = {"bills": [{"congress": 119, "type": "HR", "number": 1}]}
        _write(archive.bills_path(), payload)
        assert load_bills_payload(archive) == payload

    def test_raises_if_file_missing(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        with pytest.raises(FileNotFoundError):
            load_bills_payload(archive)


# ---------------------------------------------------------------------------
# load_member_detail_payload_map
# ---------------------------------------------------------------------------


class TestLoadMemberDetailPayloadMap:
    def test_returns_empty_map_when_dir_missing(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        assert load_member_detail_payload_map(archive) == {}

    def test_loads_single_member_detail(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        inner = {"bioguideId": "A000001", "firstName": "Ada"}
        _write(archive.member_detail_path("A000001"), {"member": inner})
        result = load_member_detail_payload_map(archive)
        assert "A000001" in result
        assert result["A000001"] == inner

    def test_extracts_inner_member_dict(self, tmp_path: Path):
        """Stored as {"member": {...}}; loader returns the inner dict."""
        archive = _make_archive(tmp_path)
        inner = {"bioguideId": "B000002", "lastName": "Smith"}
        _write(archive.member_detail_path("B000002"), {"member": inner})
        result = load_member_detail_payload_map(archive)
        assert result["B000002"] is not None
        assert "bioguideId" in result["B000002"]

    def test_fallback_when_no_member_key(self, tmp_path: Path):
        """If payload has no 'member' key, the whole dict is used."""
        archive = _make_archive(tmp_path)
        payload = {"bioguideId": "C000003"}
        _write(archive.member_detail_path("C000003"), payload)
        result = load_member_detail_payload_map(archive)
        assert result["C000003"] == payload

    def test_loads_multiple_members(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        for bio in ("A000001", "B000002", "C000003"):
            _write(archive.member_detail_path(bio), {"member": {"bioguideId": bio}})
        result = load_member_detail_payload_map(archive)
        assert set(result.keys()) == {"A000001", "B000002", "C000003"}

    def test_key_is_bioguide_id_string(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        _write(archive.member_detail_path("P000197"), {"member": {"bioguideId": "P000197"}})
        result = load_member_detail_payload_map(archive)
        assert isinstance(list(result.keys())[0], str)


# ---------------------------------------------------------------------------
# load_bill_detail_payload_map
# ---------------------------------------------------------------------------


class TestLoadBillDetailPayloadMap:
    def test_returns_empty_map_when_dir_missing(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        assert load_bill_detail_payload_map(archive) == {}

    def test_loads_single_bill_detail(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        inner = {"congress": 119, "type": "HR", "number": 1}
        _write(archive.bill_detail_path(119, "hr", 1), {"bill": inner})
        result = load_bill_detail_payload_map(archive)
        assert (119, "hr", 1) in result
        assert result[(119, "hr", 1)] == inner

    def test_extracts_inner_bill_dict(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        inner = {"congress": 118, "type": "S", "number": 50, "title": "A bill"}
        _write(archive.bill_detail_path(118, "s", 50), {"bill": inner})
        result = load_bill_detail_payload_map(archive)
        assert result[(118, "s", 50)]["title"] == "A bill"

    def test_fallback_when_no_bill_key(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        payload = {"congress": 119, "type": "HR", "number": 99}
        _write(archive.bill_detail_path(119, "hr", 99), payload)
        result = load_bill_detail_payload_map(archive)
        assert result[(119, "hr", 99)] == payload

    def test_loads_multiple_bills(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        bills = [(119, "hr", 1), (119, "s", 10), (118, "hjres", 42)]
        for congress, bill_type, bill_number in bills:
            _write(
                archive.bill_detail_path(congress, bill_type, bill_number),
                {"bill": {"congress": congress, "type": bill_type, "number": bill_number}},
            )
        result = load_bill_detail_payload_map(archive)
        assert set(result.keys()) == set(bills)

    def test_skips_files_with_unparseable_stems(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        detail_dir = archive.bill_details_dir()
        detail_dir.mkdir(parents=True, exist_ok=True)
        (detail_dir / "bad_stem.json").write_text(json.dumps({"bill": {}}), encoding="utf-8")
        (detail_dir / "119_hr_1.json").write_text(
            json.dumps({"bill": {"congress": 119}}), encoding="utf-8"
        )
        result = load_bill_detail_payload_map(archive)
        assert (119, "hr", 1) in result
        assert len(result) == 1

    def test_key_tuple_types(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        _write(archive.bill_detail_path(119, "hr", 1), {"bill": {}})
        result = load_bill_detail_payload_map(archive)
        key = next(iter(result.keys()))
        congress, bill_type, bill_number = key
        assert isinstance(congress, int)
        assert isinstance(bill_type, str)
        assert isinstance(bill_number, int)


# ---------------------------------------------------------------------------
# load_cosponsors_payload_map
# ---------------------------------------------------------------------------


class TestLoadCosponsorsPayloadMap:
    def test_returns_empty_map_when_dir_missing(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        assert load_cosponsors_payload_map(archive) == {}

    def test_loads_single_cosponsors_file(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        payload = {"cosponsors": [{"bioguideId": "B000002"}]}
        _write(archive.cosponsors_path(119, "hr", 1), payload)
        result = load_cosponsors_payload_map(archive)
        assert (119, "hr", 1) in result
        assert result[(119, "hr", 1)] == payload

    def test_cosponsors_list_is_accessible(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        payload = {"cosponsors": [{"bioguideId": "X000001"}, {"bioguideId": "X000002"}]}
        _write(archive.cosponsors_path(119, "s", 5), payload)
        result = load_cosponsors_payload_map(archive)
        assert len(result[(119, "s", 5)]["cosponsors"]) == 2

    def test_empty_cosponsors_list(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        _write(archive.cosponsors_path(119, "hr", 1), {"cosponsors": []})
        result = load_cosponsors_payload_map(archive)
        assert result[(119, "hr", 1)]["cosponsors"] == []

    def test_loads_multiple_bills_cosponsors(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        keys = [(119, "hr", 1), (119, "s", 10)]
        for congress, bill_type, bill_number in keys:
            _write(
                archive.cosponsors_path(congress, bill_type, bill_number),
                {"cosponsors": []},
            )
        result = load_cosponsors_payload_map(archive)
        assert set(result.keys()) == set(keys)

    def test_skips_files_with_unparseable_stems(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        cosponsor_dir = archive.cosponsors_dir()
        cosponsor_dir.mkdir(parents=True, exist_ok=True)
        (cosponsor_dir / "junk.json").write_text(json.dumps({"cosponsors": []}), encoding="utf-8")
        (cosponsor_dir / "119_hr_1.json").write_text(
            json.dumps({"cosponsors": []}), encoding="utf-8"
        )
        result = load_cosponsors_payload_map(archive)
        assert len(result) == 1
        assert (119, "hr", 1) in result

    def test_key_tuple_types(self, tmp_path: Path):
        archive = _make_archive(tmp_path)
        _write(archive.cosponsors_path(118, "s", 50), {"cosponsors": []})
        result = load_cosponsors_payload_map(archive)
        key = next(iter(result.keys()))
        congress, bill_type, bill_number = key
        assert isinstance(congress, int)
        assert isinstance(bill_type, str)
        assert isinstance(bill_number, int)
