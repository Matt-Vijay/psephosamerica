"""Tests for src/db/foreign_keys.py — pure unit tests, no I/O, no DB.

Covers:
- Pass-through for rows with no hint keys
- bioguide_id resolution (bare, prefixed, multi-column)
- lis_member_id resolution
- committee_code resolution (with and without congress context)
- *_raw suffix resolution
- _financial_disclosure_natural_key resolution
- Missing map entries produce structured ResolutionFailure
- Absent lookup maps produce 'lookup_not_provided' failures
- Mixed rows with multiple hint key types
- Summary counts (total_rows, resolved_count, failure_count, ok)
- Original rows are not mutated
"""

from __future__ import annotations

import pytest

from src.db.foreign_keys import (
    ResolutionFailure,
    ResolutionResult,
    ResolutionSummary,
    resolve_foreign_keys,
)

# ---------------------------------------------------------------------------
# Fixtures / shared lookup maps
# ---------------------------------------------------------------------------


@pytest.fixture()
def bio_map() -> dict[str, int]:
    return {"B001234": 1, "B005678": 2}


@pytest.fixture()
def lis_map() -> dict[str, int]:
    return {"S001": 1, "S002": 2}


@pytest.fixture()
def cc_map() -> dict[tuple[str, int], int]:
    return {
        ("SSAF", 119): 10,
        ("HFIN", 119): 11,
        ("SSAF", 118): 12,
    }


@pytest.fixture()
def raw_maps() -> dict[str, dict[str, int]]:
    return {
        "fec_committee_id": {"C00000001": 100, "C00000002": 101},
        "recipient_fec_committee_id": {"C00000003": 200},
    }


@pytest.fixture()
def disclosure_nk_map() -> dict[tuple[int, int, str, int], int]:
    return {
        (1, 2023, "annual", 0): 50,
        (2, 2022, "ptr", 0): 51,
        (1, 2023, "annual", 1): 52,
    }


@pytest.fixture()
def full_maps(bio_map, lis_map, cc_map, raw_maps, disclosure_nk_map):
    return {
        "bioguide_map": bio_map,
        "lis_member_map": lis_map,
        "committee_code_map": cc_map,
        "raw_id_maps": raw_maps,
        "disclosure_natural_key_map": disclosure_nk_map,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def first_failure(result: ResolutionResult) -> ResolutionFailure:
    return result.summary.failures[0]


# ===========================================================================
# Pass-through: rows with no hint keys
# ===========================================================================


class TestNoHintKeys:
    def test_empty_list(self):
        result = resolve_foreign_keys([])
        assert result.rows == []
        assert result.summary.total_rows == 0
        assert result.summary.resolved_count == 0
        assert result.summary.failure_count == 0
        assert result.summary.ok is True

    def test_plain_row_passes_through(self):
        row = {"member_id": 1, "vote_option": "yea", "congress": 119}
        result = resolve_foreign_keys([row])
        assert result.rows == [row]
        assert result.summary.ok is True

    def test_none_maps_treated_as_empty(self):
        row = {"id": 5, "name": "Smith"}
        result = resolve_foreign_keys([row], maps=None)
        assert result.rows == [{"id": 5, "name": "Smith"}]

    def test_original_row_not_mutated(self, bio_map):
        original = {"_bioguide_id": "B001234", "vote_option": "nay"}
        copy_before = dict(original)
        resolve_foreign_keys([original], maps={"bioguide_map": bio_map})
        assert original == copy_before


# ===========================================================================
# bioguide_id resolution
# ===========================================================================


class TestBioguideId:
    def test_bare_bioguide_id_resolves_to_member_id(self, bio_map):
        rows = [{"_bioguide_id": "B001234", "vote_option": "yea"}]
        result = resolve_foreign_keys(rows, {"bioguide_map": bio_map})
        assert result.rows[0]["member_id"] == 1
        assert "_bioguide_id" not in result.rows[0]
        assert result.summary.ok is True

    def test_prefixed_resolves_to_prefixed_fk(self, bio_map):
        rows = [{"subject_member_bioguide_id": "B005678"}]
        result = resolve_foreign_keys(rows, {"bioguide_map": bio_map})
        assert result.rows[0]["subject_member_id"] == 2
        assert "subject_member_bioguide_id" not in result.rows[0]

    def test_member_bioguide_id_resolves_to_member_id(self, bio_map):
        rows = [{"member_bioguide_id": "B001234"}]
        result = resolve_foreign_keys(rows, {"bioguide_map": bio_map})
        assert result.rows[0]["member_id"] == 1

    def test_missing_bioguide_value_fails_with_missing(self, bio_map):
        rows = [{"_bioguide_id": "UNKNOWN"}]
        result = resolve_foreign_keys(rows, {"bioguide_map": bio_map})
        assert result.summary.failure_count == 1
        assert result.summary.ok is False
        f = first_failure(result)
        assert f.hint_key == "_bioguide_id"
        assert f.hint_value == "UNKNOWN"
        assert f.reason == "missing"
        assert f.row_index == 0
        assert "member_id" not in result.rows[0]

    def test_absent_bioguide_map_fails_with_lookup_not_provided(self):
        rows = [{"_bioguide_id": "B001234"}]
        result = resolve_foreign_keys(rows, {})
        assert first_failure(result).reason == "lookup_not_provided"

    def test_multiple_rows_track_row_index_in_failure(self, bio_map):
        rows = [
            {"_bioguide_id": "B001234"},
            {"_bioguide_id": "MISSING"},
        ]
        result = resolve_foreign_keys(rows, {"bioguide_map": bio_map})
        assert result.summary.failure_count == 1
        assert result.summary.failures[0].row_index == 1

    def test_resolved_count_reflects_successes(self, bio_map):
        rows = [{"_bioguide_id": "B001234"}, {"_bioguide_id": "B005678"}]
        result = resolve_foreign_keys(rows, {"bioguide_map": bio_map})
        assert result.summary.resolved_count == 2
        assert result.summary.failure_count == 0


# ===========================================================================
# lis_member_id resolution
# ===========================================================================


class TestLisMemberId:
    def test_bare_lis_member_id_resolves_to_member_id(self, lis_map):
        rows = [{"_lis_member_id": "S001"}]
        result = resolve_foreign_keys(rows, {"lis_member_map": lis_map})
        assert result.rows[0]["member_id"] == 1
        assert "_lis_member_id" not in result.rows[0]

    def test_prefixed_lis_member_id(self, lis_map):
        rows = [{"sponsor_lis_member_id": "S002"}]
        result = resolve_foreign_keys(rows, {"lis_member_map": lis_map})
        assert result.rows[0]["sponsor_id"] == 2

    def test_missing_lis_value_fails(self, lis_map):
        rows = [{"_lis_member_id": "S999"}]
        result = resolve_foreign_keys(rows, {"lis_member_map": lis_map})
        assert first_failure(result).reason == "missing"

    def test_absent_lis_map_fails(self):
        rows = [{"_lis_member_id": "S001"}]
        result = resolve_foreign_keys(rows, {})
        assert first_failure(result).reason == "lookup_not_provided"


# ===========================================================================
# committee_code resolution
# ===========================================================================


class TestCommitteeCode:
    def test_bare_committee_code_with_congress(self, cc_map):
        rows = [{"_committee_code": "SSAF", "congress": 119}]
        result = resolve_foreign_keys(rows, {"committee_code_map": cc_map})
        assert result.rows[0]["committee_id"] == 10
        assert "_committee_code" not in result.rows[0]
        assert "congress" in result.rows[0]  # congress passthrough

    def test_prefixed_committee_code(self, cc_map):
        rows = [{"parent_committee_code": "HFIN", "congress": 119}]
        result = resolve_foreign_keys(rows, {"committee_code_map": cc_map})
        assert result.rows[0]["parent_id"] == 11

    def test_different_congress_resolves_to_different_id(self, cc_map):
        rows = [{"_committee_code": "SSAF", "congress": 118}]
        result = resolve_foreign_keys(rows, {"committee_code_map": cc_map})
        assert result.rows[0]["committee_id"] == 12

    def test_missing_congress_field_fails_with_missing_context(self, cc_map):
        rows = [{"_committee_code": "SSAF"}]
        result = resolve_foreign_keys(rows, {"committee_code_map": cc_map})
        assert first_failure(result).reason == "missing_context"

    def test_committee_code_not_in_map_fails_with_missing(self, cc_map):
        rows = [{"_committee_code": "NOPE", "congress": 119}]
        result = resolve_foreign_keys(rows, {"committee_code_map": cc_map})
        assert first_failure(result).reason == "missing"

    def test_absent_committee_code_map_fails(self):
        rows = [{"_committee_code": "SSAF", "congress": 119}]
        result = resolve_foreign_keys(rows, {})
        assert first_failure(result).reason == "lookup_not_provided"

    def test_congress_value_coerced_to_int(self, cc_map):
        """congress may arrive as a string from CSV parsing."""
        rows = [{"_committee_code": "SSAF", "congress": "119"}]
        result = resolve_foreign_keys(rows, {"committee_code_map": cc_map})
        assert result.rows[0]["committee_id"] == 10

    def test_boolean_congress_fails_without_matching_one(self):
        rows = [{"_committee_code": "SSAF", "congress": True}]
        result = resolve_foreign_keys(rows, {"committee_code_map": {("SSAF", 1): 99}})

        assert first_failure(result).reason.startswith("invalid_context")
        assert "committee_id" not in result.rows[0]


# ===========================================================================
# *_raw suffix resolution
# ===========================================================================


class TestRawSuffix:
    def test_fec_committee_raw_id_resolved(self, raw_maps):
        rows = [{"fec_committee_id_raw": "C00000001"}]
        result = resolve_foreign_keys(rows, {"raw_id_maps": raw_maps})
        assert result.rows[0]["fec_committee_id"] == 100
        assert "fec_committee_id_raw" not in result.rows[0]

    def test_prefixed_raw_id_resolved(self, raw_maps):
        rows = [{"recipient_fec_committee_id_raw": "C00000003"}]
        result = resolve_foreign_keys(rows, {"raw_id_maps": raw_maps})
        assert result.rows[0]["recipient_fec_committee_id"] == 200

    def test_missing_raw_value_fails(self, raw_maps):
        rows = [{"fec_committee_id_raw": "C99999999"}]
        result = resolve_foreign_keys(rows, {"raw_id_maps": raw_maps})
        assert first_failure(result).reason == "missing"

    def test_unknown_fk_col_in_raw_maps_fails(self, raw_maps):
        rows = [{"source_fec_committee_id_raw": "C00000001"}]
        result = resolve_foreign_keys(rows, {"raw_id_maps": raw_maps})
        assert first_failure(result).reason == "lookup_not_provided"

    def test_absent_raw_id_maps_fails(self):
        rows = [{"fec_committee_id_raw": "C00000001"}]
        result = resolve_foreign_keys(rows, {})
        assert first_failure(result).reason == "lookup_not_provided"

    def test_empty_raw_id_maps_fails(self):
        rows = [{"fec_committee_id_raw": "C00000001"}]
        result = resolve_foreign_keys(rows, {"raw_id_maps": {}})
        assert first_failure(result).reason == "lookup_not_provided"


# ===========================================================================
# _financial_disclosure_natural_key resolution
# ===========================================================================


class TestDisclosureNaturalKey:
    def test_valid_natural_key_resolves(self, disclosure_nk_map):
        nk = {"member_id": 1, "filing_year": 2023, "filing_type": "annual", "amendment_number": 0}
        rows = [{"_financial_disclosure_natural_key": nk}]
        result = resolve_foreign_keys(rows, {"disclosure_natural_key_map": disclosure_nk_map})
        assert result.rows[0]["financial_disclosure_id"] == 50
        assert "_financial_disclosure_natural_key" not in result.rows[0]

    def test_amended_filing_resolves(self, disclosure_nk_map):
        nk = {"member_id": 1, "filing_year": 2023, "filing_type": "annual", "amendment_number": 1}
        rows = [{"_financial_disclosure_natural_key": nk}]
        result = resolve_foreign_keys(rows, {"disclosure_natural_key_map": disclosure_nk_map})
        assert result.rows[0]["financial_disclosure_id"] == 52

    def test_ptr_filing_resolves(self, disclosure_nk_map):
        nk = {"member_id": 2, "filing_year": 2022, "filing_type": "ptr", "amendment_number": 0}
        rows = [{"_financial_disclosure_natural_key": nk}]
        result = resolve_foreign_keys(rows, {"disclosure_natural_key_map": disclosure_nk_map})
        assert result.rows[0]["financial_disclosure_id"] == 51

    def test_missing_natural_key_entry_fails(self, disclosure_nk_map):
        nk = {"member_id": 99, "filing_year": 2023, "filing_type": "annual", "amendment_number": 0}
        rows = [{"_financial_disclosure_natural_key": nk}]
        result = resolve_foreign_keys(rows, {"disclosure_natural_key_map": disclosure_nk_map})
        assert first_failure(result).reason == "missing"

    def test_malformed_natural_key_fails(self, disclosure_nk_map):
        rows = [{"_financial_disclosure_natural_key": {"member_id": "X", "filing_year": "not-int"}}]
        result = resolve_foreign_keys(rows, {"disclosure_natural_key_map": disclosure_nk_map})
        f = first_failure(result)
        assert f.reason.startswith("invalid_natural_key")

    def test_boolean_natural_key_parts_fail_without_matching_one(self):
        rows = [
            {
                "_financial_disclosure_natural_key": {
                    "member_id": True,
                    "filing_year": 2023,
                    "filing_type": "annual",
                    "amendment_number": 0,
                }
            }
        ]
        result = resolve_foreign_keys(
            rows,
            {"disclosure_natural_key_map": {(1, 2023, "annual", 0): 50}},
        )

        assert first_failure(result).reason.startswith("invalid_natural_key")
        assert "financial_disclosure_id" not in result.rows[0]

    def test_natural_key_missing_field_fails(self, disclosure_nk_map):
        nk = {"member_id": 1, "filing_year": 2023}  # missing filing_type, amendment_number
        rows = [{"_financial_disclosure_natural_key": nk}]
        result = resolve_foreign_keys(rows, {"disclosure_natural_key_map": disclosure_nk_map})
        f = first_failure(result)
        assert f.reason.startswith("invalid_natural_key")

    def test_absent_disclosure_map_fails(self):
        nk = {"member_id": 1, "filing_year": 2023, "filing_type": "annual", "amendment_number": 0}
        rows = [{"_financial_disclosure_natural_key": nk}]
        result = resolve_foreign_keys(rows, {})
        assert first_failure(result).reason == "lookup_not_provided"

    def test_natural_key_coerces_string_ids(self, disclosure_nk_map):
        """Values coming from CSV may be strings."""
        nk = {
            "member_id": "1",
            "filing_year": "2023",
            "filing_type": "annual",
            "amendment_number": "0",
        }
        rows = [{"_financial_disclosure_natural_key": nk}]
        result = resolve_foreign_keys(rows, {"disclosure_natural_key_map": disclosure_nk_map})
        assert result.rows[0]["financial_disclosure_id"] == 50


# ===========================================================================
# Mixed rows and summary accuracy
# ===========================================================================


class TestMixedRows:
    def test_row_with_multiple_hint_keys(self, full_maps):
        rows = [
            {
                "_bioguide_id": "B001234",
                "_committee_code": "SSAF",
                "congress": 119,
                "role": "Chair",
            }
        ]
        result = resolve_foreign_keys(rows, full_maps)
        r = result.rows[0]
        assert r["member_id"] == 1
        assert r["committee_id"] == 10
        assert r["role"] == "Chair"
        assert r["congress"] == 119
        assert result.summary.resolved_count == 2
        assert result.summary.ok is True

    def test_partial_failure_in_mixed_row(self, bio_map):
        rows = [{"_bioguide_id": "B001234", "_lis_member_id": "S001"}]
        # lis_member_map absent → one failure
        result = resolve_foreign_keys(rows, {"bioguide_map": bio_map})
        assert result.summary.resolved_count == 1
        assert result.summary.failure_count == 1
        r = result.rows[0]
        assert "member_id" in r  # bioguide resolved
        assert "_lis_member_id" not in r  # hint consumed
        # The lis hint resolved to same FK name but failed, so member_id from
        # bioguide should still be present.
        assert r["member_id"] == 1

    def test_multiple_rows_summary_aggregates(self, bio_map):
        rows = [
            {"_bioguide_id": "B001234"},
            {"_bioguide_id": "B005678"},
            {"_bioguide_id": "MISSING"},
        ]
        result = resolve_foreign_keys(rows, {"bioguide_map": bio_map})
        assert result.summary.total_rows == 3
        assert result.summary.resolved_count == 2
        assert result.summary.failure_count == 1

    def test_rows_without_hint_keys_dont_affect_counts(self, bio_map):
        rows = [
            {"member_id": 99, "vote_option": "yea"},
            {"_bioguide_id": "B001234"},
        ]
        result = resolve_foreign_keys(rows, {"bioguide_map": bio_map})
        assert result.summary.total_rows == 2
        assert result.summary.resolved_count == 1
        assert result.summary.failure_count == 0

    def test_all_hint_key_types_in_single_row(self, full_maps):
        nk = {"member_id": 1, "filing_year": 2023, "filing_type": "annual", "amendment_number": 0}
        rows = [
            {
                "_bioguide_id": "B001234",
                "_lis_member_id": "S001",
                "_committee_code": "SSAF",
                "congress": 119,
                "fec_committee_id_raw": "C00000001",
                "_financial_disclosure_natural_key": nk,
            }
        ]
        result = resolve_foreign_keys(rows, full_maps)
        r = result.rows[0]
        assert r["member_id"] == 1  # bioguide wins (written first)
        assert r["committee_id"] == 10
        assert r["fec_committee_id"] == 100
        assert r["financial_disclosure_id"] == 50
        assert result.summary.ok is True


# ===========================================================================
# ResolutionFailure string representation
# ===========================================================================


class TestResolutionFailureStr:
    def test_str_contains_row_index_key_and_reason(self):
        f = ResolutionFailure(
            row_index=3, hint_key="_bioguide_id", hint_value="X", reason="missing"
        )
        s = str(f)
        assert "3" in s
        assert "_bioguide_id" in s
        assert "missing" in s


# ===========================================================================
# ResolutionSummary.ok property
# ===========================================================================


class TestSummaryOk:
    def test_ok_when_no_failures(self):
        s = ResolutionSummary(total_rows=1, resolved_count=1, failure_count=0)
        assert s.ok is True

    def test_not_ok_when_failures_present(self):
        s = ResolutionSummary(total_rows=1, resolved_count=0, failure_count=1)
        assert s.ok is False
