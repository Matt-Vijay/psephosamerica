"""Tests for src/zip/resolve.py.

No network calls.  All inputs are inline fixtures.
"""

from __future__ import annotations

import pytest

from src.zip.resolve import (
    DistrictMemberRow,
    PluralityDistrict,
    SenatorRow,
    ZipDistrictRow,
    assemble_federal_bundle,
    find_house_member,
    find_senators,
    select_plurality_district,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ZIP_SINGLE = "90210"  # maps to exactly one district
ZIP_SPLIT = "10001"  # maps to two districts (simulated)
ZIP_NONE = "00000"  # no mapping rows

ZIP_DISTRICT_ROWS: list[ZipDistrictRow] = [
    ZipDistrictRow(zip5=ZIP_SINGLE, state="CA", district=33, population_share=1.0),
    ZipDistrictRow(zip5=ZIP_SPLIT, state="NY", district=12, population_share=0.65),
    ZipDistrictRow(zip5=ZIP_SPLIT, state="NY", district=10, population_share=0.35),
]

DISTRICT_MEMBER_ROWS: list[DistrictMemberRow] = [
    DistrictMemberRow(
        state="CA",
        district=33,
        bioguide_id="B000001",
        full_name="Alice Brown",
        party="D",
        slug="alice-brown",
    ),
    DistrictMemberRow(
        state="NY",
        district=12,
        bioguide_id="C000002",
        full_name="Carlos Cruz",
        party="R",
        slug="carlos-cruz",
    ),
]

SENATOR_ROWS: list[SenatorRow] = [
    SenatorRow(
        state="CA",
        bioguide_id="D000003",
        full_name="Dana Davis",
        party="D",
        slug="dana-davis",
        seat=1,
    ),
    SenatorRow(
        state="CA",
        bioguide_id="E000004",
        full_name="Eve Ellis",
        party="D",
        slug="eve-ellis",
        seat=2,
    ),
    SenatorRow(
        state="NY",
        bioguide_id="F000005",
        full_name="Frank Ford",
        party="D",
        slug="frank-ford",
        seat=1,
    ),
    SenatorRow(
        state="NY",
        bioguide_id="G000006",
        full_name="Grace Green",
        party="R",
        slug="grace-green",
        seat=2,
    ),
]


# ---------------------------------------------------------------------------
# select_plurality_district
# ---------------------------------------------------------------------------


class TestSelectPluralityDistrict:
    def test_returns_none_for_unknown_zip(self) -> None:
        result = select_plurality_district(ZIP_NONE, ZIP_DISTRICT_ROWS)
        assert result is None

    def test_single_district_not_ambiguous(self) -> None:
        result = select_plurality_district(ZIP_SINGLE, ZIP_DISTRICT_ROWS)
        assert result is not None
        assert result.state == "CA"
        assert result.district == 33
        assert result.is_ambiguous is False
        assert result.ambiguity_note is None

    def test_single_district_population_share(self) -> None:
        result = select_plurality_district(ZIP_SINGLE, ZIP_DISTRICT_ROWS)
        assert result is not None
        assert result.population_share == pytest.approx(1.0)

    def test_split_zip_selects_plurality(self) -> None:
        result = select_plurality_district(ZIP_SPLIT, ZIP_DISTRICT_ROWS)
        assert result is not None
        assert result.district == 12  # 0.65 > 0.35
        assert result.population_share == pytest.approx(0.65)

    def test_split_zip_is_ambiguous(self) -> None:
        result = select_plurality_district(ZIP_SPLIT, ZIP_DISTRICT_ROWS)
        assert result is not None
        assert result.is_ambiguous is True
        assert result.ambiguity_note is not None

    def test_ambiguity_note_mentions_zip(self) -> None:
        result = select_plurality_district(ZIP_SPLIT, ZIP_DISTRICT_ROWS)
        assert result is not None
        assert ZIP_SPLIT in result.ambiguity_note  # type: ignore[operator]

    def test_ambiguity_note_mentions_other_district(self) -> None:
        result = select_plurality_district(ZIP_SPLIT, ZIP_DISTRICT_ROWS)
        assert result is not None
        # district 10 should appear in the note
        assert "10" in result.ambiguity_note  # type: ignore[operator]

    def test_empty_rows_returns_none(self) -> None:
        result = select_plurality_district(ZIP_SINGLE, [])
        assert result is None


# ---------------------------------------------------------------------------
# find_house_member
# ---------------------------------------------------------------------------


class TestFindHouseMember:
    def test_finds_known_member(self) -> None:
        ref = find_house_member("CA", 33, DISTRICT_MEMBER_ROWS)
        assert ref is not None
        assert ref.bioguide_id == "B000001"
        assert ref.chamber == "house"

    def test_returns_none_for_missing_district(self) -> None:
        ref = find_house_member("CA", 99, DISTRICT_MEMBER_ROWS)
        assert ref is None

    def test_returns_none_for_unknown_state(self) -> None:
        ref = find_house_member("ZZ", 1, DISTRICT_MEMBER_ROWS)
        assert ref is None

    def test_member_fields(self) -> None:
        ref = find_house_member("NY", 12, DISTRICT_MEMBER_ROWS)
        assert ref is not None
        assert ref.full_name == "Carlos Cruz"
        assert ref.party == "R"
        assert ref.slug == "carlos-cruz"

    def test_empty_rows_returns_none(self) -> None:
        ref = find_house_member("CA", 33, [])
        assert ref is None


# ---------------------------------------------------------------------------
# find_senators
# ---------------------------------------------------------------------------


class TestFindSenators:
    def test_returns_two_senators(self) -> None:
        senators = find_senators("CA", SENATOR_ROWS)
        assert len(senators) == 2

    def test_senators_ordered_by_seat(self) -> None:
        senators = find_senators("CA", SENATOR_ROWS)
        assert senators[0].bioguide_id == "D000003"  # seat 1
        assert senators[1].bioguide_id == "E000004"  # seat 2

    def test_senator_chamber_field(self) -> None:
        senators = find_senators("CA", SENATOR_ROWS)
        for s in senators:
            assert s.chamber == "senate"

    def test_returns_empty_for_unknown_state(self) -> None:
        senators = find_senators("ZZ", SENATOR_ROWS)
        assert senators == ()

    def test_returns_empty_for_empty_rows(self) -> None:
        senators = find_senators("CA", [])
        assert senators == ()

    def test_one_senator_state(self) -> None:
        partial = [r for r in SENATOR_ROWS if r.seat == 1]  # one per state
        senators = find_senators("CA", partial)
        assert len(senators) == 1
        assert senators[0].bioguide_id == "D000003"


# ---------------------------------------------------------------------------
# assemble_federal_bundle
# ---------------------------------------------------------------------------


class TestAssembleFederalBundle:
    def test_returns_none_for_unknown_zip(self) -> None:
        bundle = assemble_federal_bundle(
            ZIP_NONE, ZIP_DISTRICT_ROWS, DISTRICT_MEMBER_ROWS, SENATOR_ROWS
        )
        assert bundle is None

    def test_bundle_zip5(self) -> None:
        bundle = assemble_federal_bundle(
            ZIP_SINGLE, ZIP_DISTRICT_ROWS, DISTRICT_MEMBER_ROWS, SENATOR_ROWS
        )
        assert bundle is not None
        assert bundle.zip5 == ZIP_SINGLE

    def test_bundle_house_member(self) -> None:
        bundle = assemble_federal_bundle(
            ZIP_SINGLE, ZIP_DISTRICT_ROWS, DISTRICT_MEMBER_ROWS, SENATOR_ROWS
        )
        assert bundle is not None
        assert bundle.house_member is not None
        assert bundle.house_member.bioguide_id == "B000001"

    def test_bundle_senators(self) -> None:
        bundle = assemble_federal_bundle(
            ZIP_SINGLE, ZIP_DISTRICT_ROWS, DISTRICT_MEMBER_ROWS, SENATOR_ROWS
        )
        assert bundle is not None
        assert len(bundle.senators) == 2

    def test_bundle_total_members(self) -> None:
        bundle = assemble_federal_bundle(
            ZIP_SINGLE, ZIP_DISTRICT_ROWS, DISTRICT_MEMBER_ROWS, SENATOR_ROWS
        )
        assert bundle is not None
        all_members = ([bundle.house_member] if bundle.house_member else []) + list(bundle.senators)
        assert len(all_members) == 3

    def test_split_zip_uses_plurality_district(self) -> None:
        bundle = assemble_federal_bundle(
            ZIP_SPLIT, ZIP_DISTRICT_ROWS, DISTRICT_MEMBER_ROWS, SENATOR_ROWS
        )
        assert bundle is not None
        assert bundle.plurality_district.district == 12

    def test_split_zip_ambiguity_propagated(self) -> None:
        bundle = assemble_federal_bundle(
            ZIP_SPLIT, ZIP_DISTRICT_ROWS, DISTRICT_MEMBER_ROWS, SENATOR_ROWS
        )
        assert bundle is not None
        assert bundle.plurality_district.is_ambiguous is True

    def test_house_member_none_when_no_district_row(self) -> None:
        # District 33 exists in zip_district_rows but not in district_member_rows
        bundle = assemble_federal_bundle(ZIP_SINGLE, ZIP_DISTRICT_ROWS, [], SENATOR_ROWS)
        assert bundle is not None
        assert bundle.house_member is None

    def test_senators_empty_when_no_senator_rows(self) -> None:
        bundle = assemble_federal_bundle(ZIP_SINGLE, ZIP_DISTRICT_ROWS, DISTRICT_MEMBER_ROWS, [])
        assert bundle is not None
        assert bundle.senators == ()

    def test_plurality_district_type(self) -> None:
        bundle = assemble_federal_bundle(
            ZIP_SINGLE, ZIP_DISTRICT_ROWS, DISTRICT_MEMBER_ROWS, SENATOR_ROWS
        )
        assert bundle is not None
        assert isinstance(bundle.plurality_district, PluralityDistrict)
