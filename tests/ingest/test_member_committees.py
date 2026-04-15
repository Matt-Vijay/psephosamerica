"""Tests for src/ingest/congress/member_committees.py.

Pure / deterministic — no network calls, no DB.
"""

from __future__ import annotations

import datetime

import pytest

from src.ingest.congress.member_committees import committee_membership_specs_from_detail
from src.ingest.congress.models import MemberRecord
from src.load.congress import CommitteeMembershipSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def member() -> MemberRecord:
    return MemberRecord(
        bioguide_id="P000197",
        first_name="Nancy",
        last_name="Pelosi",
        full_name="Nancy Pelosi",
        chamber="house",
        party="Democrat",
        state="CA",
        is_current=True,
    )


def _committee_item(
    system_code: str = "HSAS00",
    name: str = "Committee on Armed Services",
    role: str = "Member",
    congress: int = 118,
    start_date: str = "2023-01-10",
    end_date: str | None = None,
    is_current: bool = True,
    url: str = "https://api.congress.gov/v3/committee/house/HSAS00",
) -> dict:
    return {
        "committee": {"name": name, "systemCode": system_code, "url": url},
        "role": role,
        "congress": congress,
        "startDate": start_date,
        "endDate": end_date,
        "isCurrent": is_current,
    }


def _detail(*items) -> dict:
    return {"committees": {"item": list(items)}}


# ---------------------------------------------------------------------------
# Happy-path: basic field mapping
# ---------------------------------------------------------------------------


class TestBasicFieldMapping:
    def test_returns_list_of_specs(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(_detail(_committee_item()), member)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], CommitteeMembershipSpec)

    def test_bioguide_id_comes_from_member(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(_detail(_committee_item()), member)
        assert result[0].bioguide_id == "P000197"

    def test_committee_code_extracted(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(system_code="HSJU00")), member
        )
        assert result[0].committee_code == "HSJU00"

    def test_congress_extracted(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(congress=119)), member
        )
        assert result[0].congress == 119

    def test_start_date_parsed(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(start_date="2023-01-10")), member
        )
        assert result[0].start_date == datetime.date(2023, 1, 10)

    def test_end_date_parsed(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(end_date="2024-12-31")), member
        )
        assert result[0].end_date == datetime.date(2024, 12, 31)

    def test_end_date_none_when_absent(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(end_date=None)), member
        )
        assert result[0].end_date is None

    def test_is_current_true(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(is_current=True)), member
        )
        assert result[0].is_current is True

    def test_is_current_false(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(is_current=False)), member
        )
        assert result[0].is_current is False

    def test_source_url_from_committee_url(self, member: MemberRecord) -> None:
        url = "https://api.congress.gov/v3/committee/house/HSAS00"
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(url=url)), member
        )
        assert result[0].source_url == url

    def test_empty_payload_returns_empty_list(self, member: MemberRecord) -> None:
        assert committee_membership_specs_from_detail({}, member) == []

    def test_no_committee_items_returns_empty_list(self, member: MemberRecord) -> None:
        assert committee_membership_specs_from_detail({"committees": {"item": []}}, member) == []

    def test_multiple_items(self, member: MemberRecord) -> None:
        detail = _detail(
            _committee_item(system_code="HSAS00"),
            _committee_item(system_code="HSJU00"),
        )
        result = committee_membership_specs_from_detail(detail, member)
        assert len(result) == 2
        codes = {s.committee_code for s in result}
        assert codes == {"HSAS00", "HSJU00"}


# ---------------------------------------------------------------------------
# Role normalization
# ---------------------------------------------------------------------------


class TestRoleNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("Member", "member"),
        ("member", "member"),
        ("MEMBER", "member"),
        ("Chair", "chair"),
        ("Chairman", "chair"),
        ("Chairwoman", "chair"),
        ("CHAIR", "chair"),
        ("Vice Chair", "vice_chair"),
        ("Vice Chairman", "vice_chair"),
        ("Vice Chairwoman", "vice_chair"),
        ("Ranking Member", "ranking_member"),
        ("Ranking Minority Member", "ranking_member"),
        ("Ex Officio", "ex_officio"),
    ])
    def test_known_roles(self, raw: str, expected: str, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(role=raw)), member
        )
        assert result[0].role == expected

    def test_unknown_role_falls_back_to_member(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(role="Some Exotic Role")), member
        )
        assert result[0].role == "member"

    def test_none_role_falls_back_to_member(self, member: MemberRecord) -> None:
        item = _committee_item()
        item["role"] = None
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result[0].role == "member"

    def test_missing_role_key_falls_back_to_member(self, member: MemberRecord) -> None:
        item = _committee_item()
        del item["role"]
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result[0].role == "member"


# ---------------------------------------------------------------------------
# Rows dropped when committee code is unresolvable
# ---------------------------------------------------------------------------


class TestDropsUnresolvableRows:
    def test_drops_item_with_no_system_code(self, member: MemberRecord) -> None:
        item = _committee_item()
        item["committee"] = {"name": "Mystery Committee"}   # no systemCode
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result == []

    def test_drops_item_with_empty_system_code(self, member: MemberRecord) -> None:
        item = _committee_item()
        item["committee"]["systemCode"] = ""
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result == []

    def test_drops_item_with_null_system_code(self, member: MemberRecord) -> None:
        item = _committee_item()
        item["committee"]["systemCode"] = None
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result == []

    def test_drops_item_with_missing_committee_field(self, member: MemberRecord) -> None:
        item = _committee_item()
        del item["committee"]
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result == []

    def test_drops_item_with_missing_congress(self, member: MemberRecord) -> None:
        item = _committee_item()
        del item["congress"]
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result == []

    def test_drops_item_with_nonnumeric_congress(self, member: MemberRecord) -> None:
        item = _committee_item()
        item["congress"] = "one-eighteen"
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result == []

    def test_drops_item_with_missing_start_date(self, member: MemberRecord) -> None:
        item = _committee_item()
        del item["startDate"]
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result == []

    def test_drops_item_with_null_start_date(self, member: MemberRecord) -> None:
        item = _committee_item()
        item["startDate"] = None
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result == []

    def test_drops_item_with_unparseable_start_date(self, member: MemberRecord) -> None:
        item = _committee_item()
        item["startDate"] = "not-a-date"
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result == []

    def test_valid_item_kept_alongside_invalid(self, member: MemberRecord) -> None:
        good = _committee_item(system_code="HSAS00")
        bad = _committee_item()
        bad["committee"]["systemCode"] = ""
        result = committee_membership_specs_from_detail(_detail(good, bad), member)
        assert len(result) == 1
        assert result[0].committee_code == "HSAS00"


# ---------------------------------------------------------------------------
# Date parsing edge cases
# ---------------------------------------------------------------------------


class TestDateParsing:
    def test_date_with_time_component_uses_date_only(self, member: MemberRecord) -> None:
        item = _committee_item(start_date="2023-01-10T00:00:00")
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result[0].start_date == datetime.date(2023, 1, 10)

    def test_congress_as_string_int_accepted(self, member: MemberRecord) -> None:
        item = _committee_item()
        item["congress"] = "118"
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result[0].congress == 118


# ---------------------------------------------------------------------------
# isCurrent boolean coercion
# ---------------------------------------------------------------------------


class TestIsCurrentCoercion:
    """Congress.gov returns a JSON boolean; some intermediate paths may
    serialize it as a string.  Both must produce the correct bool."""

    def test_true_boolean(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(is_current=True)), member
        )
        assert result[0].is_current is True

    def test_false_boolean(self, member: MemberRecord) -> None:
        result = committee_membership_specs_from_detail(
            _detail(_committee_item(is_current=False)), member
        )
        assert result[0].is_current is False

    def test_string_true(self, member: MemberRecord) -> None:
        item = _committee_item()
        item["isCurrent"] = "true"
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result[0].is_current is True

    def test_string_false(self, member: MemberRecord) -> None:
        """String 'false' must not be coerced to True via bool()."""
        item = _committee_item()
        item["isCurrent"] = "false"
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result[0].is_current is False

    def test_string_True_capitalised(self, member: MemberRecord) -> None:
        item = _committee_item()
        item["isCurrent"] = "True"
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result[0].is_current is True

    def test_string_False_capitalised(self, member: MemberRecord) -> None:
        item = _committee_item()
        item["isCurrent"] = "False"
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result[0].is_current is False

    def test_missing_key_defaults_to_false(self, member: MemberRecord) -> None:
        item = _committee_item()
        del item["isCurrent"]
        result = committee_membership_specs_from_detail(_detail(item), member)
        assert result[0].is_current is False


# ---------------------------------------------------------------------------
# committees container type robustness
# ---------------------------------------------------------------------------


class TestCommitteesContainerRobustness:
    def test_committees_as_list_returns_empty(self, member: MemberRecord) -> None:
        detail = {"committees": [_committee_item()]}
        assert committee_membership_specs_from_detail(detail, member) == []

    def test_committees_item_as_dict_returns_empty(self, member: MemberRecord) -> None:
        # "item" is a single dict rather than a list — non-standard, should not crash
        detail = {"committees": {"item": _committee_item()}}
        assert committee_membership_specs_from_detail(detail, member) == []

    def test_committees_none_returns_empty(self, member: MemberRecord) -> None:
        assert committee_membership_specs_from_detail({"committees": None}, member) == []

    def test_committees_key_absent_returns_empty(self, member: MemberRecord) -> None:
        assert committee_membership_specs_from_detail({}, member) == []


# ---------------------------------------------------------------------------
# Realistic full-detail payload — Congress.gov API shape
# ---------------------------------------------------------------------------

# Inner member object for a senator with two committee memberships in different
# congress sessions; one closed (Ranking Member, 118th), one active (Member, 119th).
_WARREN_COMMITTEES_DETAIL: dict = {
    "bioguideId": "W000817",
    "firstName": "Elizabeth",
    "lastName": "Warren",
    "committees": {
        "item": [
            {
                "committee": {
                    "name": "Committee on Banking, Housing, and Urban Affairs",
                    "systemCode": "ssbk00",
                    "url": "https://api.congress.gov/v3/committee/senate/ssbk00",
                },
                "congress": 119,
                "endDate": None,
                "isCurrent": True,
                "rank": 2,
                "role": "Member",
                "startDate": "2025-01-15",
            },
            {
                "committee": {
                    "name": "Special Committee on Aging",
                    "systemCode": "spag00",
                    "url": "https://api.congress.gov/v3/committee/senate/spag00",
                },
                "congress": 118,
                "endDate": "2025-01-03",
                "isCurrent": False,
                "rank": 1,
                "role": "Ranking Member",
                "startDate": "2023-01-12",
            },
        ]
    },
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


class TestRealisticSenatorCommittees:
    def test_two_memberships_returned(self) -> None:
        result = committee_membership_specs_from_detail(_WARREN_COMMITTEES_DETAIL, _WARREN)
        assert len(result) == 2

    def test_bioguide_ids_from_member(self) -> None:
        result = committee_membership_specs_from_detail(_WARREN_COMMITTEES_DETAIL, _WARREN)
        assert all(r.bioguide_id == "W000817" for r in result)

    def test_committee_codes_extracted(self) -> None:
        result = committee_membership_specs_from_detail(_WARREN_COMMITTEES_DETAIL, _WARREN)
        codes = {r.committee_code for r in result}
        assert codes == {"ssbk00", "spag00"}

    def test_active_membership_is_current_no_end_date(self) -> None:
        result = committee_membership_specs_from_detail(_WARREN_COMMITTEES_DETAIL, _WARREN)
        active = next(r for r in result if r.committee_code == "ssbk00")
        assert active.is_current is True
        assert active.end_date is None

    def test_closed_membership_not_current_has_end_date(self) -> None:
        result = committee_membership_specs_from_detail(_WARREN_COMMITTEES_DETAIL, _WARREN)
        closed = next(r for r in result if r.committee_code == "spag00")
        assert closed.is_current is False
        assert closed.end_date == datetime.date(2025, 1, 3)

    def test_ranking_member_role_normalised(self) -> None:
        result = committee_membership_specs_from_detail(_WARREN_COMMITTEES_DETAIL, _WARREN)
        closed = next(r for r in result if r.committee_code == "spag00")
        assert closed.role == "ranking_member"

    def test_source_url_from_committee_node(self) -> None:
        result = committee_membership_specs_from_detail(_WARREN_COMMITTEES_DETAIL, _WARREN)
        active = next(r for r in result if r.committee_code == "ssbk00")
        assert "ssbk00" in (active.source_url or "")

    def test_rank_field_does_not_appear_in_spec(self) -> None:
        """rank is an API-side display field; it must not bleed into the spec."""
        result = committee_membership_specs_from_detail(_WARREN_COMMITTEES_DETAIL, _WARREN)
        assert not hasattr(result[0], "rank")
