"""Tests for src/normalize/member_crosswalk.py"""

import pytest

from src.normalize.member_crosswalk import (
    AmbiguousMatch,
    CrosswalkRecord,
    MappingConflict,
    NotFound,
    build_index,
    lookup_by_bioguide,
    lookup_by_fec,
    lookup_by_lis,
    validate_one_to_one,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ALICE = CrosswalkRecord(
    bioguide_id="A000001",
    lis_member_id="S001",
    fec_candidate_id="P00000001",
)
BOB = CrosswalkRecord(
    bioguide_id="B000002",
    lis_member_id="S002",
    fec_candidate_id="P00000002",
)
# House member — no lis_member_id
CAROL = CrosswalkRecord(
    bioguide_id="C000003",
    lis_member_id=None,
    fec_candidate_id="P00000003",
)
# No FEC id
DAN = CrosswalkRecord(
    bioguide_id="D000004",
    lis_member_id="S004",
    fec_candidate_id=None,
)


@pytest.fixture()
def clean_index():
    return build_index([ALICE, BOB, CAROL, DAN])


# ---------------------------------------------------------------------------
# CrosswalkRecord construction
# ---------------------------------------------------------------------------


class TestCrosswalkRecord:
    def test_valid_minimal(self):
        r = CrosswalkRecord(bioguide_id="X000001")
        assert r.lis_member_id is None
        assert r.fec_candidate_id is None

    def test_empty_bioguide_raises(self):
        with pytest.raises(ValueError, match="bioguide_id"):
            CrosswalkRecord(bioguide_id="")

    def test_frozen(self):
        r = CrosswalkRecord(bioguide_id="X000001")
        with pytest.raises((AttributeError, TypeError)):
            r.bioguide_id = "Y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_index — dict coercion
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_from_records(self, clean_index):
        assert len(clean_index.records) == 4

    def test_from_dicts(self):
        rows = [
            {"bioguide_id": "A000001", "lis_member_id": "S001"},
            {"bioguide_id": "B000002"},
        ]
        idx = build_index(rows)
        assert len(idx.records) == 2

    def test_dict_missing_bioguide_raises(self):
        with pytest.raises(KeyError):
            build_index([{"lis_member_id": "S001"}])

    def test_mixed_input(self):
        idx = build_index([ALICE, {"bioguide_id": "Z000009"}])
        assert len(idx.records) == 2


# ---------------------------------------------------------------------------
# Lookup by bioguide_id
# ---------------------------------------------------------------------------


class TestLookupByBioguide:
    def test_found(self, clean_index):
        result = lookup_by_bioguide(clean_index, "A000001")
        assert result == ALICE

    def test_not_found(self, clean_index):
        result = lookup_by_bioguide(clean_index, "Z999999")
        assert isinstance(result, NotFound)
        assert result.field == "bioguide_id"
        assert result.value == "Z999999"

    def test_index_method_equivalent(self, clean_index):
        assert clean_index.by_bioguide("B000002") == BOB


# ---------------------------------------------------------------------------
# Lookup by lis_member_id
# ---------------------------------------------------------------------------


class TestLookupByLis:
    def test_found(self, clean_index):
        result = lookup_by_lis(clean_index, "S001")
        assert result == ALICE

    def test_not_found_missing_key(self, clean_index):
        result = lookup_by_lis(clean_index, "S999")
        assert isinstance(result, NotFound)
        assert result.field == "lis_member_id"

    def test_house_member_no_lis(self, clean_index):
        # CAROL has no lis_member_id; bioguide lookup still works
        result = lookup_by_bioguide(clean_index, "C000003")
        assert result == CAROL

    def test_index_method_equivalent(self, clean_index):
        assert clean_index.by_lis("S002") == BOB


# ---------------------------------------------------------------------------
# Lookup by fec_candidate_id
# ---------------------------------------------------------------------------


class TestLookupByFec:
    def test_found(self, clean_index):
        result = lookup_by_fec(clean_index, "P00000003")
        assert result == CAROL

    def test_not_found(self, clean_index):
        result = lookup_by_fec(clean_index, "P99999999")
        assert isinstance(result, NotFound)
        assert result.field == "fec_candidate_id"

    def test_member_without_fec_not_indexed(self, clean_index):
        # DAN has no fec_candidate_id; should be absent from fec index
        result = lookup_by_fec(clean_index, "")
        assert isinstance(result, NotFound)

    def test_index_method_equivalent(self, clean_index):
        assert clean_index.by_fec("P00000002") == BOB


# ---------------------------------------------------------------------------
# Ambiguous match — duplicate secondary ID
# ---------------------------------------------------------------------------


class TestAmbiguousMatch:
    def test_duplicate_lis_returns_ambiguous(self):
        dupe1 = CrosswalkRecord(bioguide_id="A000001", lis_member_id="S001")
        dupe2 = CrosswalkRecord(bioguide_id="B000002", lis_member_id="S001")
        idx = build_index([dupe1, dupe2])
        result = lookup_by_lis(idx, "S001")
        assert isinstance(result, AmbiguousMatch)
        assert result.field == "lis_member_id"
        assert result.value == "S001"
        assert len(result.matches) == 2
        bioguides = {r.bioguide_id for r in result.matches}
        assert bioguides == {"A000001", "B000002"}

    def test_duplicate_fec_returns_ambiguous(self):
        dupe1 = CrosswalkRecord(bioguide_id="A000001", fec_candidate_id="P00000001")
        dupe2 = CrosswalkRecord(bioguide_id="B000002", fec_candidate_id="P00000001")
        idx = build_index([dupe1, dupe2])
        result = lookup_by_fec(idx, "P00000001")
        assert isinstance(result, AmbiguousMatch)
        assert len(result.matches) == 2

    def test_no_silent_guess_on_ambiguous(self):
        """We must never silently return one record when multiple match."""
        dupe1 = CrosswalkRecord(bioguide_id="X000001", lis_member_id="S999")
        dupe2 = CrosswalkRecord(bioguide_id="Y000002", lis_member_id="S999")
        idx = build_index([dupe1, dupe2])
        result = idx.by_lis("S999")
        assert not isinstance(result, CrosswalkRecord), (
            "Ambiguous lookup must not silently return a single record"
        )


# ---------------------------------------------------------------------------
# validate_one_to_one
# ---------------------------------------------------------------------------


class TestValidateOneToOne:
    def test_clean_dataset_no_conflicts(self):
        conflicts = validate_one_to_one([ALICE, BOB, CAROL, DAN])
        assert conflicts == []

    def test_duplicate_lis_is_conflict(self):
        dupe1 = CrosswalkRecord(bioguide_id="A000001", lis_member_id="S001")
        dupe2 = CrosswalkRecord(bioguide_id="B000002", lis_member_id="S001")
        conflicts = validate_one_to_one([dupe1, dupe2])
        lis_conflicts = [c for c in conflicts if c.field == "lis_member_id"]
        assert len(lis_conflicts) == 1
        assert set(lis_conflicts[0].bioguide_ids) == {"A000001", "B000002"}

    def test_duplicate_fec_is_conflict(self):
        dupe1 = CrosswalkRecord(bioguide_id="A000001", fec_candidate_id="P00000001")
        dupe2 = CrosswalkRecord(bioguide_id="B000002", fec_candidate_id="P00000001")
        conflicts = validate_one_to_one([dupe1, dupe2])
        fec_conflicts = [c for c in conflicts if c.field == "fec_candidate_id"]
        assert len(fec_conflicts) == 1

    def test_duplicate_bioguide_rows_is_conflict(self):
        dup1 = CrosswalkRecord(bioguide_id="A000001", lis_member_id="S001")
        dup2 = CrosswalkRecord(bioguide_id="A000001", lis_member_id="S002")
        conflicts = validate_one_to_one([dup1, dup2])
        bio_conflicts = [c for c in conflicts if c.field == "bioguide_id"]
        assert len(bio_conflicts) == 1

    def test_missing_optional_ids_not_flagged(self):
        # Members with None optional IDs should not produce spurious conflicts
        r1 = CrosswalkRecord(bioguide_id="A000001", lis_member_id=None, fec_candidate_id=None)
        r2 = CrosswalkRecord(bioguide_id="B000002", lis_member_id=None, fec_candidate_id=None)
        conflicts = validate_one_to_one([r1, r2])
        assert conflicts == []

    def test_returns_mapping_conflict_type(self):
        dupe1 = CrosswalkRecord(bioguide_id="A000001", lis_member_id="S001")
        dupe2 = CrosswalkRecord(bioguide_id="B000002", lis_member_id="S001")
        conflicts = validate_one_to_one([dupe1, dupe2])
        for c in conflicts:
            assert isinstance(c, MappingConflict)

    def test_empty_input(self):
        assert validate_one_to_one([]) == []

    def test_single_record_no_conflict(self):
        assert validate_one_to_one([ALICE]) == []
