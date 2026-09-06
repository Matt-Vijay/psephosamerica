"""Tests for src/runtime/disclosures_identity.py

No network calls; all index rows are constructed in-process.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.parse.disclosures.house_index import HouseFilingKind, HouseIndexRow
from src.parse.disclosures.senate_index import SenateIndexRow
from src.runtime.disclosures_identity import (
    disclosure_identity_from_index_row,
    house_identity_from_index_row,
    senate_identity_from_index_row,
)
from src.runtime.disclosures_member_resolution import DisclosureHeaderIdentity

# ---------------------------------------------------------------------------
# Row factories
# ---------------------------------------------------------------------------


def _house_row(
    last_name: str = "Pelosi",
    first_name: str = "Nancy",
    state_dst: str = "CA11",
    filing_kind: HouseFilingKind = HouseFilingKind.ANNUAL,
) -> HouseIndexRow:
    return HouseIndexRow(
        last_name=last_name,
        first_name=first_name,
        suffix="",
        raw_filing_type="O",
        state_dst=state_dst,
        year=2023,
        filing_date=date(2024, 1, 15),
        doc_id="DOC001",
        filing_kind=filing_kind,
    )


def _senate_row(
    last_name: str = "Warren",
    first_name: str = "Elizabeth",
    office: str = "Senator, MA",
) -> SenateIndexRow:
    return SenateIndexRow(
        first_name=first_name,
        last_name=last_name,
        office=office,
        report_type="Annual Report for CY2023",
        date_filed="01/15/2024",
        doc_id="uuid-001",
        filing_year=2023,
    )


# ---------------------------------------------------------------------------
# house_identity_from_index_row
# ---------------------------------------------------------------------------


class TestHouseIdentityFromIndexRow:
    def test_returns_disclosure_header_identity(self):
        result = house_identity_from_index_row(_house_row())
        assert isinstance(result, DisclosureHeaderIdentity)

    def test_chamber_is_house(self):
        result = house_identity_from_index_row(_house_row())
        assert result.chamber == "house"

    def test_names_preserved(self):
        result = house_identity_from_index_row(_house_row("Smith", "Adam", "WA09"))
        assert result.last_name == "Smith"
        assert result.first_name == "Adam"

    def test_state_parsed_from_state_dst(self):
        result = house_identity_from_index_row(_house_row(state_dst="CA11"))
        assert result.state == "CA"

    def test_district_parsed_from_state_dst(self):
        result = house_identity_from_index_row(_house_row(state_dst="CA11"))
        assert result.district == 11

    def test_leading_zero_stripped_from_district(self):
        result = house_identity_from_index_row(_house_row(state_dst="WA09"))
        assert result.district == 9

    def test_district_is_not_none(self):
        result = house_identity_from_index_row(_house_row(state_dst="TX05"))
        assert result.district is not None

    def test_malformed_state_dst_raises(self):
        with pytest.raises(ValueError):
            house_identity_from_index_row(_house_row(state_dst="CA"))

    def test_ptr_filing_kind_accepted(self):
        row = _house_row(filing_kind=HouseFilingKind.PTR, state_dst="TX05")
        result = house_identity_from_index_row(row)
        assert result.state == "TX"
        assert result.district == 5


# ---------------------------------------------------------------------------
# senate_identity_from_index_row
# ---------------------------------------------------------------------------


class TestSenateIdentityFromIndexRow:
    def test_returns_disclosure_header_identity(self):
        result = senate_identity_from_index_row(_senate_row())
        assert isinstance(result, DisclosureHeaderIdentity)

    def test_chamber_is_senate(self):
        result = senate_identity_from_index_row(_senate_row())
        assert result.chamber == "senate"

    def test_names_preserved(self):
        result = senate_identity_from_index_row(_senate_row("Sanders", "Bernard", "Senator, VT"))
        assert result.last_name == "Sanders"
        assert result.first_name == "Bernard"

    def test_state_parsed_from_office(self):
        result = senate_identity_from_index_row(_senate_row(office="Senator, TX"))
        assert result.state == "TX"

    def test_district_is_none(self):
        result = senate_identity_from_index_row(_senate_row())
        assert result.district is None

    def test_state_uppercased(self):
        result = senate_identity_from_index_row(_senate_row(office="Senator, vt"))
        assert result.state == "VT"

    def test_malformed_office_raises(self):
        with pytest.raises(ValueError):
            senate_identity_from_index_row(_senate_row(office="Senator"))

    def test_non_alpha_state_raises(self):
        with pytest.raises(ValueError):
            senate_identity_from_index_row(_senate_row(office="Senator, T1"))


# ---------------------------------------------------------------------------
# disclosure_identity_from_index_row — dispatch
# ---------------------------------------------------------------------------


class TestDisclosureIdentityFromIndexRow:
    def test_house_chamber_dispatches_correctly(self):
        row = _house_row(state_dst="CA11")
        result = disclosure_identity_from_index_row("house", row)
        assert result.chamber == "house"
        assert result.state == "CA"
        assert result.district == 11

    def test_senate_chamber_dispatches_correctly(self):
        row = _senate_row(office="Senator, MA")
        result = disclosure_identity_from_index_row("senate", row)
        assert result.chamber == "senate"
        assert result.state == "MA"
        assert result.district is None

    def test_chamber_case_insensitive_house(self):
        row = _house_row(state_dst="TX05")
        result = disclosure_identity_from_index_row("House", row)
        assert result.chamber == "house"

    def test_chamber_case_insensitive_senate(self):
        row = _senate_row(office="Senator, VT")
        result = disclosure_identity_from_index_row("SENATE", row)
        assert result.chamber == "senate"

    def test_unknown_chamber_raises(self):
        row = _house_row()
        with pytest.raises(ValueError, match="Unknown chamber"):
            disclosure_identity_from_index_row("xyz", row)

    def test_house_chamber_with_senate_row_raises(self):
        row = _senate_row()
        with pytest.raises(TypeError):
            disclosure_identity_from_index_row("house", row)

    def test_senate_chamber_with_house_row_raises(self):
        row = _house_row()
        with pytest.raises(TypeError):
            disclosure_identity_from_index_row("senate", row)

    def test_returns_disclosure_header_identity(self):
        row = _house_row()
        result = disclosure_identity_from_index_row("house", row)
        assert isinstance(result, DisclosureHeaderIdentity)


# ---------------------------------------------------------------------------
# Wrong-chamber / malformed-row explicit errors — typed and distinct
# ---------------------------------------------------------------------------


class TestWrongChamberExplicitErrors:
    """Wrong-chamber mismatches and malformed rows must raise typed errors.

    TypeError for wrong row type, ValueError for unknown chamber string.
    These are distinct from each other and from resolution NoMatch.
    """

    def test_house_chamber_wrong_row_type_is_type_error_not_value_error(self):
        row = _senate_row()
        with pytest.raises(TypeError):
            disclosure_identity_from_index_row("house", row)

    def test_senate_chamber_wrong_row_type_is_type_error_not_value_error(self):
        row = _house_row()
        with pytest.raises(TypeError):
            disclosure_identity_from_index_row("senate", row)

    def test_unknown_chamber_is_value_error_not_type_error(self):
        row = _house_row()
        with pytest.raises(ValueError):
            disclosure_identity_from_index_row("congress", row)

    def test_house_wrong_row_error_message_names_type(self):
        row = _senate_row()
        with pytest.raises(TypeError, match="HouseIndexRow"):
            disclosure_identity_from_index_row("house", row)

    def test_senate_wrong_row_error_message_names_type(self):
        row = _house_row()
        with pytest.raises(TypeError, match="SenateIndexRow"):
            disclosure_identity_from_index_row("senate", row)

    def test_unknown_chamber_error_names_the_value(self):
        row = _house_row()
        with pytest.raises(ValueError, match="congress"):
            disclosure_identity_from_index_row("congress", row)

    def test_malformed_state_dst_raises_value_error_through_house_extractor(self):
        row = _house_row(state_dst="X")  # too short
        with pytest.raises(ValueError):
            house_identity_from_index_row(row)

    def test_malformed_office_raises_value_error_through_senate_extractor(self):
        row = _senate_row(office="no-comma-here")  # unparseable
        with pytest.raises(ValueError):
            senate_identity_from_index_row(row)
