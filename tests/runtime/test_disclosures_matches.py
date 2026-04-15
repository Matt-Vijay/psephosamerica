"""Tests for src/runtime/disclosures_matches.py

All resolution runs over in-process row dicts — no DB, no network.
"""

from __future__ import annotations

import pytest

from src.runtime.disclosures_matches import (
    MemberLookup,
    resolve_artifact_member,
    resolve_artifact_members,
)
from src.runtime.disclosures_member_resolution import (
    Ambiguous,
    NoMatch,
    Resolved,
)


# ---------------------------------------------------------------------------
# Fixtures — row factories
# ---------------------------------------------------------------------------


def _house_member(
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


def _senate_member(
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


def _artifact(chamber: str, source_record_id: str = "doc-001") -> dict:
    return {"chamber": chamber, "source_record_id": source_record_id}


def _house_index(
    last_name: str,
    first_name: str,
    state_dst: str,
) -> dict:
    return {"last_name": last_name, "first_name": first_name, "state_dst": state_dst}


def _senate_index(
    last_name: str,
    first_name: str,
    office: str,
) -> dict:
    return {"last_name": last_name, "first_name": first_name, "office": office}


# Member roster used across most tests
_HOUSE_ROWS = [
    _house_member("P000197", "Pelosi", "Nancy", "CA", 11),
    _house_member("S001175", "Smith", "Adam", "WA", 9),
    _house_member("G000410", "Green", "Al", "TX", 9),
]

_SENATE_ROWS = [
    _senate_member("W000817", "Warren", "Elizabeth", "MA"),
    _senate_member("S000033", "Sanders", "Bernard", "VT"),
]

_LOOKUP: MemberLookup = {
    "house": _HOUSE_ROWS,
    "senate": _SENATE_ROWS,
}


# ---------------------------------------------------------------------------
# resolve_artifact_member — House
# ---------------------------------------------------------------------------


class TestResolveArtifactMemberHouse:
    def test_exact_match_resolved(self):
        art = _artifact("house")
        idx = _house_index("Pelosi", "Nancy", "CA11")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        assert isinstance(result, Resolved)
        assert result.bioguide_id == "P000197"

    def test_identity_carried_in_resolved(self):
        art = _artifact("house")
        idx = _house_index("Pelosi", "Nancy", "CA11")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        assert isinstance(result, Resolved)
        assert result.identity.last_name == "Pelosi"
        assert result.identity.chamber == "house"

    def test_wrong_district_no_match_location(self):
        art = _artifact("house")
        idx = _house_index("Pelosi", "Nancy", "CA10")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_location"

    def test_wrong_state_no_match_location(self):
        art = _artifact("house")
        idx = _house_index("Pelosi", "Nancy", "NY11")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        assert isinstance(result, NoMatch)

    def test_wrong_last_name_no_match_name(self):
        art = _artifact("house")
        idx = _house_index("Johnson", "Nancy", "CA11")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_last_name"

    def test_case_insensitive_match(self):
        art = _artifact("house")
        idx = _house_index("PELOSI", "NANCY", "CA11")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        assert isinstance(result, Resolved)
        assert result.bioguide_id == "P000197"

    def test_leading_zero_in_state_dst(self):
        art = _artifact("house")
        idx = _house_index("Smith", "Adam", "WA09")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        assert isinstance(result, Resolved)
        assert result.bioguide_id == "S001175"


# ---------------------------------------------------------------------------
# resolve_artifact_member — Senate
# ---------------------------------------------------------------------------


class TestResolveArtifactMemberSenate:
    def test_exact_match_resolved(self):
        art = _artifact("senate")
        idx = _senate_index("Warren", "Elizabeth", "Senator, MA")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        assert isinstance(result, Resolved)
        assert result.bioguide_id == "W000817"

    def test_wrong_state_no_match(self):
        art = _artifact("senate")
        idx = _senate_index("Warren", "Elizabeth", "Senator, VT")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        # Sanders is in VT, not Warren
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_last_name"

    def test_identity_district_none_for_senate(self):
        art = _artifact("senate")
        idx = _senate_index("Warren", "Elizabeth", "Senator, MA")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        assert isinstance(result, Resolved)
        assert result.identity.district is None

    def test_chamber_mismatch_no_cross_match(self):
        # A House member name looked up via senate artifact — no senate rows for CA
        art = _artifact("senate")
        idx = _senate_index("Pelosi", "Nancy", "Senator, CA")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_location"


# ---------------------------------------------------------------------------
# resolve_artifact_member — lookup edge cases
# ---------------------------------------------------------------------------


class TestResolveArtifactMemberLookupEdges:
    def test_empty_lookup_returns_no_match(self):
        art = _artifact("house")
        idx = _house_index("Pelosi", "Nancy", "CA11")
        result = resolve_artifact_member(art, idx, {})
        assert isinstance(result, NoMatch)
        assert result.reason == "no_member_for_location"

    def test_missing_chamber_key_in_lookup_returns_no_match(self):
        art = _artifact("senate")
        idx = _senate_index("Warren", "Elizabeth", "Senator, MA")
        # Only house rows in the lookup
        result = resolve_artifact_member(art, idx, {"house": _HOUSE_ROWS})
        assert isinstance(result, NoMatch)

    def test_unknown_chamber_raises(self):
        art = _artifact("other")
        idx = {"last_name": "X", "first_name": "Y", "state_dst": "CA11"}
        with pytest.raises(ValueError, match="chamber must be"):
            resolve_artifact_member(art, idx, _LOOKUP)

    def test_missing_artifact_chamber_raises(self):
        art = {}  # no 'chamber' key
        idx = _house_index("Pelosi", "Nancy", "CA11")
        with pytest.raises(KeyError):
            resolve_artifact_member(art, idx, _LOOKUP)

    def test_missing_index_field_raises(self):
        art = _artifact("house")
        idx = {"last_name": "Pelosi", "first_name": "Nancy"}  # no state_dst
        with pytest.raises(KeyError):
            resolve_artifact_member(art, idx, _LOOKUP)

    def test_malformed_state_dst_raises(self):
        art = _artifact("house")
        idx = _house_index("Pelosi", "Nancy", "CA")  # too short
        with pytest.raises(ValueError):
            resolve_artifact_member(art, idx, _LOOKUP)


# ---------------------------------------------------------------------------
# resolve_artifact_member — ambiguous
# ---------------------------------------------------------------------------


class TestResolveArtifactMemberAmbiguous:
    def _lookup_with_two_smiths(self) -> MemberLookup:
        rows = [
            _house_member("S000001", "Smith", "Alice", "TX", 5),
            _house_member("S000002", "Smith", "Bob", "TX", 5),
        ]
        return {"house": rows}

    def test_ambiguous_when_name_not_unique(self):
        art = _artifact("house")
        idx = _house_index("Smith", "Unknown", "TX05")
        result = resolve_artifact_member(art, idx, self._lookup_with_two_smiths())
        assert isinstance(result, Ambiguous)
        assert set(result.candidates) == {"S000001", "S000002"}

    def test_first_name_breaks_tie(self):
        art = _artifact("house")
        idx = _house_index("Smith", "Alice", "TX05")
        result = resolve_artifact_member(art, idx, self._lookup_with_two_smiths())
        assert isinstance(result, Resolved)
        assert result.bioguide_id == "S000001"

    def test_ambiguous_result_is_never_a_silent_resolved(self):
        art = _artifact("house")
        idx = _house_index("Smith", "Unknown", "TX05")
        result = resolve_artifact_member(art, idx, self._lookup_with_two_smiths())
        assert not isinstance(result, Resolved)


# ---------------------------------------------------------------------------
# resolve_artifact_members — batch
# ---------------------------------------------------------------------------


class TestResolveArtifactMembers:
    def test_empty_returns_empty(self):
        assert resolve_artifact_members([], [], _LOOKUP) == []

    def test_returns_one_result_per_pair(self):
        arts = [_artifact("house"), _artifact("senate")]
        idxs = [
            _house_index("Pelosi", "Nancy", "CA11"),
            _senate_index("Warren", "Elizabeth", "Senator, MA"),
        ]
        results = resolve_artifact_members(arts, idxs, _LOOKUP)
        assert len(results) == 2

    def test_order_preserved(self):
        arts = [_artifact("senate"), _artifact("senate")]
        idxs = [
            _senate_index("Sanders", "Bernard", "Senator, VT"),
            _senate_index("Warren", "Elizabeth", "Senator, MA"),
        ]
        results = resolve_artifact_members(arts, idxs, _LOOKUP)
        assert isinstance(results[0], Resolved)
        assert results[0].bioguide_id == "S000033"
        assert isinstance(results[1], Resolved)
        assert results[1].bioguide_id == "W000817"

    def test_mixed_outcomes_preserved(self):
        arts = [_artifact("house"), _artifact("house")]
        idxs = [
            _house_index("Pelosi", "Nancy", "CA11"),   # Resolved
            _house_index("Ghost", "X", "CA11"),          # NoMatch
        ]
        results = resolve_artifact_members(arts, idxs, _LOOKUP)
        assert isinstance(results[0], Resolved)
        assert isinstance(results[1], NoMatch)

    def test_length_mismatch_raises(self):
        arts = [_artifact("house"), _artifact("house")]
        idxs = [_house_index("Pelosi", "Nancy", "CA11")]
        with pytest.raises(ValueError, match="same length"):
            resolve_artifact_members(arts, idxs, _LOOKUP)

    def test_single_pair_house(self):
        arts = [_artifact("house")]
        idxs = [_house_index("Green", "Al", "TX09")]
        results = resolve_artifact_members(arts, idxs, _LOOKUP)
        assert len(results) == 1
        assert isinstance(results[0], Resolved)
        assert results[0].bioguide_id == "G000410"

    def test_single_pair_senate(self):
        arts = [_artifact("senate")]
        idxs = [_senate_index("Sanders", "Bernard", "Senator, VT")]
        results = resolve_artifact_members(arts, idxs, _LOOKUP)
        assert len(results) == 1
        assert isinstance(results[0], Resolved)
        assert results[0].bioguide_id == "S000033"

    def test_each_pair_independent(self):
        # Same pair twice — each resolved independently, same outcome
        art = _artifact("house")
        idx = _house_index("Pelosi", "Nancy", "CA11")
        results = resolve_artifact_members([art, art], [idx, idx], _LOOKUP)
        assert all(isinstance(r, Resolved) for r in results)
        assert results[0].bioguide_id == results[1].bioguide_id


# ---------------------------------------------------------------------------
# Wrong-chamber / malformed-row explicit cases — distinct typed errors
# ---------------------------------------------------------------------------


class TestWrongChamberMalformedRows:
    """Wrong-chamber and malformed-row cases in the matches layer must raise
    typed errors (ValueError / KeyError) rather than producing silent NoMatch.

    Ambiguous vs NoMatch vs data-integrity failures are distinct outcomes.
    """

    def test_wrong_chamber_string_raises_value_error(self):
        art = {**_artifact("house"), "chamber": "congress"}
        idx = _house_index("Pelosi", "Nancy", "CA11")
        with pytest.raises(ValueError):
            resolve_artifact_member(art, idx, _LOOKUP)

    def test_missing_chamber_key_raises_key_error(self):
        art = {"source_record_id": "doc-001"}  # no 'chamber' key
        idx = _house_index("Pelosi", "Nancy", "CA11")
        with pytest.raises(KeyError):
            resolve_artifact_member(art, idx, _LOOKUP)

    def test_missing_index_last_name_raises_key_error(self):
        art = _artifact("house")
        idx: dict = {"first_name": "Nancy", "state_dst": "CA11"}  # no last_name
        with pytest.raises(KeyError):
            resolve_artifact_member(art, idx, _LOOKUP)

    def test_missing_index_state_dst_raises_key_error(self):
        art = _artifact("house")
        idx: dict = {"last_name": "Pelosi", "first_name": "Nancy"}  # no state_dst
        with pytest.raises(KeyError):
            resolve_artifact_member(art, idx, _LOOKUP)

    def test_malformed_state_dst_raises_value_error_not_no_match(self):
        """A malformed state_dst is a data-integrity failure, not a NoMatch."""
        art = _artifact("house")
        idx: dict = {"last_name": "Pelosi", "first_name": "Nancy", "state_dst": "X"}
        with pytest.raises(ValueError):
            resolve_artifact_member(art, idx, _LOOKUP)

    def test_no_match_is_distinct_from_key_error(self):
        """A genuine no-match returns NoMatch, not an exception."""
        from src.runtime.disclosures_member_resolution import NoMatch
        art = _artifact("house")
        idx = _house_index("Ghost", "X", "AK01")
        result = resolve_artifact_member(art, idx, _LOOKUP)
        assert isinstance(result, NoMatch)

    def test_ambiguous_is_distinct_from_no_match(self):
        """Ambiguous and NoMatch are distinct types — no silent collapse."""
        from src.runtime.disclosures_member_resolution import Ambiguous, NoMatch
        lookup = {
            "house": [
                _house_member("A000001", "Smith", "Adam", "NY", 10),
                _house_member("A000002", "Smith", "Alice", "NY", 10),
            ]
        }
        art = {**_artifact("house"), "chamber": "house"}
        idx = _house_index("Smith", "Zach", "NY10")  # no first-name match
        result = resolve_artifact_member(art, idx, lookup)
        assert isinstance(result, Ambiguous)
        assert not isinstance(result, NoMatch)
