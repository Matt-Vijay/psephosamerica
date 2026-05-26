"""Tests for src.ingest.fec.normalize deterministic cleanup functions."""

from __future__ import annotations

from src.ingest.fec.normalize import (
    normalize_committee_id,
    normalize_donor_name,
    normalize_employer,
    normalize_occupation,
)


# ---------------------------------------------------------------------------
# normalize_donor_name
# ---------------------------------------------------------------------------


class TestNormalizeDonorName:
    def test_empty_and_none(self):
        assert normalize_donor_name(None) == ""
        assert normalize_donor_name("") == ""
        assert normalize_donor_name("   ") == ""

    def test_upper_and_whitespace(self):
        assert normalize_donor_name("  john   doe  ") == "JOHN DOE"

    def test_last_comma_first(self):
        assert normalize_donor_name("DOE, JOHN") == "JOHN DOE"
        assert normalize_donor_name("DOE, JOHN MICHAEL") == "JOHN DOE"

    def test_suffix_stripping(self):
        assert normalize_donor_name("DOE, JOHN JR") == "JOHN DOE"
        assert normalize_donor_name("DOE, JOHN MD") == "JOHN DOE"
        assert normalize_donor_name("DOE, JOHN JR.") == "JOHN DOE"
        assert normalize_donor_name("SMITH, JANE SR MD") == "JANE SMITH"

    def test_no_comma_passthrough(self):
        assert normalize_donor_name("JOHN DOE") == "JOHN DOE"

    def test_unicode_normalization(self):
        # Full-width characters should normalize
        result = normalize_donor_name("ＤＯＥ, ＪＯＨＮ")
        assert result == "JOHN DOE"


# ---------------------------------------------------------------------------
# normalize_employer
# ---------------------------------------------------------------------------


class TestNormalizeEmployer:
    def test_empty_and_none(self):
        assert normalize_employer(None) == ""
        assert normalize_employer("") == ""

    def test_self_employed_variants(self):
        assert normalize_employer("SELF-EMPLOYED") == "SELF-EMPLOYED"
        assert normalize_employer("self employed") == "SELF-EMPLOYED"
        assert normalize_employer("Self") == "SELF-EMPLOYED"
        assert normalize_employer("NONE") == "SELF-EMPLOYED"
        assert normalize_employer("N/A") == "SELF-EMPLOYED"
        assert normalize_employer("NOT EMPLOYED") == "SELF-EMPLOYED"

    def test_retired(self):
        assert normalize_employer("RETIRED") == "RETIRED"
        assert normalize_employer("retired") == "RETIRED"

    def test_homemaker(self):
        assert normalize_employer("HOMEMAKER") == "HOMEMAKER"

    def test_student(self):
        assert normalize_employer("STUDENT") == "STUDENT"

    def test_entity_suffix_stripping(self):
        assert normalize_employer("ACME INC") == "ACME"
        assert normalize_employer("ACME INC.") == "ACME"
        assert normalize_employer("FOO CORP") == "FOO"
        assert normalize_employer("BAR LLC") == "BAR"
        assert normalize_employer("BAZ LLP") == "BAZ"

    def test_real_employer(self):
        assert normalize_employer("GOOGLE") == "GOOGLE"
        assert normalize_employer("  Goldman  Sachs  ") == "GOLDMAN SACHS"


# ---------------------------------------------------------------------------
# normalize_occupation
# ---------------------------------------------------------------------------


class TestNormalizeOccupation:
    def test_empty_and_none(self):
        assert normalize_occupation(None) == ""
        assert normalize_occupation("") == ""

    def test_generic_values_become_empty(self):
        assert normalize_occupation("NONE") == ""
        assert normalize_occupation("N/A") == ""
        assert normalize_occupation("RETIRED") == ""
        assert normalize_occupation("INFORMATION REQUESTED") == ""

    def test_real_occupations(self):
        assert normalize_occupation("ATTORNEY") == "ATTORNEY"
        assert normalize_occupation("  software  engineer  ") == "SOFTWARE ENGINEER"


# ---------------------------------------------------------------------------
# normalize_committee_id
# ---------------------------------------------------------------------------


class TestNormalizeCommitteeId:
    def test_empty_and_none(self):
        assert normalize_committee_id(None) == ""
        assert normalize_committee_id("") == ""

    def test_valid_ids(self):
        assert normalize_committee_id("C00431445") == "C00431445"
        assert normalize_committee_id("  c00431445 ") == "C00431445"

    def test_invalid_ids(self):
        assert normalize_committee_id("X00431445") == ""
        assert normalize_committee_id("C0043144") == ""  # too short
        assert normalize_committee_id("C004314456") == ""  # too long
        assert normalize_committee_id("NOTANID") == ""
