"""Tests for src/db/runtime_lookups.py.

All DB access is mocked via unittest.mock.patch on
src.db.runtime_lookups.fetch_all — no live DB required.

Covers:
- fetch_member_lookup_rows: delegates to fetch_all, returns rows
- fetch_committee_lookup_rows: delegates to fetch_all, returns rows
- fetch_fec_committee_lookup_rows: delegates to fetch_all, returns rows
- fetch_financial_disclosure_lookup_rows: delegates to fetch_all, returns rows
- load_lookup_bundle: assembles a correct LookupBundle from mocked rows
- load_lookup_bundle: duplicate bioguide_id propagates LookupBuildError
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.db.runtime_lookups import (
    fetch_committee_lookup_rows,
    fetch_fec_committee_lookup_rows,
    fetch_financial_disclosure_lookup_rows,
    fetch_member_lookup_rows,
    load_lookup_bundle,
)
from src.db.lookups import LookupBuildError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONN = MagicMock()  # stand-in; never called directly by the module under test


def _member(id, bioguide_id, lis=None, fec_cand=None):
    return {"id": id, "bioguide_id": bioguide_id, "lis_member_id": lis, "fec_candidate_id": fec_cand}


def _committee(id, code, congress):
    return {"id": id, "committee_code": code, "congress": congress}


def _fec_committee(id, fec_id):
    return {"id": id, "fec_committee_id": fec_id}


def _disclosure(id, member_id, filing_year, filing_type, amendment_number=0):
    return {
        "id": id,
        "member_id": member_id,
        "filing_year": filing_year,
        "filing_type": filing_type,
        "amendment_number": amendment_number,
    }


# ---------------------------------------------------------------------------
# fetch_* tests — verify delegation to fetch_all and correct return
# ---------------------------------------------------------------------------


def test_fetch_member_lookup_rows_delegates_and_returns():
    rows = [_member(1, "A000001")]
    with patch("src.db.runtime_lookups.fetch_all", return_value=rows) as mock_fa:
        result = fetch_member_lookup_rows(CONN)
    mock_fa.assert_called_once()
    assert mock_fa.call_args[0][0] is CONN
    assert result == rows


def test_fetch_committee_lookup_rows_delegates_and_returns():
    rows = [_committee(1, "SSAF", 119)]
    with patch("src.db.runtime_lookups.fetch_all", return_value=rows) as mock_fa:
        result = fetch_committee_lookup_rows(CONN)
    mock_fa.assert_called_once()
    assert mock_fa.call_args[0][0] is CONN
    assert result == rows


def test_fetch_fec_committee_lookup_rows_delegates_and_returns():
    rows = [_fec_committee(1, "C00000001")]
    with patch("src.db.runtime_lookups.fetch_all", return_value=rows) as mock_fa:
        result = fetch_fec_committee_lookup_rows(CONN)
    mock_fa.assert_called_once()
    assert mock_fa.call_args[0][0] is CONN
    assert result == rows


def test_fetch_financial_disclosure_lookup_rows_delegates_and_returns():
    rows = [_disclosure(1, 10, 2023, "annual")]
    with patch("src.db.runtime_lookups.fetch_all", return_value=rows) as mock_fa:
        result = fetch_financial_disclosure_lookup_rows(CONN)
    mock_fa.assert_called_once()
    assert mock_fa.call_args[0][0] is CONN
    assert result == rows


# ---------------------------------------------------------------------------
# load_lookup_bundle — happy path
# ---------------------------------------------------------------------------


def _side_effect_for(member_rows, committee_rows, fec_rows, disc_rows):
    """Return a side_effect function that dispatches by SQL keyword."""
    def _se(conn, sql, params=None):
        sql_l = sql.lower()
        if "financial_disclosure" in sql_l:
            return disc_rows
        if "fec_committee" in sql_l:
            return fec_rows
        if "committee" in sql_l:
            return committee_rows
        if "member" in sql_l:
            return member_rows
        return []
    return _se


def test_load_lookup_bundle_builds_correct_maps():
    member_rows = [
        _member(1, "A000001", lis="S001", fec_cand="P00000001"),
        _member(2, "B000002"),
    ]
    committee_rows = [_committee(10, "SSAF", 119), _committee(11, "HJUD", 119)]
    fec_rows = [_fec_committee(20, "C00000001")]
    disc_rows = [
        _disclosure(30, member_id=1, filing_year=2023, filing_type="annual"),
        _disclosure(31, member_id=2, filing_year=2023, filing_type="ptr"),
    ]

    with patch(
        "src.db.runtime_lookups.fetch_all",
        side_effect=_side_effect_for(member_rows, committee_rows, fec_rows, disc_rows),
    ):
        bundle = load_lookup_bundle(CONN)

    assert bundle.bioguide_map == {"A000001": 1, "B000002": 2}
    assert bundle.lis_member_map == {"S001": 1}
    assert bundle.fec_candidate_map == {"P00000001": 1}
    assert bundle.committee_code_map == {("SSAF", 119): 10, ("HJUD", 119): 11}
    assert bundle.fec_committee_map == {"C00000001": 20}
    assert bundle.disclosure_natural_key_map == {
        (1, 2023, "annual", 0): 30,
        (2, 2023, "ptr", 0): 31,
    }


def test_load_lookup_bundle_empty_tables():
    with patch("src.db.runtime_lookups.fetch_all", return_value=[]):
        bundle = load_lookup_bundle(CONN)

    assert bundle.bioguide_map == {}
    assert bundle.committee_code_map == {}
    assert bundle.fec_committee_map == {}
    assert bundle.disclosure_natural_key_map == {}


def test_load_lookup_bundle_to_maps_structure():
    member_rows = [_member(1, "A000001")]
    with patch(
        "src.db.runtime_lookups.fetch_all",
        side_effect=_side_effect_for(member_rows, [], [], []),
    ):
        bundle = load_lookup_bundle(CONN)

    maps = bundle.to_maps()
    assert set(maps.keys()) == {
        "bioguide_map",
        "lis_member_map",
        "committee_code_map",
        "raw_id_maps",
        "disclosure_natural_key_map",
    }
    assert set(maps["raw_id_maps"].keys()) == {"fec_candidate_id", "fec_committee_id"}


# ---------------------------------------------------------------------------
# load_lookup_bundle — error propagation
# ---------------------------------------------------------------------------


def test_load_lookup_bundle_raises_on_duplicate_bioguide():
    dup_rows = [_member(1, "A000001"), _member(2, "A000001")]
    with patch(
        "src.db.runtime_lookups.fetch_all",
        side_effect=_side_effect_for(dup_rows, [], [], []),
    ):
        with pytest.raises(LookupBuildError, match="duplicate bioguide_id"):
            load_lookup_bundle(CONN)


def test_load_lookup_bundle_raises_on_duplicate_fec_committee():
    fec_dup = [_fec_committee(1, "C00000001"), _fec_committee(2, "C00000001")]
    with patch(
        "src.db.runtime_lookups.fetch_all",
        side_effect=_side_effect_for([], [], fec_dup, []),
    ):
        with pytest.raises(LookupBuildError, match="duplicate fec_committee_id"):
            load_lookup_bundle(CONN)


def test_load_lookup_bundle_raises_on_duplicate_disclosure_key():
    disc_dup = [
        _disclosure(1, member_id=5, filing_year=2023, filing_type="annual"),
        _disclosure(2, member_id=5, filing_year=2023, filing_type="annual"),
    ]
    with patch(
        "src.db.runtime_lookups.fetch_all",
        side_effect=_side_effect_for([], [], [], disc_dup),
    ):
        with pytest.raises(LookupBuildError, match="duplicate natural key"):
            load_lookup_bundle(CONN)
