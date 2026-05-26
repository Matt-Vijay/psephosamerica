"""Tests for the FEC bulk-file row parsers in src/ingest/fec/bulk.py."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

from src.ingest.fec.bulk import (
    _field,
    _parse_amount,
    _parse_fec_date,
    iter_candidate_committee_linkage,
    iter_committee_master,
    iter_individual_contributions,
)


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="latin-1")
    return path


# --- field/date/amount helpers ---


def test_field_returns_none_past_row_end_and_strips_blanks() -> None:
    row = ["a", "  ", "  b  "]
    assert _field(row, 0) == "a"
    assert _field(row, 1) is None  # whitespace-only -> None
    assert _field(row, 2) == "b"  # stripped
    assert _field(row, 9) is None  # past end -> None


def test_parse_fec_date_handles_both_formats_and_invalid() -> None:
    assert _parse_fec_date("03152024") == datetime.date(2024, 3, 15)
    assert _parse_fec_date("03/15/2024") == datetime.date(2024, 3, 15)
    assert _parse_fec_date(None) is None
    assert _parse_fec_date("") is None
    assert _parse_fec_date("not-a-date") is None


def test_parse_amount_handles_blank_valid_and_invalid() -> None:
    assert _parse_amount(None) == Decimal(0)
    assert _parse_amount("") == Decimal(0)
    assert _parse_amount("250.50") == Decimal("250.50")
    assert _parse_amount("garbage") == Decimal(0)


# --- committee master (cm.txt) ---


def test_iter_committee_master_maps_fields_and_skips_incomplete(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "cm.txt",
        [
            "C001|Energy PAC|Jane Doe|st1|st2|Austin|TX|78701|U|Q|REP|Q|||",
            "|No ID Committee|",  # missing cmte_id -> skipped
            "C002||",  # missing name -> skipped
        ],
    )
    records = list(iter_committee_master(path))
    assert len(records) == 1
    rec = records[0]
    assert rec.fec_committee_id == "C001"
    assert rec.committee_name == "Energy PAC"
    assert rec.treasurer_name == "Jane Doe"
    assert rec.city == "Austin"
    assert rec.state == "TX"
    assert rec.designation_code == "U"
    assert rec.committee_type == "Q"
    assert rec.party == "REP"
    assert rec.filing_frequency == "Q"


# --- candidate-committee linkage (ccl.txt) ---


def test_iter_candidate_committee_linkage_parses_year_and_defaults_type(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "ccl.txt",
        [
            "H8CA00001|2024|2024|C001|H|P|L1",  # designation P
            "S0NY00002|notayear|2024|C002|S||L2",  # bad year -> None, blank dsgn -> "U"
            "H8TX00006||2024|C006|H|A|L6",  # empty election year -> None
            "|2024|2024|C003|H|P|L3",  # missing cand_id -> skipped
            "H8CA00004|2024|2024||H|P|L4",  # missing cmte_id -> skipped
        ],
    )
    records = list(iter_candidate_committee_linkage(path))
    assert len(records) == 3
    first, second, third = records
    assert first.fec_candidate_id == "H8CA00001"
    assert first.fec_committee_id == "C001"
    assert first.linkage_type == "P"
    assert first.election_year == 2024
    assert second.linkage_type == "U"  # blank designation -> default
    assert second.election_year is None  # unparseable year -> None
    assert third.election_year is None  # empty year field -> None


# --- individual contributions (itcont.txt) ---


def _contrib_row(cmte: str, name: str, amount: str, date: str = "03152024") -> str:
    cols = [""] * 21
    cols[0] = cmte
    cols[5] = "15"  # transaction_type
    cols[6] = "IND"  # entity_type
    cols[7] = name
    cols[8] = "Reno"
    cols[9] = "NV"
    cols[10] = "89501"
    cols[11] = "Acme"
    cols[12] = "Engineer"
    cols[13] = date
    cols[14] = amount
    cols[19] = "memo"
    cols[20] = "SUB123"
    return "|".join(cols)


def test_iter_individual_contributions_maps_fields_and_filters(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "itcont.txt",
        [
            _contrib_row("C001", "Smith, Pat", "250.00"),
            _contrib_row("C001", "Zero Donor", "0"),  # amount <= 0 -> skipped
            _contrib_row("C001", "Negative Donor", "-5"),  # negative -> skipped
            _contrib_row("", "No Committee", "100"),  # missing cmte -> skipped
            _contrib_row("C001", "", "100"),  # missing name -> skipped
        ],
    )
    records = list(iter_individual_contributions(path))
    assert len(records) == 1
    rec = records[0]
    assert rec.fec_committee_id == "C001"
    assert rec.donor_name == "Smith, Pat"
    assert rec.city == "Reno"
    assert rec.state == "NV"
    assert rec.zip_code == "89501"
    assert rec.employer == "Acme"
    assert rec.occupation == "Engineer"
    assert rec.contribution_date == datetime.date(2024, 3, 15)
    assert rec.amount == Decimal("250.00")
    assert rec.transaction_type == "15"
    assert rec.memo_text == "memo"
    assert rec.sub_id == "SUB123"
    assert rec.entity_type == "IND"
