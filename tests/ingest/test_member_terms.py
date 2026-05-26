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
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "House of Representatives",
                        "startYear": "2025",
                        "endYear": None,
                        "district": 14,
                    }
                ]
            }
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
            "terms": {
                "item": [
                    {
                        "congress": 118,
                        "chamber": "House of Representatives",
                        "startYear": "2023",
                        "endYear": "2025",
                        "district": 5,
                    }
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].is_current is False
        assert specs[0].end_date == datetime.date(2025, 1, 3)

    def test_iso_date_start_year(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "House of Representatives",
                        "startYear": "2025-01-03",
                        "endYear": None,
                        "district": 7,
                    }
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].start_date == datetime.date(2025, 1, 3)

    def test_iso_date_end_year(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 118,
                        "chamber": "House of Representatives",
                        "startYear": "2023-01-03",
                        "endYear": "2025-01-03",
                        "district": 7,
                    }
                ]
            }
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
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "Senate",
                        "startYear": "2023",
                        "endYear": None,
                        "district": 1,
                    }
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, sen)
        assert specs[0].district is None

    def test_district_none_when_absent(self, sen: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {"congress": 119, "chamber": "Senate", "startYear": "2023", "endYear": None}
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, sen)
        assert specs[0].district is None

    def test_chamber_field(self, sen: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {"congress": 119, "chamber": "Senate", "startYear": "2023", "endYear": None}
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, sen)
        assert specs[0].chamber == "senate"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_sorted_ascending_by_start_date(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "House of Representatives",
                        "startYear": "2025",
                        "endYear": None,
                        "district": 5,
                    },
                    {
                        "congress": 117,
                        "chamber": "House of Representatives",
                        "startYear": "2021",
                        "endYear": "2023",
                        "district": 5,
                    },
                    {
                        "congress": 118,
                        "chamber": "House of Representatives",
                        "startYear": "2023",
                        "endYear": "2025",
                        "district": 5,
                    },
                ]
            }
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
            "terms": {
                "item": [
                    {
                        "chamber": "House of Representatives",
                        "startYear": "2025",
                        "endYear": None,
                        "district": 5,
                    }
                ]
            }
        }
        assert member_term_specs_from_detail(detail, rep) == []

    def test_null_start_year_skips_term(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "House of Representatives",
                        "startYear": None,
                        "endYear": None,
                        "district": 5,
                    }
                ]
            }
        }
        assert member_term_specs_from_detail(detail, rep) == []

    def test_empty_string_start_year_skips_term(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "House of Representatives",
                        "startYear": "",
                        "endYear": None,
                        "district": 5,
                    }
                ]
            }
        }
        assert member_term_specs_from_detail(detail, rep) == []


# ---------------------------------------------------------------------------
# State resolution
# ---------------------------------------------------------------------------


class TestStateResolution:
    def test_state_from_item_stateCode(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "House of Representatives",
                        "startYear": "2025",
                        "endYear": None,
                        "district": 5,
                        "stateCode": "NY",
                    }
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].state == "NY"

    def test_state_falls_back_to_member(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "House of Representatives",
                        "startYear": "2025",
                        "endYear": None,
                        "district": 5,
                    }
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].state == "CA"


# ---------------------------------------------------------------------------
# Chamber fallback
# ---------------------------------------------------------------------------


class TestChamberFallback:
    def test_empty_chamber_string_falls_back_to_member(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "",
                        "startYear": "2025",
                        "endYear": None,
                        "district": 5,
                    }
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].chamber == "house"


# ---------------------------------------------------------------------------
# Type-safety: non-dict terms structure
# ---------------------------------------------------------------------------


class TestTermsTypeRobustness:
    """Congress.gov structure is stable but callers may pass partial payloads."""

    def test_terms_as_list_returns_empty(self, rep: MemberRecord) -> None:
        # If the API ever returns terms as a bare list instead of {"item": [...]}
        detail = {
            "terms": [
                {
                    "congress": 119,
                    "chamber": "House of Representatives",
                    "startYear": "2025",
                    "endYear": None,
                    "district": 5,
                }
            ]
        }
        assert member_term_specs_from_detail(detail, rep) == []

    def test_terms_as_none_returns_empty(self, rep: MemberRecord) -> None:
        assert member_term_specs_from_detail({"terms": None}, rep) == []

    def test_terms_as_string_returns_empty(self, rep: MemberRecord) -> None:
        assert member_term_specs_from_detail({"terms": "unexpected"}, rep) == []

    def test_startYear_as_integer_accepted(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "House of Representatives",
                        "startYear": 2025,
                        "endYear": None,
                        "district": 3,
                    }
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert len(specs) == 1
        assert specs[0].start_date == datetime.date(2025, 1, 3)

    def test_congress_as_string_int_accepted(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": "119",
                        "chamber": "House of Representatives",
                        "startYear": "2025",
                        "endYear": None,
                        "district": 5,
                    }
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert len(specs) == 1
        assert specs[0].congress == 119

    def test_boolean_congress_rejected(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": True,
                        "chamber": "House of Representatives",
                        "startYear": "2025",
                        "endYear": None,
                        "district": 5,
                    }
                ]
            }
        }

        with pytest.raises(ValueError, match="congress must be an integer"):
            member_term_specs_from_detail(detail, rep)

    def test_boolean_house_district_rejected(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "House of Representatives",
                        "startYear": "2025",
                        "endYear": None,
                        "district": False,
                    }
                ]
            }
        }

        with pytest.raises(ValueError, match="district must be an integer"):
            member_term_specs_from_detail(detail, rep)

    def test_mixed_valid_and_missing_congress_keeps_valid(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "House of Representatives",
                        "startYear": "2025",
                        "endYear": None,
                        "district": 5,
                    },
                    {
                        "chamber": "House of Representatives",
                        "startYear": "2023",
                        "endYear": "2025",
                        "district": 5,
                    },
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert len(specs) == 1
        assert specs[0].congress == 119


# ---------------------------------------------------------------------------
# Realistic full-detail payloads — Congress.gov API shape
# ---------------------------------------------------------------------------

# Realistic Congress.gov member detail inner object (senator, 3 terms).
_WARREN_DETAIL: dict = {
    "bioguideId": "W000817",
    "directOrderName": "Warren, Elizabeth",
    "firstName": "Elizabeth",
    "lastName": "Warren",
    "party": "Democrat",
    "stateCode": "MA",
    "stateName": "Massachusetts",
    "terms": {
        "item": [
            {
                "chamber": "Senate",
                "congress": 113,
                "endYear": "2019",
                "memberType": "Senator",
                "startYear": "2013",
                "stateCode": "MA",
                "stateName": "Massachusetts",
            },
            {
                "chamber": "Senate",
                "congress": 116,
                "endYear": "2025",
                "memberType": "Senator",
                "startYear": "2019",
                "stateCode": "MA",
                "stateName": "Massachusetts",
            },
            {
                "chamber": "Senate",
                "congress": 119,
                "endYear": None,
                "memberType": "Senator",
                "startYear": "2025",
                "stateCode": "MA",
                "stateName": "Massachusetts",
            },
        ]
    },
    "committees": {"item": []},
}

_WARREN = MemberRecord(
    bioguide_id="W000817",
    first_name="Elizabeth",
    last_name="Warren",
    full_name="Elizabeth Warren",
    chamber="senate",
    state="MA",
    is_current=True,
)


class TestRealisticSenatorPayload:
    def test_three_senate_terms_parsed(self) -> None:
        specs = member_term_specs_from_detail(_WARREN_DETAIL, _WARREN)
        assert len(specs) == 3

    def test_sorted_chronologically(self) -> None:
        specs = member_term_specs_from_detail(_WARREN_DETAIL, _WARREN)
        assert [s.congress for s in specs] == [113, 116, 119]

    def test_only_last_term_is_current(self) -> None:
        specs = member_term_specs_from_detail(_WARREN_DETAIL, _WARREN)
        assert specs[0].is_current is False
        assert specs[1].is_current is False
        assert specs[2].is_current is True

    def test_closed_terms_have_end_date(self) -> None:
        specs = member_term_specs_from_detail(_WARREN_DETAIL, _WARREN)
        assert specs[0].end_date == datetime.date(2019, 1, 3)
        assert specs[1].end_date == datetime.date(2025, 1, 3)

    def test_all_senate_terms_have_no_district(self) -> None:
        specs = member_term_specs_from_detail(_WARREN_DETAIL, _WARREN)
        assert all(s.district is None for s in specs)

    def test_state_code_from_term_item(self) -> None:
        specs = member_term_specs_from_detail(_WARREN_DETAIL, _WARREN)
        assert all(s.state == "MA" for s in specs)


# Realistic crossover: served in House then elected to Senate.
_CROSSOVER_DETAIL: dict = {
    "bioguideId": "C999999",
    "firstName": "Chris",
    "lastName": "Cross",
    "terms": {
        "item": [
            {
                "chamber": "House of Representatives",
                "congress": 116,
                "district": 7,
                "endYear": "2021",
                "startYear": "2019",
                "stateCode": "OH",
            },
            {
                "chamber": "Senate",
                "congress": 117,
                "endYear": None,
                "startYear": "2021",
                "stateCode": "OH",
            },
        ]
    },
}

_CROSSOVER = MemberRecord(
    bioguide_id="C999999",
    first_name="Chris",
    last_name="Cross",
    full_name="Chris Cross",
    chamber="senate",
    state="OH",
    is_current=True,
)


class TestCrossoverMember:
    """Member who served in House then Senate — both chambers appear in terms list."""

    def test_two_terms_different_chambers(self) -> None:
        specs = member_term_specs_from_detail(_CROSSOVER_DETAIL, _CROSSOVER)
        assert len(specs) == 2
        chambers = [s.chamber for s in specs]
        assert "house" in chambers
        assert "senate" in chambers

    def test_house_term_has_district(self) -> None:
        specs = member_term_specs_from_detail(_CROSSOVER_DETAIL, _CROSSOVER)
        house_term = next(s for s in specs if s.chamber == "house")
        assert house_term.district == 7

    def test_senate_term_no_district(self) -> None:
        specs = member_term_specs_from_detail(_CROSSOVER_DETAIL, _CROSSOVER)
        senate_term = next(s for s in specs if s.chamber == "senate")
        assert senate_term.district is None

    def test_house_term_is_not_current(self) -> None:
        specs = member_term_specs_from_detail(_CROSSOVER_DETAIL, _CROSSOVER)
        house_term = next(s for s in specs if s.chamber == "house")
        assert house_term.is_current is False

    def test_senate_term_is_current(self) -> None:
        specs = member_term_specs_from_detail(_CROSSOVER_DETAIL, _CROSSOVER)
        senate_term = next(s for s in specs if s.chamber == "senate")
        assert senate_term.is_current is True


class TestAtLargeDistrict:
    """At-large representatives have district=0 in Congress.gov data."""

    def test_district_zero_preserved(self, rep: MemberRecord) -> None:
        detail = {
            "terms": {
                "item": [
                    {
                        "congress": 119,
                        "chamber": "House of Representatives",
                        "startYear": "2025",
                        "endYear": None,
                        "district": 0,
                    }
                ]
            }
        }
        specs = member_term_specs_from_detail(detail, rep)
        assert specs[0].district == 0
