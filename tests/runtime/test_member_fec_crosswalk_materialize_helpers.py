"""Tests for the pure helpers in member_fec_crosswalk_materialize."""

from __future__ import annotations

from src.runtime.member_fec_crosswalk_materialize import (
    _clean_id,
    _clean_optional_int,
    _clean_state,
    _fec_ids,
    _latest_term_type,
    _select_fec_id,
    _term_date,
    _term_rows_from_item,
)


def test_clean_id_upcases_strips_and_rejects_non_strings() -> None:
    assert _clean_id("  h4mi00001 ") == "H4MI00001"
    assert _clean_id("") is None
    assert _clean_id(123) is None


def test_fec_ids_handles_string_list_and_dedups() -> None:
    assert _fec_ids("h4mi00001") == ["H4MI00001"]
    assert _fec_ids(["H1", "H1", "s2", None, ""]) == ["H1", "S2"]
    assert _fec_ids(42) == []


def test_latest_term_type() -> None:
    assert _latest_term_type({"terms": [{"type": "rep"}, {"type": "SEN"}]}) == "sen"
    assert _latest_term_type({"terms": []}) is None
    assert _latest_term_type({"terms": "x"}) is None
    assert _latest_term_type({"terms": ["not-a-dict"]}) is None
    assert _latest_term_type({"terms": [{"no_type": 1}]}) is None


def test_term_date_normalizes_year_and_iso() -> None:
    assert _term_date(None) is None
    assert _term_date("2019") == "2019-01-03"
    assert _term_date("2019-01-03T00:00:00") == "2019-01-03"


def test_clean_state_requires_two_chars() -> None:
    assert _clean_state("ca") == "CA"
    assert _clean_state("california") is None
    assert _clean_state(None) is None


def test_clean_optional_int() -> None:
    assert _clean_optional_int(None) == ""
    assert _clean_optional_int("") == ""
    assert _clean_optional_int(True) == ""
    assert _clean_optional_int("12") == "12"
    assert _clean_optional_int(5) == "5"
    assert _clean_optional_int("abc") == ""


def test_select_fec_id_prefers_chamber_prefix_then_falls_back() -> None:
    assert _select_fec_id(["S2CA", "H4MI"], latest_term_type="rep") == "H4MI"
    assert _select_fec_id(["S2CA", "H4MI"], latest_term_type="sen") == "S2CA"
    assert _select_fec_id(["X1", "Y2"], latest_term_type="rep") == "X1"  # no H -> first
    assert _select_fec_id([], latest_term_type=None) is None


def test_term_rows_from_item_maps_chambers_and_skips_invalid() -> None:
    item = {
        "terms": [
            {"type": "rep", "start": "2019-01-03", "state": "ca", "district": 12, "congress": 116},
            {"type": "sen", "start": "2021", "state": "ny"},
            "not-a-dict",
            {"type": "rep"},  # missing start -> skipped
            {"type": "gov", "start": "2020-01-01"},  # non-legislative chamber -> skipped
        ]
    }
    rows = _term_rows_from_item(item, bioguide_id="A000001")
    assert [r["chamber"] for r in rows] == ["house", "senate"]
    assert rows[0]["district"] == "12"
    assert rows[1]["district"] == ""  # senate has no district
    assert rows[1]["is_current"] == "true"  # no end date
    assert _term_rows_from_item({"terms": "not-a-list"}, bioguide_id="A000001") == []
