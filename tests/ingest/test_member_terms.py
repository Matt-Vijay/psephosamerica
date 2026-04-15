"""Tests for src/ingest/congress/member_terms.py.

Pure / deterministic — no network calls, no DB.
"""

from __future__ import annotations

import datetime

import pytest

from src.ingest.congress.member_terms import member_term_specs_from_detail
from src.ingest.congress.models import MemberRecord
from src.load.congress import MemberTermSpec as LoadMemberTermSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def rep() -> MemberRecord:
    return MemberRecord(
        bioguide_id="A000001",
        first_name="Ada",
        last_name="Lovelace",
        full_name="Ada Lovelace",
        chamber="house",
        state="CA",
    )


@pytest.fixture()
def sen() -> MemberRecord:
    return MemberRecord(
        bioguide_id="B000002",
        first_name="Bob",
        last_name="Smith",
        full_name="Bob Smith",
        chamber="senate",
        state="TX",
    )


# ---------------------------------------------------------------------------
# Single term — House
# ---------------------------------------------------------------------------

class TestHouseTerm:
    def test_basic_fields(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 119, "chamber": "House of Representatives",
                 "startYear": "2025", "endYear": None, "district": 14}
            ]}
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert len(specs) == 1
        s = specs[0]
        assert isinstance(s, LoadMemberTermSpec)
        assert s.record.bioguide_id == "A000001"
        assert s.congress == 119
        assert s.chamber == "house"
        assert s.state == "CA"
        assert s.district == 14
        assert s.start_date == datetime.date(2025, 1, 3)
        assert s.end_date is None
        assert s.is_current is True

    def test_closed_term_not_current(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 118, "chamber": "House of Representatives",
                 "startYear": "2023", "endYear": "2025", "district": 5}
            ]}
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].is_current is False
        assert specs[0].end_date == datetime.date(2025, 1, 3)

    def test_iso_date_start_year(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 119, "chamber": "House of Representatives",
                 "startYear": "2025-01-03", "endYear": None, "district": 7}
            ]}
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].start_date == datetime.date(2025, 1, 3)

    def test_iso_date_end_year(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 118, "chamber": "House of Representatives",
                 "startYear": "2023-01-03", "endYear": "2025-01-03", "district": 7}
            ]}
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].end_date == datetime.date(2025, 1, 3)
        assert specs[0].is_current is False


# ---------------------------------------------------------------------------
# Single term — Senate
# ---------------------------------------------------------------------------

class TestSenateTerm:
    def test_district_is_always_none(self, sen: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 119, "chamber": "Senate",
                 "startYear": "2023", "endYear": None, "district": 1}
            ]}
        }
        specs = member_term_specs_from_detail(detail, sen)
        assert specs[0].district is None

    def test_district_none_when_absent(self, sen: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 119, "chamber": "Senate",
                 "startYear": "2023", "endYear": None}
            ]}
        }
        specs = member_term_specs_from_detail(detail, sen)
        assert specs[0].district is None

    def test_chamber_field(self, sen: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 119, "chamber": "Senate", "startYear": "2023", "endYear": None}
            ]}
        }
        specs = member_term_specs_from_detail(detail, sen)
        assert specs[0].chamber == "senate"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

class TestOrdering:
    def test_sorted_ascending_by_start_date(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 119, "chamber": "House of Representatives",
                 "startYear": "2025", "endYear": None, "district": 5},
                {"congress": 117, "chamber": "House of Representatives",
                 "startYear": "2021", "endYear": "2023", "district": 5},
                {"congress": 118, "chamber": "House of Representatives",
                 "startYear": "2023", "endYear": "2025", "district": 5},
            ]}
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert [s.congress for s in specs] == [117, 118, 119]
        assert specs[0].start_date < specs[1].start_date < specs[2].start_date


# ---------------------------------------------------------------------------
# Empty / missing data
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_detail_returns_empty_list(self, rep: MemberRecord) -> None:
        assert member_term_specs_from_detail({}, rep) == []

    def test_empty_terms_item_returns_empty_list(self, sen: MemberRecord) -> None:
        detail = {"terms": {"item": []}}
        assert member_term_specs_from_detail(detail, sen) == []

    def test_missing_congress_skips_term(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"chamber": "House of Representatives", "startYear": "2025",
                 "endYear": None, "district": 5}
            ]}
        }
        assert member_term_specs_from_detail(detail, rep) == []

    def test_null_start_year_skips_term(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 119, "chamber": "House of Representatives",
                 "startYear": None, "endYear": None, "district": 5}
            ]}
        }
        assert member_term_specs_from_detail(detail, rep) == []

    def test_empty_string_start_year_skips_term(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 119, "chamber": "House of Representatives",
                 "startYear": "", "endYear": None, "district": 5}
            ]}
        }
        assert member_term_specs_from_detail(detail, rep) == []


# ---------------------------------------------------------------------------
# State resolution
# ---------------------------------------------------------------------------

class TestStateResolution:
    def test_state_from_item_stateCode(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 119, "chamber": "House of Representatives",
                 "startYear": "2025", "endYear": None, "district": 5, "stateCode": "NY"}
            ]}
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].state == "NY"

    def test_state_falls_back_to_member(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 119, "chamber": "House of Representatives",
                 "startYear": "2025", "endYear": None, "district": 5}
            ]}
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].state == "CA"


# ---------------------------------------------------------------------------
# Chamber fallback
# ---------------------------------------------------------------------------

class TestChamberFallback:
    def test_empty_chamber_string_falls_back_to_member(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {"item": [
                {"congress": 119, "chamber": "",
                 "startYear": "2025", "endYear": None, "district": 5}
            ]}
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].chamber == "house"
