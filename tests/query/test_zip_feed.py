"""Tests for src/query/zip_feed.py.

Pure unit tests — no DB, no network, no I/O.
"""

from __future__ import annotations

import datetime as dt

from src.export.contracts import ZipFeedPayload
from src.query.zip_feed import _format_district, assemble_zip_feed
from src.zip.resolve import (
    DistrictMemberRow,
    FederalBundle,
    SenatorRow,
    ZipDistrictRow,
    assemble_federal_bundle,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SNAPSHOT = dt.date(2025, 1, 15)

# ZIP that maps cleanly to one district
ZIP_SINGLE = "90001"
ZIP_MULTI = "90210"

DISTRICT_ROWS_SINGLE = [
    ZipDistrictRow(zip5=ZIP_SINGLE, state="CA", district=33, population_share=1.0),
]

DISTRICT_ROWS_MULTI = [
    ZipDistrictRow(zip5=ZIP_MULTI, state="CA", district=33, population_share=0.6),
    ZipDistrictRow(zip5=ZIP_MULTI, state="CA", district=36, population_share=0.4),
]

HOUSE_MEMBER_ROW = DistrictMemberRow(
    state="CA",
    district=33,
    bioguide_id="H000001",
    full_name="House Rep One",
    party="D",
    slug="house-rep-one-h000001",
)

SENATOR_ROWS = [
    SenatorRow(
        state="CA",
        bioguide_id="S000001",
        full_name="Senator One",
        party="D",
        slug="senator-one-s000001",
        seat=1,
    ),
    SenatorRow(
        state="CA",
        bioguide_id="S000002",
        full_name="Senator Two",
        party="R",
        slug="senator-two-s000002",
        seat=2,
    ),
]

SCORE_ROWS: list[dict] = [
    {
        "bioguide_id": "H000001",
        "dimension": "conflict_of_interest_risk",
        "current_score": -3.5,
        "rule_fire_count": 2,
    },
    {
        "bioguide_id": "S000001",
        "dimension": "conflict_of_interest_risk",
        "current_score": -1.0,
        "rule_fire_count": 1,
    },
    {
        "bioguide_id": "S000002",
        "dimension": "conflict_of_interest_risk",
        "current_score": 0.0,
        "rule_fire_count": 0,
    },
]

EVIDENCE_IDS: dict[str, list[str]] = {
    "H000001": ["ec-h1", "ec-h2", "ec-h3", "ec-h4"],
    "S000001": ["ec-s1"],
    "S000002": [],
}


def _make_bundle(zip5: str, district_rows: list[ZipDistrictRow]) -> FederalBundle:
    result = assemble_federal_bundle(
        zip5=zip5,
        zip_district_rows=district_rows,
        district_member_rows=[HOUSE_MEMBER_ROW],
        senator_rows=SENATOR_ROWS,
    )
    assert result is not None
    return result


# ---------------------------------------------------------------------------
# _format_district
# ---------------------------------------------------------------------------


class TestFormatDistrict:
    def test_standard_district(self):
        assert _format_district("CA", 33) == "CA-33"

    def test_at_large_district(self):
        assert _format_district("AK", 0) == "AK-00"

    def test_single_digit_padded(self):
        assert _format_district("TX", 5) == "TX-05"

    def test_two_digit_district(self):
        assert _format_district("NY", 12) == "NY-12"


# ---------------------------------------------------------------------------
# assemble_zip_feed — basic structure
# ---------------------------------------------------------------------------


class TestAssembleZipFeedBasic:
    def test_returns_zip_feed_payload(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert isinstance(payload, ZipFeedPayload)

    def test_zip_code_preserved(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert payload.zip_code == ZIP_SINGLE

    def test_snapshot_date_preserved(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert payload.snapshot_date == SNAPSHOT

    def test_congressional_district_formatted(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert payload.congressional_district == "CA-33"

    def test_three_members_returned(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert len(payload.members) == 3

    def test_house_member_is_first(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert payload.members[0].bioguide_id == "H000001"
        assert payload.members[0].chamber == "house"

    def test_senators_follow_house_member(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        senator_bioguides = [m.bioguide_id for m in payload.members[1:]]
        assert "S000001" in senator_bioguides
        assert "S000002" in senator_bioguides

    def test_senators_in_seat_order(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert payload.members[1].bioguide_id == "S000001"
        assert payload.members[2].bioguide_id == "S000002"

    def test_member_fields_populated(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        house = payload.members[0]
        assert house.name == "House Rep One"
        assert house.slug == "house-rep-one-h000001"
        assert house.party == "D"


# ---------------------------------------------------------------------------
# Scores and evidence card IDs
# ---------------------------------------------------------------------------


class TestScoresAndEvidence:
    def _payload(self) -> ZipFeedPayload:
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        return assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)

    def test_scores_attached_to_correct_member(self):
        payload = self._payload()
        house = payload.members[0]
        assert len(house.scores) == 1
        assert house.scores[0].dimension == "conflict_of_interest_risk"
        assert house.scores[0].current_score == -3.5
        assert house.scores[0].rule_fire_count == 2

    def test_evidence_card_ids_capped_at_three(self):
        payload = self._payload()
        house = payload.members[0]
        assert house.top_evidence_card_ids == ["ec-h1", "ec-h2", "ec-h3"]

    def test_evidence_card_ids_partial_list_preserved(self):
        payload = self._payload()
        sen1 = payload.members[1]
        assert sen1.top_evidence_card_ids == ["ec-s1"]

    def test_member_with_no_evidence_gets_empty_list(self):
        payload = self._payload()
        sen2 = payload.members[2]
        assert sen2.top_evidence_card_ids == []

    def test_member_with_no_score_rows_gets_empty_scores(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, [], {}, SNAPSHOT)
        for member in payload.members:
            assert member.scores == []

    def test_multiple_score_dimensions_per_member(self):
        extra_rows = SCORE_ROWS + [
            {
                "bioguide_id": "H000001",
                "dimension": "late_disclosure_risk",
                "current_score": -1.0,
                "rule_fire_count": 1,
            }
        ]
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, extra_rows, EVIDENCE_IDS, SNAPSHOT)
        house = payload.members[0]
        assert len(house.scores) == 2
        dimensions = {s.dimension for s in house.scores}
        assert "conflict_of_interest_risk" in dimensions
        assert "late_disclosure_risk" in dimensions

    def test_score_rows_for_other_members_not_leaked(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        sen2 = payload.members[2]
        # S000002 has one score row with rule_fire_count=0
        assert len(sen2.scores) == 1
        assert sen2.scores[0].rule_fire_count == 0


# ---------------------------------------------------------------------------
# Ambiguity note (plurality-district behaviour)
# ---------------------------------------------------------------------------


class TestAmbiguityNote:
    def test_no_ambiguity_note_for_single_district_zip(self):
        bundle = _make_bundle(ZIP_SINGLE, DISTRICT_ROWS_SINGLE)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert payload.ambiguity_note is None

    def test_ambiguity_note_set_for_multi_district_zip(self):
        bundle = _make_bundle(ZIP_MULTI, DISTRICT_ROWS_MULTI)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert payload.ambiguity_note is not None

    def test_ambiguity_note_mentions_zip(self):
        bundle = _make_bundle(ZIP_MULTI, DISTRICT_ROWS_MULTI)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert ZIP_MULTI in payload.ambiguity_note

    def test_ambiguity_note_passed_through_verbatim(self):
        bundle = _make_bundle(ZIP_MULTI, DISTRICT_ROWS_MULTI)
        expected_note = bundle.plurality_district.ambiguity_note
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert payload.ambiguity_note == expected_note

    def test_plurality_district_is_highest_share(self):
        bundle = _make_bundle(ZIP_MULTI, DISTRICT_ROWS_MULTI)
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        # CA-33 has 60% share, CA-36 has 40% — plurality is CA-33
        assert payload.congressional_district == "CA-33"


# ---------------------------------------------------------------------------
# Missing house member (data gap)
# ---------------------------------------------------------------------------


class TestMissingHouseMember:
    def test_only_senators_when_house_member_absent(self):
        # Supply no district member rows so house_member resolves to None
        bundle = assemble_federal_bundle(
            zip5=ZIP_SINGLE,
            zip_district_rows=DISTRICT_ROWS_SINGLE,
            district_member_rows=[],  # no match
            senator_rows=SENATOR_ROWS,
        )
        assert bundle is not None
        assert bundle.house_member is None

        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert len(payload.members) == 2
        chambers = {m.chamber for m in payload.members}
        assert chambers == {"senate"}

    def test_zero_senators_when_none_available(self):
        bundle = assemble_federal_bundle(
            zip5=ZIP_SINGLE,
            zip_district_rows=DISTRICT_ROWS_SINGLE,
            district_member_rows=[HOUSE_MEMBER_ROW],
            senator_rows=[],
        )
        assert bundle is not None
        payload = assemble_zip_feed(bundle, SCORE_ROWS, EVIDENCE_IDS, SNAPSHOT)
        assert len(payload.members) == 1
        assert payload.members[0].chamber == "house"

    def test_empty_members_when_no_members_resolved(self):
        bundle = assemble_federal_bundle(
            zip5=ZIP_SINGLE,
            zip_district_rows=DISTRICT_ROWS_SINGLE,
            district_member_rows=[],
            senator_rows=[],
        )
        assert bundle is not None
        payload = assemble_zip_feed(bundle, [], {}, SNAPSHOT)
        assert payload.members == []
