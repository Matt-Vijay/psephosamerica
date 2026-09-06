"""Tests for disclosure header-field extraction.

All tests are pure — no I/O, no network, no DB.  Each test exercises one
mechanical extraction step against realistic line-oriented text that
matches actual House and Senate disclosure form layouts.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.parse.disclosures.header_fields import (
    CONFLICT_AMBIGUOUS_CHAMBER,
    CONFLICT_AMBIGUOUS_MEMBER_NAME,
    CONFLICT_YEAR_CONFLICT_PREFIX,
    HeaderFields,
    _detect_conflicts,
    _extract_amendment_number,
    _extract_chamber,
    _extract_filed_at,
    _extract_filing_type,
    _extract_filing_year,
    _extract_is_amended,
    _extract_member_name,
    extract_header_fields,
)
from src.parse.disclosures.models import Chamber, FilingType

# ── Fixtures: realistic form text ─────────────────────────────────────────────

_HOUSE_ANNUAL = [
    "ANNUAL FINANCIAL DISCLOSURE REPORT",
    "U.S. House of Representatives",
    "Member Name: Jane Doe",
    "Reporting Year: 2023",
    "Date Filed: May 15, 2024",
]

_SENATE_ANNUAL = [
    "Annual Report for Calendar Year 2022",
    "Name: Smith, John",
    "State: CA",
    "U.S. Senate",
    "Date Filed: 05/20/2023",
]

_SENATE_PTR = [
    "Periodic Transaction Report",
    "U.S. Senate",
    "Name: Brown, Mike",
    "Date Filed: 03/10/2024",
]

_HOUSE_PTR = [
    "Periodic Transaction Report",
    "U.S. House of Representatives",
    "Member Name: Alice Walker",
    "Date Filed: July 01, 2023",
]

_SENATE_AMENDMENT = [
    "Annual Report for Calendar Year 2021 (Amendment No. 2)",
    "U.S. Senate",
    "Name: Jones, Sarah",
    "Date Filed: 08/20/2022",
]

_AMENDMENT_NO_NUMBER = [
    "Annual Financial Disclosure Report — Amendment",
    "U.S. House of Representatives",
    "Member Name: Bob Lee",
    "Reporting Year: 2020",
    "Date Filed: 2021-06-01",
]


# ── _extract_member_name ──────────────────────────────────────────────────────


class TestExtractMemberName:
    def test_house_member_name_label(self) -> None:
        assert _extract_member_name(_HOUSE_ANNUAL) == "Jane Doe"

    def test_senate_name_label(self) -> None:
        assert _extract_member_name(_SENATE_ANNUAL) == "Smith, John"

    def test_returns_none_when_absent(self) -> None:
        assert _extract_member_name(["Some random line", "Another line"]) is None

    def test_strips_surrounding_whitespace(self) -> None:
        lines = ["Member Name:   Alice Walker   "]
        assert _extract_member_name(lines) == "Alice Walker"

    def test_case_insensitive_label(self) -> None:
        lines = ["MEMBER NAME: Robert Burns"]
        assert _extract_member_name(lines) == "Robert Burns"

    def test_empty_value_returns_none(self) -> None:
        lines = ["Name:   "]
        assert _extract_member_name(lines) is None


# ── _extract_chamber ──────────────────────────────────────────────────────────


class TestExtractChamber:
    def test_house_full_phrase(self) -> None:
        assert _extract_chamber(["U.S. House of Representatives"]) == Chamber.HOUSE

    def test_senate_phrase(self) -> None:
        assert _extract_chamber(["U.S. Senate"]) == Chamber.SENATE

    def test_house_abbreviation(self) -> None:
        assert _extract_chamber(["US House"]) == Chamber.HOUSE

    def test_senate_bare_word(self) -> None:
        assert _extract_chamber(["Filed with: Senate"]) == Chamber.SENATE

    def test_house_takes_priority_on_first_match(self) -> None:
        # House line appears before Senate line
        lines = ["U.S. House of Representatives", "U.S. Senate"]
        assert _extract_chamber(lines) == Chamber.HOUSE

    def test_returns_none_when_absent(self) -> None:
        assert _extract_chamber(["Annual Report 2023"]) is None

    def test_from_house_annual_fixture(self) -> None:
        assert _extract_chamber(_HOUSE_ANNUAL) == Chamber.HOUSE

    def test_from_senate_annual_fixture(self) -> None:
        assert _extract_chamber(_SENATE_ANNUAL) == Chamber.SENATE


# ── _extract_filing_type ──────────────────────────────────────────────────────


class TestExtractFilingType:
    def test_annual_report_phrase(self) -> None:
        assert _extract_filing_type(["Annual Financial Disclosure Report"]) == FilingType.ANNUAL

    def test_annual_report_short(self) -> None:
        assert _extract_filing_type(["Annual Report for Calendar Year 2022"]) == FilingType.ANNUAL

    def test_ptr_detection(self) -> None:
        assert _extract_filing_type(["Periodic Transaction Report"]) == FilingType.PTR

    def test_ptr_beats_annual_when_both_present(self) -> None:
        # Some PTR forms include the word "Annual" in boilerplate
        lines = ["Annual Periodic Transaction Report"]
        assert _extract_filing_type(lines) == FilingType.PTR

    def test_amendment_when_only_word_present(self) -> None:
        lines = ["Disclosure Report — Amendment"]
        assert _extract_filing_type(lines) == FilingType.AMENDMENT

    def test_annual_beats_amendment_alone(self) -> None:
        # "Amendment" appears alongside "Annual"; type should be ANNUAL not AMENDMENT
        lines = ["Annual Financial Disclosure Report — Amendment"]
        assert _extract_filing_type(lines) == FilingType.ANNUAL

    def test_returns_none_when_absent(self) -> None:
        assert _extract_filing_type(["Name: Smith, John", "Date Filed: 01/01/2024"]) is None

    def test_from_senate_ptr_fixture(self) -> None:
        assert _extract_filing_type(_SENATE_PTR) == FilingType.PTR

    def test_case_insensitive(self) -> None:
        assert _extract_filing_type(["PERIODIC TRANSACTION REPORT"]) == FilingType.PTR


# ── _extract_filing_year ──────────────────────────────────────────────────────


class TestExtractFilingYear:
    def test_reporting_year_label(self) -> None:
        assert _extract_filing_year(["Reporting Year: 2023"]) == 2023

    def test_calendar_year_label(self) -> None:
        assert _extract_filing_year(["Annual Report for Calendar Year 2022"]) == 2022

    def test_for_year_phrase(self) -> None:
        assert _extract_filing_year(["Report for Year 2021"]) == 2021

    def test_bare_year_fallback(self) -> None:
        assert _extract_filing_year(["Financial Disclosure 2019"]) == 2019

    def test_labeled_year_beats_bare_year(self) -> None:
        # Bare year in a different line should not win over the labeled one
        lines = ["Some text 2020", "Reporting Year: 2023"]
        assert _extract_filing_year(lines) == 2023

    def test_returns_none_when_absent(self) -> None:
        assert _extract_filing_year(["Name: Smith, John"]) is None

    def test_from_house_annual_fixture(self) -> None:
        assert _extract_filing_year(_HOUSE_ANNUAL) == 2023

    def test_from_senate_annual_fixture(self) -> None:
        assert _extract_filing_year(_SENATE_ANNUAL) == 2022


# ── _extract_filed_at ─────────────────────────────────────────────────────────


class TestExtractFiledAt:
    def test_long_month_format(self) -> None:
        result = _extract_filed_at(["Date Filed: May 15, 2024"])
        assert result == date(2024, 5, 15)

    def test_slash_format(self) -> None:
        result = _extract_filed_at(["Date Filed: 05/20/2023"])
        assert result == date(2023, 5, 20)

    def test_iso_format(self) -> None:
        result = _extract_filed_at(["Date Filed: 2021-06-01"])
        assert result == date(2021, 6, 1)

    def test_filed_date_label_variant(self) -> None:
        result = _extract_filed_at(["Filed Date: July 01, 2023"])
        assert result == date(2023, 7, 1)

    def test_short_month_format(self) -> None:
        result = _extract_filed_at(["Date Filed: Aug 20, 2022"])
        assert result == date(2022, 8, 20)

    def test_returns_none_when_absent(self) -> None:
        assert _extract_filed_at(["Name: Smith", "Reporting Year: 2022"]) is None

    def test_returns_none_for_unparseable_date(self) -> None:
        assert _extract_filed_at(["Date Filed: tomorrow"]) is None

    def test_from_house_annual_fixture(self) -> None:
        assert _extract_filed_at(_HOUSE_ANNUAL) == date(2024, 5, 15)

    def test_from_senate_annual_fixture(self) -> None:
        assert _extract_filed_at(_SENATE_ANNUAL) == date(2023, 5, 20)


# ── _extract_amendment_number ─────────────────────────────────────────────────


class TestExtractAmendmentNumber:
    def test_amendment_no_dot(self) -> None:
        assert _extract_amendment_number(["Amendment No. 2"]) == 2

    def test_amendment_number_word(self) -> None:
        assert _extract_amendment_number(["Amendment Number 1"]) == 1

    def test_amendment_hash(self) -> None:
        assert _extract_amendment_number(["Amendment #3"]) == 3

    def test_inline_parenthetical(self) -> None:
        assert _extract_amendment_number(["Annual Report for 2021 (Amendment No. 2)"]) == 2

    def test_returns_zero_when_absent(self) -> None:
        assert _extract_amendment_number(["Annual Report 2023"]) == 0

    def test_returns_zero_for_bare_amendment_word(self) -> None:
        # "Amendment" without a number yields 0
        assert _extract_amendment_number(["Annual Report — Amendment"]) == 0

    def test_from_senate_amendment_fixture(self) -> None:
        assert _extract_amendment_number(_SENATE_AMENDMENT) == 2


# ── _extract_is_amended ───────────────────────────────────────────────────────


class TestExtractIsAmended:
    def test_true_when_amendment_number_positive(self) -> None:
        assert _extract_is_amended([], FilingType.ANNUAL, 1) is True

    def test_true_when_filing_type_is_amendment(self) -> None:
        assert _extract_is_amended([], FilingType.AMENDMENT, 0) is True

    def test_true_when_amendment_word_in_lines(self) -> None:
        lines = ["Annual Report — Amendment"]
        assert _extract_is_amended(lines, FilingType.ANNUAL, 0) is True

    def test_false_for_clean_annual(self) -> None:
        lines = ["Annual Report 2023", "Date Filed: 01/01/2024"]
        assert _extract_is_amended(lines, FilingType.ANNUAL, 0) is False

    def test_false_for_clean_ptr(self) -> None:
        lines = ["Periodic Transaction Report"]
        assert _extract_is_amended(lines, FilingType.PTR, 0) is False


# ── extract_header_fields (integration) ──────────────────────────────────────


class TestExtractHeaderFields:
    def test_returns_header_fields_instance(self) -> None:
        result = extract_header_fields(_HOUSE_ANNUAL)
        assert isinstance(result, HeaderFields)

    def test_house_annual_full(self) -> None:
        r = extract_header_fields(_HOUSE_ANNUAL)
        assert r.member_name == "Jane Doe"
        assert r.chamber == Chamber.HOUSE
        assert r.filing_type == FilingType.ANNUAL
        assert r.filing_year == 2023
        assert r.filed_at == date(2024, 5, 15)
        assert r.amendment_number == 0
        assert r.is_amended is False
        assert r.conflicts == ()

    def test_senate_annual_full(self) -> None:
        r = extract_header_fields(_SENATE_ANNUAL)
        assert r.member_name == "Smith, John"
        assert r.chamber == Chamber.SENATE
        assert r.filing_type == FilingType.ANNUAL
        assert r.filing_year == 2022
        assert r.filed_at == date(2023, 5, 20)
        assert r.amendment_number == 0
        assert r.is_amended is False
        assert r.conflicts == ()

    def test_senate_ptr_full(self) -> None:
        r = extract_header_fields(_SENATE_PTR)
        assert r.member_name == "Brown, Mike"
        assert r.chamber == Chamber.SENATE
        assert r.filing_type == FilingType.PTR
        assert r.filed_at == date(2024, 3, 10)
        assert r.is_amended is False

    def test_house_ptr_full(self) -> None:
        r = extract_header_fields(_HOUSE_PTR)
        assert r.member_name == "Alice Walker"
        assert r.chamber == Chamber.HOUSE
        assert r.filing_type == FilingType.PTR

    def test_senate_amendment_full(self) -> None:
        r = extract_header_fields(_SENATE_AMENDMENT)
        assert r.member_name == "Jones, Sarah"
        assert r.chamber == Chamber.SENATE
        assert r.filing_type == FilingType.ANNUAL
        assert r.filing_year == 2021
        assert r.amendment_number == 2
        assert r.is_amended is True

    def test_amendment_word_without_number(self) -> None:
        r = extract_header_fields(_AMENDMENT_NO_NUMBER)
        assert r.member_name == "Bob Lee"
        assert r.chamber == Chamber.HOUSE
        assert r.filing_year == 2020
        assert r.filed_at == date(2021, 6, 1)
        assert r.amendment_number == 0
        assert r.is_amended is True

    def test_empty_lines_returns_nulls(self) -> None:
        r = extract_header_fields([])
        assert r.member_name is None
        assert r.chamber is None
        assert r.filing_type is None
        assert r.filing_year is None
        assert r.filed_at is None
        assert r.amendment_number == 0
        assert r.is_amended is False
        assert r.conflicts == ()

    def test_result_is_frozen(self) -> None:
        r = extract_header_fields(_HOUSE_ANNUAL)
        with pytest.raises((AttributeError, TypeError)):
            r.member_name = "Other"  # type: ignore[misc]


# ── _detect_conflicts ─────────────────────────────────────────────────────────


class TestDetectConflicts:
    def test_clean_house_annual_has_no_conflicts(self) -> None:
        assert _detect_conflicts(_HOUSE_ANNUAL) == ()

    def test_clean_senate_annual_has_no_conflicts(self) -> None:
        assert _detect_conflicts(_SENATE_ANNUAL) == ()

    def test_clean_senate_ptr_has_no_conflicts(self) -> None:
        assert _detect_conflicts(_SENATE_PTR) == ()

    def test_both_chamber_markers_flagged(self) -> None:
        lines = [
            "U.S. House of Representatives",
            "U.S. Senate",
            "Member Name: Jane Doe",
            "Reporting Year: 2023",
        ]
        conflicts = _detect_conflicts(lines)
        assert CONFLICT_AMBIGUOUS_CHAMBER in conflicts

    def test_single_chamber_no_conflict(self) -> None:
        lines = ["U.S. Senate", "Name: Smith, John", "Reporting Year: 2022"]
        assert _detect_conflicts(lines) == ()

    def test_multiple_labeled_years_flagged(self) -> None:
        lines = [
            "Reporting Year: 2022",
            "U.S. Senate",
            "Reporting Year: 2023",
        ]
        conflicts = _detect_conflicts(lines)
        assert any(c.startswith(CONFLICT_YEAR_CONFLICT_PREFIX) for c in conflicts)
        assert any("2022" in c and "2023" in c for c in conflicts)

    def test_same_labeled_year_repeated_not_flagged(self) -> None:
        lines = [
            "Reporting Year: 2023",
            "Annual Report for Calendar Year 2023",
        ]
        assert _detect_conflicts(lines) == ()

    def test_multiple_distinct_member_names_flagged(self) -> None:
        lines = [
            "Member Name: Jane Doe",
            "U.S. House of Representatives",
            "Member Name: John Smith",
        ]
        conflicts = _detect_conflicts(lines)
        assert CONFLICT_AMBIGUOUS_MEMBER_NAME in conflicts

    def test_same_member_name_repeated_not_flagged(self) -> None:
        lines = [
            "Member Name: Jane Doe",
            "Member Name: Jane Doe",
        ]
        assert _detect_conflicts(lines) == ()

    def test_multiple_conflicts_all_reported(self) -> None:
        lines = [
            "U.S. House of Representatives",
            "U.S. Senate",
            "Member Name: Alice",
            "Member Name: Bob",
            "Reporting Year: 2021",
            "Reporting Year: 2022",
        ]
        conflicts = _detect_conflicts(lines)
        assert len(conflicts) == 3
        assert CONFLICT_AMBIGUOUS_CHAMBER in conflicts
        assert any(c.startswith(CONFLICT_YEAR_CONFLICT_PREFIX) for c in conflicts)
        assert CONFLICT_AMBIGUOUS_MEMBER_NAME in conflicts

    def test_returns_tuple(self) -> None:
        assert isinstance(_detect_conflicts([]), tuple)
        assert isinstance(_detect_conflicts(_HOUSE_ANNUAL), tuple)

    def test_conflict_ordering_is_deterministic(self) -> None:
        """When multiple conflicts are present, the order is stable:
        chamber first, then year, then member name."""
        lines = [
            "U.S. House of Representatives",
            "U.S. Senate",
            "Member Name: Alice",
            "Member Name: Bob",
            "Reporting Year: 2021",
            "Reporting Year: 2022",
        ]
        conflicts = _detect_conflicts(lines)
        assert conflicts[0] == CONFLICT_AMBIGUOUS_CHAMBER
        assert conflicts[1].startswith(CONFLICT_YEAR_CONFLICT_PREFIX)
        assert conflicts[2] == CONFLICT_AMBIGUOUS_MEMBER_NAME


# ── Conflict constants ───────────────────────────────────────────────────────


class TestConflictConstants:
    """Verify the module-level conflict constants are importable and match
    the tokens produced by _detect_conflicts."""

    def test_ambiguous_chamber_constant_matches_output(self) -> None:
        lines = ["U.S. House of Representatives", "U.S. Senate"]
        conflicts = _detect_conflicts(lines)
        assert CONFLICT_AMBIGUOUS_CHAMBER in conflicts

    def test_year_conflict_prefix_matches_output(self) -> None:
        lines = ["Reporting Year: 2021", "Reporting Year: 2022"]
        conflicts = _detect_conflicts(lines)
        assert any(c.startswith(CONFLICT_YEAR_CONFLICT_PREFIX) for c in conflicts)

    def test_ambiguous_member_name_constant_matches_output(self) -> None:
        lines = ["Member Name: Alice", "Member Name: Bob"]
        conflicts = _detect_conflicts(lines)
        assert CONFLICT_AMBIGUOUS_MEMBER_NAME in conflicts
