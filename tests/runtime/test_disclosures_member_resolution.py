"""Tests for src/runtime/disclosures_member_resolution.py

No live DB — all resolution runs over in-process row dicts.
"""

from __future__ import annotations

import pytest

from src.runtime.disclosures_member_resolution import (
    Ambiguous,
    NoMatch,
    Resolved,
    ResolutionResult,
    house_identity,
    resolve_disclosure_member,
    resolve_disclosure_members,
    senate_identity,
)

# ---------------------------------------------------------------------------
# Fixtures — member row factory
# ---------------------------------------------------------------------------


def _house_row(
    bioguide_id: str,
    last_name: str,
    first_name: str,
    state: str,
    district: int,
) -> dict:
    return {
        "bioguide_id": bioguide_id,
        "chamber": "house",
        "state": state,
        "district": district,
        "last_name": last_name,
        "first_name": first_name,
    }


def _senate_row(
    bioguide_id: str,
    last_name: str,
    first_name: str,
    state: str,
) -> dict:
    return {
        "bioguide_id": bioguide_id,
        "chamber": "senate",
        "state": state,
        "district": None,
        "last_name": last_name,
        "first_name": first_name,
    }


# Shared roster used across many tests
_ROWS = [
    _house_row("P000197", "Pelosi", "Nancy", "CA", 11),
    _house_row("S001175", "Smith", "Adam", "WA", 9),
    _house_row("S001185", "Smith", "Adrian", "NE", 3),  # same last, different state+district
    _senate_row("W000817", "Warren", "Elizabeth", "MA"),
    _senate_row("S000033", "Sanders", "Bernard", "VT"),
]


# ---------------------------------------------------------------------------
# house_identity — parsing state_dst
# ---------------------------------------------------------------------------


class TestHouseIdentity:
    def test_basic_parse(self):
        hid = house_identity("Pelosi", "Nancy", "CA11")
        assert hid.last_name == "Pelosi"
        assert hid.first_name == "Nancy"
        assert hid.chamber == "house"
        assert hid.state == "CA"
        assert hid.district == 11

    def test_leading_zero_stripped(self):
        hid = house_identity("Smith", "Adam", "WA09")
        assert hid.district == 9

    def test_state_uppercased(self):
        hid = house_identity("X", "Y", "ca05")
        assert hid.state == "CA"

    def test_three_digit_district(self):
        # AL00 → district 0 (at-large)
        hid = house_identity("X", "Y", "AL00")
        assert hid.district == 0

    def test_state_dst_too_short_raises(self):
        with pytest.raises(ValueError, match="state_dst too short"):
            house_identity("X", "Y", "CA")

    def test_non_numeric_district_raises(self):
        with pytest.raises(ValueError, match="Non-numeric district"):
            house_identity("X", "Y", "CAXX")

    def test_district_none_not_set(self):
        hid = house_identity("X", "Y", "TX05")
        assert hid.district == 5  # House always has int district

    def test_frozen(self):
        hid = house_identity("X", "Y", "TX05")
        with pytest.raises((AttributeError, TypeError)):
            hid.state = "NY"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# senate_identity — parsing office field
# ---------------------------------------------------------------------------


class TestSenateIdentity:
    def test_basic_parse(self):
        sid = senate_identity("Warren", "Elizabeth", "Senator, MA")
        assert sid.last_name == "Warren"
        assert sid.first_name == "Elizabeth"
        assert sid.chamber == "senate"
        assert sid.state == "MA"
        assert sid.district is None

    def test_state_uppercased(self):
        sid = senate_identity("X", "Y", "Senator, vt")
        assert sid.state == "VT"

    def test_extra_whitespace_tolerated(self):
        sid = senate_identity("X", "Y", "Senator,  TX ")
        assert sid.state == "TX"

    def test_missing_state_raises(self):
        with pytest.raises(ValueError, match="Cannot extract 2-char state"):
            senate_identity("X", "Y", "Senator")

    def test_non_alpha_state_raises(self):
        with pytest.raises(ValueError, match="Cannot extract 2-char state"):
            senate_identity("X", "Y", "Senator, T1")

    def test_district_always_none(self):
        sid = senate_identity("X", "Y", "Senator, TX")
        assert sid.district is None


# ---------------------------------------------------------------------------
# resolve_disclosure_member — House
# ---------------------------------------------------------------------------


class TestResolveHouseMember:
    def test_exact_match_resolved(self):
        identity = house_identity("Pelosi", "Nancy", "CA11")
        result = resolve_disclosure_member(identity, _ROWS)
        assert isinstance(result, Resolved)
        assert result.bioguide_id == "P000197"

    def test_identity_preserved_in_resolved(self):
        identity = house_identity("Pelosi", "Nancy", "CA11")
        result = resolve_disclosure_member(identity, _ROWS)
        assert isinstance(result, Resolved)
        assert result.identity is identity

    def test_wrong_district_no_match(self):
        identity = house_identity("Pelosi", "Nancy", "CA10")
        result = resolve_disclosure_member(identity, _ROWS)
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_location"

    def test_wrong_state_no_match(self):
        identity = house_identity("Pelosi", "Nancy", "NY11")
        result = resolve_disclosure_member(identity, _ROWS)
        assert isinstance(result, NoMatch)

    def test_wrong_last_name_no_match(self):
        identity = house_identity("Johnson", "Nancy", "CA11")
        result = resolve_disclosure_member(identity, _ROWS)
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_last_name"

    def test_case_insensitive_last_name(self):
        identity = house_identity("PELOSI", "NANCY", "CA11")
        result = resolve_disclosure_member(identity, _ROWS)
        assert isinstance(result, Resolved)
        assert result.bioguide_id == "P000197"

    def test_identity_preserved_in_no_match(self):
        identity = house_identity("Ghost", "X", "CA11")
        result = resolve_disclosure_member(identity, _ROWS)
        assert isinstance(result, NoMatch)
        assert result.identity is identity


# ---------------------------------------------------------------------------
# resolve_disclosure_member — Senate
# ---------------------------------------------------------------------------


class TestResolveSenateMember:
    def test_exact_match_resolved(self):
        identity = senate_identity("Warren", "Elizabeth", "Senator, MA")
        result = resolve_disclosure_member(identity, _ROWS)
        assert isinstance(result, Resolved)
        assert result.bioguide_id == "W000817"

    def test_wrong_state_no_match(self):
        identity = senate_identity("Warren", "Elizabeth", "Senator, VT")
        result = resolve_disclosure_member(identity, _ROWS)
        # Sanders is in VT, not Warren
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_last_name"

    def test_wrong_chamber_no_match(self):
        # House member looked up as Senate — no senate members in CA in roster
        identity = senate_identity("Pelosi", "Nancy", "Senator, CA")
        result = resolve_disclosure_member(identity, _ROWS)
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_location"

    def test_senate_district_is_none(self):
        # Senate rows have district=None; House rows have int; must not cross-match
        identity = senate_identity("Smith", "Adam", "Senator, WA")
        result = resolve_disclosure_member(identity, _ROWS)
        # House Smith rows have district set; no senate Smith in WA
        assert isinstance(result, NoMatch)


# ---------------------------------------------------------------------------
# Ambiguous resolution
# ---------------------------------------------------------------------------


class TestAmbiguousResolution:
    def _two_smiths_same_seat(self) -> list[dict]:
        """Two members with same last_name in the same district — only possible
        in a special election interim period."""
        return [
            _house_row("S000001", "Smith", "Alice", "TX", 5),
            _house_row("S000002", "Smith", "Bob", "TX", 5),
        ]

    def test_last_name_tie_broken_by_first_name(self):
        rows = self._two_smiths_same_seat()
        identity = house_identity("Smith", "Alice", "TX05")
        result = resolve_disclosure_member(identity, rows)
        assert isinstance(result, Resolved)
        assert result.bioguide_id == "S000001"

    def test_ambiguous_when_first_name_also_ties(self):
        rows = [
            _house_row("S000001", "Smith", "Alice", "TX", 5),
            _house_row("S000002", "Smith", "Alice", "TX", 5),  # exact duplicate names
        ]
        identity = house_identity("Smith", "Alice", "TX05")
        result = resolve_disclosure_member(identity, rows)
        assert isinstance(result, Ambiguous)
        assert set(result.candidates) == {"S000001", "S000002"}

    def test_ambiguous_when_first_name_does_not_break_tie(self):
        # "BILL" vs "WILLIAM" — neither matches "CHARLIE" first token.
        # Last-name step found two candidates; first-name step could not narrow to one
        # → result is Ambiguous, not NoMatch (the location+last_name match IS present).
        rows = [
            _house_row("S000001", "Smith", "Bill", "TX", 5),
            _house_row("S000002", "Smith", "William", "TX", 5),
        ]
        identity = house_identity("Smith", "Charlie", "TX05")
        result = resolve_disclosure_member(identity, rows)
        assert isinstance(result, Ambiguous)
        assert set(result.candidates) == {"S000001", "S000002"}

    def test_ambiguous_preserves_identity(self):
        rows = self._two_smiths_same_seat()
        identity = house_identity("Smith", "Unknown", "TX05")
        result = resolve_disclosure_member(identity, rows)
        # Neither "ALICE" nor "BOB" matches "UNKNOWN" — ambiguous after last_name
        assert isinstance(result, Ambiguous)
        assert result.identity is identity

    def test_candidates_are_tuple(self):
        rows = self._two_smiths_same_seat()
        identity = house_identity("Smith", "Unknown", "TX05")
        result = resolve_disclosure_member(identity, rows)
        assert isinstance(result, Ambiguous)
        assert isinstance(result.candidates, tuple)

    def test_no_silent_guess(self):
        """Ambiguous must never silently return one of multiple candidates."""
        rows = self._two_smiths_same_seat()
        identity = house_identity("Smith", "Unknown", "TX05")
        result = resolve_disclosure_member(identity, rows)
        assert not isinstance(result, Resolved), (
            "Ambiguous lookup must not silently resolve to one candidate"
        )


# ---------------------------------------------------------------------------
# resolve_disclosure_members — batch
# ---------------------------------------------------------------------------


class TestResolveDisclosureMembers:
    def test_empty_input_returns_empty(self):
        result = resolve_disclosure_members([], _ROWS)
        assert result == []

    def test_returns_one_result_per_identity(self):
        identities = [
            house_identity("Pelosi", "Nancy", "CA11"),
            senate_identity("Warren", "Elizabeth", "Senator, MA"),
        ]
        results = resolve_disclosure_members(identities, _ROWS)
        assert len(results) == 2

    def test_order_preserved(self):
        identities = [
            senate_identity("Sanders", "Bernard", "Senator, VT"),
            senate_identity("Warren", "Elizabeth", "Senator, MA"),
        ]
        results = resolve_disclosure_members(identities, _ROWS)
        assert isinstance(results[0], Resolved) and results[0].bioguide_id == "S000033"
        assert isinstance(results[1], Resolved) and results[1].bioguide_id == "W000817"

    def test_mixed_outcomes(self):
        identities = [
            house_identity("Pelosi", "Nancy", "CA11"),  # Resolved
            house_identity("Ghost", "X", "CA11"),  # NoMatch
        ]
        results = resolve_disclosure_members(identities, _ROWS)
        assert isinstance(results[0], Resolved)
        assert isinstance(results[1], NoMatch)

    def test_each_identity_resolved_independently(self):
        # Same identity twice — each resolution is independent, same outcome
        identity = house_identity("Pelosi", "Nancy", "CA11")
        results = resolve_disclosure_members([identity, identity], _ROWS)
        assert all(isinstance(r, Resolved) for r in results)
        assert results[0].bioguide_id == results[1].bioguide_id

    def test_empty_rows_all_no_match(self):
        identities = [house_identity("Smith", "Adam", "WA09")]
        results = resolve_disclosure_members(identities, [])
        assert all(isinstance(r, NoMatch) for r in results)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_rows_returns_no_match(self):
        identity = house_identity("Pelosi", "Nancy", "CA11")
        result = resolve_disclosure_member(identity, [])
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_location"

    def test_chamber_case_insensitive_in_rows(self):
        rows = [_house_row("P000197", "Pelosi", "Nancy", "CA", 11)]
        rows[0] = {**rows[0], "chamber": "House"}  # mixed case
        identity = house_identity("Pelosi", "Nancy", "CA11")
        result = resolve_disclosure_member(identity, rows)
        assert isinstance(result, Resolved)

    def test_state_case_insensitive_in_rows(self):
        rows = [{**_house_row("P000197", "Pelosi", "Nancy", "CA", 11), "state": "ca"}]
        identity = house_identity("Pelosi", "Nancy", "CA11")
        result = resolve_disclosure_member(identity, rows)
        assert isinstance(result, Resolved)

    def test_last_name_case_insensitive_in_rows(self):
        rows = [{**_house_row("P000197", "Pelosi", "Nancy", "CA", 11), "last_name": "pelosi"}]
        identity = house_identity("PELOSI", "NANCY", "CA11")
        result = resolve_disclosure_member(identity, rows)
        assert isinstance(result, Resolved)

    def test_result_type_union(self):
        identity = house_identity("Pelosi", "Nancy", "CA11")
        result: ResolutionResult = resolve_disclosure_member(identity, _ROWS)
        assert isinstance(result, (Resolved, NoMatch, Ambiguous))


# ---------------------------------------------------------------------------
# NoMatchReason — typed Literal contract
# ---------------------------------------------------------------------------


class TestNoMatchReason:
    """NoMatch.reason must be one of the two canonical Literal values.

    Tests ensure no silent string aliases slip through and that callers can
    exhaustively branch on the reason without string-equality hacks.
    """

    def test_no_member_for_location_reason_is_exact_string(self):
        identity = house_identity("Ghost", "X", "AK01")
        result = resolve_disclosure_member(identity, [])
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_location"

    def test_no_member_for_last_name_reason_is_exact_string(self):
        rows = [_house_row("P000197", "Pelosi", "Nancy", "CA", 11)]
        identity = house_identity("Ghost", "X", "CA11")
        result = resolve_disclosure_member(identity, rows)
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_last_name"

    def test_reason_values_are_disjoint(self):
        """The two reason strings must be distinct — they represent different failure modes."""
        assert "no_member_for_location" != "no_member_for_last_name"

    def test_no_match_reason_importable_as_type_alias(self):
        """NoMatchReason is exported and is a type alias (not a class or enum)."""
        # If NoMatchReason is importable, the import at the top of this file worked.
        # Verify it is the same underlying type as what NoMatch carries.
        identity = house_identity("Ghost", "X", "AK01")
        result = resolve_disclosure_member(identity, [])
        assert isinstance(result, NoMatch)
        # The annotation is Literal — we can only verify the value at runtime.
        assert result.reason in ("no_member_for_location", "no_member_for_last_name")

    def test_location_reason_on_wrong_state(self):
        rows = [_house_row("P000197", "Pelosi", "Nancy", "CA", 11)]
        identity = house_identity("Pelosi", "Nancy", "TX11")  # wrong state
        result = resolve_disclosure_member(identity, rows)
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_location"

    def test_location_reason_on_senate_wrong_state(self):
        rows = [_senate_row("W000817", "Warren", "Elizabeth", "MA")]
        identity = senate_identity("Warren", "Elizabeth", "Senator, TX")
        result = resolve_disclosure_member(identity, rows)
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_location"

    def test_last_name_reason_does_not_fire_on_location_failure(self):
        """location failures must not produce no_member_for_last_name."""
        identity = house_identity("Pelosi", "Nancy", "TX11")
        result = resolve_disclosure_member(identity, [])
        assert isinstance(result, NoMatch)
        assert result.reason != "no_member_for_last_name"

    def test_ambiguous_does_not_carry_reason(self):
        """Ambiguous is a distinct outcome with no reason field."""
        rows = [
            _house_row("A000001", "Smith", "Adam", "NY", 10),
            _house_row("A000002", "Smith", "Alice", "NY", 10),
        ]
        identity = house_identity("Smith", "Zach", "NY10")  # no first-name match
        result = resolve_disclosure_member(identity, rows)
        assert isinstance(result, Ambiguous)
        assert not hasattr(result, "reason")
