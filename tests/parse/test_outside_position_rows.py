"""Tests for outside_position_rows.py.

All tests are pure: no DB, no network, no filesystem I/O.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.parse.disclosures.models import OutsidePosition, OwnerType
from src.parse.disclosures.outside_position_rows import (
    outside_position_from_cells,
    outside_position_rows_from_table,
)


# ---------------------------------------------------------------------------
# Shared builder — thin default wrapper around outside_position_from_cells
# ---------------------------------------------------------------------------


def _op(**kw) -> OutsidePosition:
    kw.setdefault("line_number", 1)
    kw.setdefault("owner_raw", "Self")
    kw.setdefault("entity_name_raw", "Org")
    kw.setdefault("position_title_raw", None)
    kw.setdefault("from_date_raw", None)
    kw.setdefault("to_date_raw", None)
    return outside_position_from_cells(**kw)


# ---------------------------------------------------------------------------
# outside_position_from_cells
# ---------------------------------------------------------------------------


class TestOutsidePositionFromCells:
    def test_full_row_all_fields(self) -> None:
        op = _op(
            entity_name_raw="Acme Corp",
            position_title_raw="Board Member",
            from_date_raw="01/15/2020",
            to_date_raw="12/31/2023",
            source_record_id="OP-001",
        )
        assert isinstance(op, OutsidePosition)
        assert op.line_number == 1
        assert op.owner_type == OwnerType.SELF
        assert op.entity_name == "Acme Corp"
        assert op.position_title == "Board Member"
        assert op.from_date == date(2020, 1, 15)
        assert op.to_date == date(2023, 12, 31)
        assert op.source_record_id == "OP-001"

    def test_minimal_row_only_required_fields(self) -> None:
        op = _op(line_number=3, entity_name_raw="Open Org")
        assert op.line_number == 3
        assert op.entity_name == "Open Org"
        assert op.position_title is None
        assert op.from_date is None
        assert op.to_date is None
        assert op.source_record_id is None

    def test_owner_normalizes_spouse_abbreviation(self) -> None:
        assert _op(owner_raw="SP", entity_name_raw="Law Firm LLP").owner_type == OwnerType.SPOUSE

    def test_owner_normalizes_joint(self) -> None:
        assert _op(owner_raw="JT", entity_name_raw="Corp X").owner_type == OwnerType.JOINT

    def test_unrecognized_owner_falls_back_to_other(self) -> None:
        assert _op(owner_raw="Unknown").owner_type == OwnerType.OTHER

    def test_date_slash_format_mmddyyyy(self) -> None:
        op = _op(from_date_raw="06/01/2019", to_date_raw="12/31/2022")
        assert op.from_date == date(2019, 6, 1)
        assert op.to_date == date(2022, 12, 31)

    def test_date_iso_format(self) -> None:
        op = _op(from_date_raw="2020-03-15", to_date_raw="2023-09-30")
        assert op.from_date == date(2020, 3, 15)
        assert op.to_date == date(2023, 9, 30)

    def test_date_two_digit_year(self) -> None:
        assert _op(from_date_raw="01/01/20").from_date == date(2020, 1, 1)

    def test_blank_date_string_yields_none(self) -> None:
        op = _op(from_date_raw="", to_date_raw="  ")
        assert op.from_date is None
        assert op.to_date is None

    def test_dash_date_placeholder_yields_none(self) -> None:
        op = _op(from_date_raw="-", to_date_raw="—")
        assert op.from_date is None
        assert op.to_date is None

    def test_present_token_yields_none(self) -> None:
        assert _op(to_date_raw="Present").to_date is None

    def test_unrecognized_date_yields_none(self) -> None:
        assert _op(from_date_raw="not-a-date").from_date is None

    def test_entity_name_whitespace_collapsed(self) -> None:
        assert _op(entity_name_raw="  Acme   Corp  ").entity_name == "Acme Corp"

    def test_position_title_dash_yields_none(self) -> None:
        assert _op(position_title_raw="-").position_title is None

    def test_position_title_na_yields_none(self) -> None:
        assert _op(position_title_raw="N/A").position_title is None

    def test_result_is_frozen(self) -> None:
        with pytest.raises((AttributeError, TypeError)):
            _op().entity_name = "changed"  # type: ignore[misc]

    def test_line_number_preserved(self) -> None:
        for n in (1, 10, 99):
            assert _op(line_number=n).line_number == n


# ---------------------------------------------------------------------------
# outside_position_rows_from_table
# ---------------------------------------------------------------------------


class TestOutsidePositionRowsFromTable:
    def _row(self, **kwargs: str) -> dict[str, str]:
        defaults: dict[str, str] = {
            "owner": "Self",
            "organization": "Test Corp",
            "position": "Director",
            "from": "01/01/2021",
            "to": "12/31/2023",
        }
        defaults.update(kwargs)
        return defaults

    def test_single_row_produces_one_result(self) -> None:
        rows = [self._row()]
        result = outside_position_rows_from_table(rows)
        assert len(result) == 1
        assert isinstance(result[0], OutsidePosition)

    def test_multiple_rows_preserve_order(self) -> None:
        rows = [
            self._row(organization="Alpha"),
            self._row(organization="Beta"),
            self._row(organization="Gamma"),
        ]
        result = outside_position_rows_from_table(rows)
        assert [op.entity_name for op in result] == ["Alpha", "Beta", "Gamma"]

    def test_line_numbers_are_sequential_from_one(self) -> None:
        rows = [self._row() for _ in range(4)]
        result = outside_position_rows_from_table(rows)
        assert [op.line_number for op in result] == [1, 2, 3, 4]

    def test_blank_entity_row_is_skipped(self) -> None:
        rows = [
            self._row(organization="Valid Corp"),
            self._row(organization=""),
            self._row(organization="  "),
            self._row(organization="-"),
            self._row(organization="Another Corp"),
        ]
        result = outside_position_rows_from_table(rows)
        assert len(result) == 2
        assert result[0].entity_name == "Valid Corp"
        assert result[1].entity_name == "Another Corp"

    def test_empty_table_returns_empty_list(self) -> None:
        assert outside_position_rows_from_table([]) == []

    def test_all_blank_entity_rows_returns_empty_list(self) -> None:
        rows = [self._row(organization=""), self._row(organization="-")]
        assert outside_position_rows_from_table(rows) == []

    def test_fields_mapped_from_default_columns(self) -> None:
        rows = [
            {
                "owner": "SP",
                "organization": "Big Law Firm",
                "position": "Partner",
                "from": "06/01/2019",
                "to": "12/31/2022",
            }
        ]
        result = outside_position_rows_from_table(rows)
        op = result[0]
        assert op.owner_type == OwnerType.SPOUSE
        assert op.entity_name == "Big Law Firm"
        assert op.position_title == "Partner"
        assert op.from_date == date(2019, 6, 1)
        assert op.to_date == date(2022, 12, 31)

    def test_custom_column_names(self) -> None:
        rows = [
            {
                "Owner": "Self",
                "Organization Name": "Custom Org",
                "Position Held": "CEO",
                "From Date": "03/01/2020",
                "To Date": "Present",
            }
        ]
        result = outside_position_rows_from_table(
            rows,
            owner_col="Owner",
            entity_col="Organization Name",
            position_col="Position Held",
            from_col="From Date",
            to_col="To Date",
        )
        assert len(result) == 1
        op = result[0]
        assert op.entity_name == "Custom Org"
        assert op.position_title == "CEO"
        assert op.from_date == date(2020, 3, 1)
        assert op.to_date is None  # "Present" → None

    def test_source_record_id_applied_to_all_rows(self) -> None:
        rows = [self._row(organization=f"Org{i}") for i in range(3)]
        result = outside_position_rows_from_table(rows, source_record_id="FILING-99")
        assert all(op.source_record_id == "FILING-99" for op in result)

    def test_missing_optional_columns_default_to_none(self) -> None:
        rows = [{"organization": "Minimal Org"}]
        result = outside_position_rows_from_table(rows)
        op = result[0]
        assert op.owner_type == OwnerType.OTHER  # empty owner_raw → OTHER
        assert op.position_title is None
        assert op.from_date is None
        assert op.to_date is None

    def test_line_number_gaps_when_blank_rows_skipped(self) -> None:
        # line_number counts all input rows (including skipped), not just results
        rows = [
            self._row(organization="First"),
            self._row(organization=""),  # skipped but still increments counter
            self._row(organization="Third"),
        ]
        result = outside_position_rows_from_table(rows)
        assert len(result) == 2
        assert result[0].line_number == 1
        # The blank row at index 1 consumed line_number=2; Third gets 3.
        assert result[1].line_number == 3
