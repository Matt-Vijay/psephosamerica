"""Tests for src/db/lookups.py — pure unit tests, no I/O, no DB.

Covers:
- build_bioguide_map: happy path, None skipped, duplicate raises
- build_lis_member_map: happy path, None skipped, duplicate raises
- build_fec_candidate_map: happy path, None skipped, duplicate raises
- build_committee_code_map: happy path, duplicate (code, congress) raises
- build_fec_committee_map: happy path, duplicate raises
- build_disclosure_natural_key_map: happy path, duplicate raises
- _detect_duplicates: missing required fields raises with detail
- build_lookup_bundle: round-trip, to_maps() produces correct structure
- LookupBundle.to_maps() has expected top-level keys
- LookupBuildError carries .duplicates list
"""

from __future__ import annotations

import pytest

from src.db.lookups import (
    LookupBuildError,
    LookupBundle,
    build_bioguide_map,
    build_committee_code_map,
    build_disclosure_natural_key_map,
    build_disclosure_source_record_id_map,
    build_fec_candidate_map,
    build_fec_committee_map,
    build_lis_member_map,
    build_lookup_bundle,
)

# ---------------------------------------------------------------------------
# Sample row factories
# ---------------------------------------------------------------------------


def _member(
    id: int,
    bioguide_id: str,
    lis_member_id: str | None = None,
    fec_candidate_id: str | None = None,
) -> dict:
    return {
        "id": id,
        "bioguide_id": bioguide_id,
        "lis_member_id": lis_member_id,
        "fec_candidate_id": fec_candidate_id,
    }


def _committee(id: int, committee_code: str, congress: int) -> dict:
    return {"id": id, "committee_code": committee_code, "congress": congress}


def _fec_committee(id: int, fec_committee_id: str) -> dict:
    return {"id": id, "fec_committee_id": fec_committee_id}


def _disclosure(
    id: int,
    member_id: int,
    filing_year: int,
    filing_type: str,
    amendment_number: int = 0,
    source_record_id: str | None = None,
) -> dict:
    return {
        "id": id,
        "member_id": member_id,
        "filing_year": filing_year,
        "filing_type": filing_type,
        "amendment_number": amendment_number,
        "source_record_id": source_record_id,
    }


# ---------------------------------------------------------------------------
# build_bioguide_map
# ---------------------------------------------------------------------------


class TestBuildBioguidMap:
    def test_happy_path(self):
        rows = [
            _member(1, "B001234"),
            _member(2, "B005678"),
        ]
        result = build_bioguide_map(rows)
        assert result == {"B001234": 1, "B005678": 2}

    def test_none_bioguide_skipped(self):
        rows = [
            _member(1, "B001234"),
            {"id": 2, "bioguide_id": None},
        ]
        result = build_bioguide_map(rows)
        assert result == {"B001234": 1}

    def test_empty_input(self):
        assert build_bioguide_map([]) == {}

    def test_duplicate_raises(self):
        rows = [_member(1, "B001234"), _member(2, "B001234")]
        with pytest.raises(LookupBuildError) as exc_info:
            build_bioguide_map(rows)
        err = exc_info.value
        assert "B001234" in str(err)
        assert len(err.duplicates) == 1
        key, colliding = err.duplicates[0]
        assert key == "B001234"
        assert len(colliding) == 2

    def test_missing_id_field_raises(self):
        rows = [{"bioguide_id": "B001234"}]
        with pytest.raises(LookupBuildError):
            build_bioguide_map(rows)

    def test_boolean_id_raises(self):
        rows = [{"id": True, "bioguide_id": "B001234"}]
        with pytest.raises(LookupBuildError, match="id"):
            build_bioguide_map(rows)


# ---------------------------------------------------------------------------
# build_lis_member_map
# ---------------------------------------------------------------------------


class TestBuildLisMemberMap:
    def test_happy_path(self):
        rows = [
            _member(1, "B001234", lis_member_id="S001"),
            _member(2, "B005678", lis_member_id="S002"),
        ]
        result = build_lis_member_map(rows)
        assert result == {"S001": 1, "S002": 2}

    def test_none_lis_skipped(self):
        rows = [
            _member(1, "B001234", lis_member_id="S001"),
            _member(2, "B005678", lis_member_id=None),
        ]
        result = build_lis_member_map(rows)
        assert result == {"S001": 1}

    def test_all_none_returns_empty(self):
        rows = [_member(1, "B001234"), _member(2, "B005678")]
        assert build_lis_member_map(rows) == {}

    def test_duplicate_raises(self):
        rows = [
            _member(1, "B001234", lis_member_id="S001"),
            _member(2, "B005678", lis_member_id="S001"),
        ]
        with pytest.raises(LookupBuildError) as exc_info:
            build_lis_member_map(rows)
        assert "S001" in str(exc_info.value)
        assert len(exc_info.value.duplicates) == 1


# ---------------------------------------------------------------------------
# build_fec_candidate_map
# ---------------------------------------------------------------------------


class TestBuildFecCandidateMap:
    def test_happy_path(self):
        rows = [
            _member(1, "B001234", fec_candidate_id="H0TX01234"),
            _member(2, "B005678", fec_candidate_id="S6TX00567"),
        ]
        result = build_fec_candidate_map(rows)
        assert result == {"H0TX01234": 1, "S6TX00567": 2}

    def test_none_fec_candidate_skipped(self):
        rows = [
            _member(1, "B001234", fec_candidate_id="H0TX01234"),
            _member(2, "B005678", fec_candidate_id=None),
        ]
        result = build_fec_candidate_map(rows)
        assert result == {"H0TX01234": 1}

    def test_duplicate_raises(self):
        rows = [
            _member(1, "B001234", fec_candidate_id="H0TX01234"),
            _member(2, "B005678", fec_candidate_id="H0TX01234"),
        ]
        with pytest.raises(LookupBuildError) as exc_info:
            build_fec_candidate_map(rows)
        assert "H0TX01234" in str(exc_info.value)


# ---------------------------------------------------------------------------
# build_committee_code_map
# ---------------------------------------------------------------------------


class TestBuildCommitteeCodeMap:
    def test_happy_path(self):
        rows = [
            _committee(10, "SSAF", 119),
            _committee(11, "HFIN", 119),
            _committee(12, "SSAF", 118),
        ]
        result = build_committee_code_map(rows)
        assert result == {
            ("SSAF", 119): 10,
            ("HFIN", 119): 11,
            ("SSAF", 118): 12,
        }

    def test_same_code_different_congress_ok(self):
        rows = [_committee(1, "APPR", 119), _committee(2, "APPR", 118)]
        result = build_committee_code_map(rows)
        assert result[("APPR", 119)] == 1
        assert result[("APPR", 118)] == 2

    def test_duplicate_same_congress_raises(self):
        rows = [_committee(1, "SSAF", 119), _committee(2, "SSAF", 119)]
        with pytest.raises(LookupBuildError) as exc_info:
            build_committee_code_map(rows)
        err = exc_info.value
        assert len(err.duplicates) == 1
        key, _ = err.duplicates[0]
        assert key == ("SSAF", 119)

    def test_empty_returns_empty(self):
        assert build_committee_code_map([]) == {}

    def test_missing_congress_raises(self):
        rows = [{"id": 1, "committee_code": "SSAF"}]
        with pytest.raises(LookupBuildError):
            build_committee_code_map(rows)

    def test_boolean_congress_raises(self):
        rows = [{"id": 1, "committee_code": "SSAF", "congress": True}]
        with pytest.raises(LookupBuildError, match="congress"):
            build_committee_code_map(rows)


# ---------------------------------------------------------------------------
# build_fec_committee_map
# ---------------------------------------------------------------------------


class TestBuildFecCommitteeMap:
    def test_happy_path(self):
        rows = [
            _fec_committee(100, "C00000001"),
            _fec_committee(101, "C00000002"),
        ]
        result = build_fec_committee_map(rows)
        assert result == {"C00000001": 100, "C00000002": 101}

    def test_duplicate_raises(self):
        rows = [_fec_committee(100, "C00000001"), _fec_committee(101, "C00000001")]
        with pytest.raises(LookupBuildError) as exc_info:
            build_fec_committee_map(rows)
        assert "C00000001" in str(exc_info.value)
        assert len(exc_info.value.duplicates) == 1

    def test_missing_fec_committee_id_raises(self):
        rows = [{"id": 1}]
        with pytest.raises(LookupBuildError):
            build_fec_committee_map(rows)

    def test_empty_returns_empty(self):
        assert build_fec_committee_map([]) == {}


# ---------------------------------------------------------------------------
# build_disclosure_natural_key_map
# ---------------------------------------------------------------------------


class TestBuildDisclosureNaturalKeyMap:
    def test_happy_path(self):
        rows = [
            _disclosure(1, member_id=10, filing_year=2023, filing_type="annual"),
            _disclosure(2, member_id=10, filing_year=2023, filing_type="ptr"),
            _disclosure(3, member_id=11, filing_year=2023, filing_type="annual"),
        ]
        result = build_disclosure_natural_key_map(rows)
        assert result[(10, 2023, "annual", 0)] == 1
        assert result[(10, 2023, "ptr", 0)] == 2
        assert result[(11, 2023, "annual", 0)] == 3

    def test_amendment_number_differentiates(self):
        rows = [
            _disclosure(1, 10, 2023, "annual", amendment_number=0),
            _disclosure(2, 10, 2023, "annual", amendment_number=1),
        ]
        result = build_disclosure_natural_key_map(rows)
        assert result[(10, 2023, "annual", 0)] == 1
        assert result[(10, 2023, "annual", 1)] == 2

    def test_duplicate_raises(self):
        rows = [
            _disclosure(1, 10, 2023, "annual", 0),
            _disclosure(2, 10, 2023, "annual", 0),
        ]
        with pytest.raises(LookupBuildError) as exc_info:
            build_disclosure_natural_key_map(rows)
        err = exc_info.value
        assert len(err.duplicates) == 1
        key, _ = err.duplicates[0]
        assert key == (10, 2023, "annual", 0)

    def test_missing_filing_year_raises(self):
        rows = [{"id": 1, "member_id": 10, "filing_type": "annual", "amendment_number": 0}]
        with pytest.raises(LookupBuildError):
            build_disclosure_natural_key_map(rows)

    def test_boolean_natural_key_part_raises(self):
        rows = [
            {
                "id": 1,
                "member_id": True,
                "filing_year": 2023,
                "filing_type": "annual",
                "amendment_number": 0,
            }
        ]
        with pytest.raises(LookupBuildError, match="member_id"):
            build_disclosure_natural_key_map(rows)

    def test_empty_returns_empty(self):
        assert build_disclosure_natural_key_map([]) == {}


# ---------------------------------------------------------------------------
# build_disclosure_source_record_id_map
# ---------------------------------------------------------------------------


class TestBuildDisclosureSourceRecordIdMap:
    def test_happy_path_skips_missing_source_record_id(self):
        rows = [
            _disclosure(1, 10, 2023, "annual", source_record_id="FILING-001"),
            _disclosure(2, 10, 2023, "amendment", 1, source_record_id="FILING-002"),
            _disclosure(3, 10, 2023, "ptr"),
        ]

        result = build_disclosure_source_record_id_map(rows)

        assert result == {"FILING-001": 1, "FILING-002": 2}

    def test_duplicate_source_record_id_raises(self):
        rows = [
            _disclosure(1, 10, 2023, "annual", source_record_id="FILING-001"),
            _disclosure(2, 11, 2023, "annual", source_record_id="FILING-001"),
        ]

        with pytest.raises(LookupBuildError, match="duplicate source_record_id"):
            build_disclosure_source_record_id_map(rows)


# ---------------------------------------------------------------------------
# LookupBuildError
# ---------------------------------------------------------------------------


class TestLookupBuildError:
    def test_default_duplicates_empty(self):
        err = LookupBuildError("some message")
        assert err.duplicates == []
        assert "some message" in str(err)

    def test_duplicates_attached(self):
        rows = [{"id": 1}]
        err = LookupBuildError("dup", duplicates=[("key", rows)])
        assert err.duplicates[0][0] == "key"


# ---------------------------------------------------------------------------
# build_lookup_bundle
# ---------------------------------------------------------------------------


class TestBuildLookupBundle:
    def _member_rows(self):
        return [
            _member(1, "B001234", lis_member_id="S001", fec_candidate_id="H0TX01234"),
            _member(2, "B005678", lis_member_id="S002", fec_candidate_id="S6TX00567"),
            _member(3, "B009999"),  # no LIS, no FEC
        ]

    def _committee_rows(self):
        return [
            _committee(10, "SSAF", 119),
            _committee(11, "HFIN", 119),
        ]

    def _fec_committee_rows(self):
        return [
            _fec_committee(100, "C00000001"),
            _fec_committee(101, "C00000002"),
        ]

    def _disclosure_rows(self):
        return [
            _disclosure(20, 1, 2023, "annual"),
            _disclosure(21, 1, 2023, "ptr"),
            _disclosure(22, 2, 2023, "annual"),
        ]

    def test_bundle_builds_without_error(self):
        bundle = build_lookup_bundle(
            member_rows=self._member_rows(),
            committee_rows=self._committee_rows(),
            fec_committee_rows=self._fec_committee_rows(),
            financial_disclosure_rows=self._disclosure_rows(),
        )
        assert isinstance(bundle, LookupBundle)

    def test_bioguide_map_correct(self):
        bundle = build_lookup_bundle(
            member_rows=self._member_rows(),
            committee_rows=self._committee_rows(),
            fec_committee_rows=self._fec_committee_rows(),
            financial_disclosure_rows=self._disclosure_rows(),
        )
        assert bundle.bioguide_map == {"B001234": 1, "B005678": 2, "B009999": 3}

    def test_lis_member_map_excludes_none(self):
        bundle = build_lookup_bundle(
            member_rows=self._member_rows(),
            committee_rows=self._committee_rows(),
            fec_committee_rows=self._fec_committee_rows(),
            financial_disclosure_rows=self._disclosure_rows(),
        )
        assert bundle.lis_member_map == {"S001": 1, "S002": 2}

    def test_fec_candidate_map_excludes_none(self):
        bundle = build_lookup_bundle(
            member_rows=self._member_rows(),
            committee_rows=self._committee_rows(),
            fec_committee_rows=self._fec_committee_rows(),
            financial_disclosure_rows=self._disclosure_rows(),
        )
        assert bundle.fec_candidate_map == {"H0TX01234": 1, "S6TX00567": 2}

    def test_committee_code_map_correct(self):
        bundle = build_lookup_bundle(
            member_rows=self._member_rows(),
            committee_rows=self._committee_rows(),
            fec_committee_rows=self._fec_committee_rows(),
            financial_disclosure_rows=self._disclosure_rows(),
        )
        assert bundle.committee_code_map == {("SSAF", 119): 10, ("HFIN", 119): 11}

    def test_fec_committee_map_correct(self):
        bundle = build_lookup_bundle(
            member_rows=self._member_rows(),
            committee_rows=self._committee_rows(),
            fec_committee_rows=self._fec_committee_rows(),
            financial_disclosure_rows=self._disclosure_rows(),
        )
        assert bundle.fec_committee_map == {"C00000001": 100, "C00000002": 101}

    def test_disclosure_natural_key_map_correct(self):
        bundle = build_lookup_bundle(
            member_rows=self._member_rows(),
            committee_rows=self._committee_rows(),
            fec_committee_rows=self._fec_committee_rows(),
            financial_disclosure_rows=self._disclosure_rows(),
        )
        assert bundle.disclosure_natural_key_map[(1, 2023, "annual", 0)] == 20
        assert bundle.disclosure_natural_key_map[(1, 2023, "ptr", 0)] == 21
        assert bundle.disclosure_natural_key_map[(2, 2023, "annual", 0)] == 22

    def test_disclosure_source_record_id_map_correct(self):
        rows = [
            _disclosure(20, 1, 2023, "annual", source_record_id="FILING-001"),
            _disclosure(21, 1, 2023, "amendment", 1, source_record_id="FILING-002"),
        ]

        bundle = build_lookup_bundle(
            member_rows=self._member_rows(),
            committee_rows=self._committee_rows(),
            fec_committee_rows=self._fec_committee_rows(),
            financial_disclosure_rows=rows,
        )

        assert bundle.disclosure_source_record_id_map == {
            "FILING-001": 20,
            "FILING-002": 21,
        }

    def test_empty_inputs_produce_empty_bundle(self):
        bundle = build_lookup_bundle(
            member_rows=[],
            committee_rows=[],
            fec_committee_rows=[],
            financial_disclosure_rows=[],
        )
        assert bundle.bioguide_map == {}
        assert bundle.lis_member_map == {}
        assert bundle.fec_candidate_map == {}
        assert bundle.committee_code_map == {}
        assert bundle.fec_committee_map == {}
        assert bundle.disclosure_natural_key_map == {}
        assert bundle.disclosure_source_record_id_map == {}


# ---------------------------------------------------------------------------
# LookupBundle.to_maps()
# ---------------------------------------------------------------------------


class TestToMaps:
    def test_to_maps_has_required_keys(self):
        bundle = LookupBundle()
        maps = bundle.to_maps()
        assert "bioguide_map" in maps
        assert "lis_member_map" in maps
        assert "committee_code_map" in maps
        assert "raw_id_maps" in maps
        assert "disclosure_natural_key_map" in maps
        assert "disclosure_source_record_id_map" in maps

    def test_raw_id_maps_has_fec_subkeys(self):
        bundle = LookupBundle(
            fec_candidate_map={"H0TX01234": 1},
            fec_committee_map={"C00000001": 100},
        )
        maps = bundle.to_maps()
        raw = maps["raw_id_maps"]
        assert raw["fec_candidate_id"] == {"H0TX01234": 1}
        assert raw["fec_committee_id"] == {"C00000001": 100}

    def test_to_maps_populated_values_pass_through(self):
        bundle = LookupBundle(
            bioguide_map={"B001234": 1},
            lis_member_map={"S001": 1},
            committee_code_map={("SSAF", 119): 10},
            disclosure_natural_key_map={(1, 2023, "annual", 0): 20},
            disclosure_source_record_id_map={"FILING-001": 20},
        )
        maps = bundle.to_maps()
        assert maps["bioguide_map"] == {"B001234": 1}
        assert maps["lis_member_map"] == {"S001": 1}
        assert maps["committee_code_map"] == {("SSAF", 119): 10}
        assert maps["disclosure_natural_key_map"] == {(1, 2023, "annual", 0): 20}
        assert maps["disclosure_source_record_id_map"] == {"FILING-001": 20}

    def test_to_maps_compatible_with_resolve_foreign_keys(self):
        """Smoke-test that to_maps() output is accepted by resolve_foreign_keys."""
        from src.db.foreign_keys import resolve_foreign_keys

        bundle = LookupBundle(
            bioguide_map={"B001234": 1},
            lis_member_map={"S001": 1},
            committee_code_map={("SSAF", 119): 10},
            fec_committee_map={"C00000001": 100},
            disclosure_natural_key_map={(1, 2023, "annual", 0): 20},
        )
        # hint key is "_bioguide_id" (bare = empty prefix → member_id)
        rows = [
            {"_bioguide_id": "B001234", "amount": 500},
        ]
        result = resolve_foreign_keys(rows, maps=bundle.to_maps())
        assert result.summary.ok
        assert result.rows[0]["member_id"] == 1
        assert result.rows[0]["amount"] == 500
