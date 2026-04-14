"""Tests for src.load.fec — FEC canonical load-plan layer.

All tests are pure (no I/O, no DB, no network).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from src.ingest.fec.models import (
    CandidateCommitteeLinkage,
    CommitteeRecord,
    ContributionRecord,
)
from src.load.fec import (
    plan_contributions,
    plan_fec_committees,
    plan_fec_load,
    plan_linkage_hints,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_CID = "C00431445"
VALID_CID_2 = "C00000001"
VALID_CANDIDATE_ID = "H2TN05209"


def _committee(**kw) -> CommitteeRecord:
    defaults = dict(
        fec_committee_id=VALID_CID,
        committee_name="FRIENDS OF JANE DOE",
        treasurer_name="John Treasurer",
        city="Nashville",
        state="TN",
        committee_type="H",
        designation_code="P",
    )
    return CommitteeRecord(**{**defaults, **kw})


def _contribution(**kw) -> ContributionRecord:
    defaults = dict(
        fec_committee_id=VALID_CID,
        donor_name="DOE, JOHN",
        city="Memphis",
        state="TN",
        zip_code="38101",
        employer="ACME INC",
        occupation="ATTORNEY",
        contribution_date=datetime.date(2024, 3, 15),
        amount=Decimal("500.00"),
        transaction_type="10",
        memo_text=None,
        sub_id="123456789012345",
        entity_type="IND",
    )
    return ContributionRecord(**{**defaults, **kw})


def _linkage(**kw) -> CandidateCommitteeLinkage:
    defaults = dict(
        fec_candidate_id=VALID_CANDIDATE_ID,
        fec_committee_id=VALID_CID,
        linkage_type="P",
        election_year=2024,
    )
    return CandidateCommitteeLinkage(**{**defaults, **kw})


# ---------------------------------------------------------------------------
# plan_fec_committees
# ---------------------------------------------------------------------------


class TestPlanFecCommittees:
    def test_op_dict_shape(self):
        op = plan_fec_committees([])
        assert set(op.keys()) == {"table", "rows", "conflict_columns", "mode"}

    def test_table_name(self):
        assert plan_fec_committees([])["table"] == "fec_committee"

    def test_conflict_columns(self):
        assert plan_fec_committees([])["conflict_columns"] == ["fec_committee_id"]

    def test_mode_upsert(self):
        assert plan_fec_committees([])["mode"] == "upsert"

    def test_empty_records_empty_rows(self):
        assert plan_fec_committees([])["rows"] == []

    def test_row_count_matches_input(self):
        records = [_committee(), _committee(fec_committee_id=VALID_CID_2)]
        assert len(plan_fec_committees(records)["rows"]) == 2

    def test_required_row_keys_present(self):
        row = plan_fec_committees([_committee()])["rows"][0]
        assert set(row) >= {
            "fec_committee_id",
            "committee_name",
            "committee_type",
            "designation_code",
            "treasurer_name",
            "city",
            "state",
        }

    def test_official_id_preserved(self):
        op = plan_fec_committees([_committee(fec_committee_id="  c00431445 ")])
        assert op["rows"][0]["fec_committee_id"] == VALID_CID

    def test_no_provenance_keys_in_rows(self):
        row = plan_fec_committees([_committee()])["rows"][0]
        assert "source_artifact_id" not in row
        assert "id" not in row

    def test_invalid_committee_id_raises(self):
        with pytest.raises(ValueError, match="Invalid fec_committee_id"):
            plan_fec_committees([_committee(fec_committee_id="BADID")])

    def test_empty_committee_id_raises(self):
        with pytest.raises(ValueError, match="Invalid fec_committee_id"):
            plan_fec_committees([_committee(fec_committee_id="")])

    def test_none_optional_fields_allowed(self):
        op = plan_fec_committees([_committee(
            treasurer_name=None, city=None, state=None,
            committee_type=None, designation_code=None,
        )])
        row = op["rows"][0]
        assert row["treasurer_name"] is None
        assert row["city"] is None
        assert row["state"] is None

    def test_multiple_rows_all_transformed(self):
        records = [
            _committee(committee_name="COMMITTEE A"),
            _committee(fec_committee_id=VALID_CID_2, committee_name="COMMITTEE B"),
        ]
        rows = plan_fec_committees(records)["rows"]
        names = {r["committee_name"] for r in rows}
        assert names == {"COMMITTEE A", "COMMITTEE B"}


# ---------------------------------------------------------------------------
# plan_contributions
# ---------------------------------------------------------------------------


class TestPlanContributions:
    def test_op_dict_shape(self):
        op = plan_contributions([])
        assert set(op.keys()) == {"table", "rows", "conflict_columns", "mode"}

    def test_table_name(self):
        assert plan_contributions([])["table"] == "contribution"

    def test_conflict_columns(self):
        assert plan_contributions([])["conflict_columns"] == ["source_record_id"]

    def test_mode_upsert(self):
        assert plan_contributions([])["mode"] == "upsert"

    def test_empty_records_empty_rows(self):
        assert plan_contributions([])["rows"] == []

    def test_row_count_matches_input(self):
        records = [_contribution(sub_id="1"), _contribution(sub_id="2")]
        assert len(plan_contributions(records)["rows"]) == 2

    def test_required_row_keys_present(self):
        row = plan_contributions([_contribution()])["rows"][0]
        assert set(row) >= {
            "recipient_fec_committee_id_raw",
            "source_record_id",
            "donor_name",
            "donor_type",
            "contribution_type",
            "contribution_date",
            "amount",
            "memo",
        }

    def test_fk_left_as_raw_string(self):
        # The load plan must NOT resolve the FK to a DB bigint.
        row = plan_contributions([_contribution()])["rows"][0]
        assert "recipient_fec_committee_id_raw" in row
        assert "recipient_fec_committee_id" not in row

    def test_sub_id_becomes_source_record_id(self):
        row = plan_contributions([_contribution(sub_id="999")])["rows"][0]
        assert row["source_record_id"] == "999"

    def test_contribution_date_preserved(self):
        d = datetime.date(2024, 11, 5)
        row = plan_contributions([_contribution(contribution_date=d)])["rows"][0]
        assert row["contribution_date"] == d

    def test_amount_preserved_as_decimal(self):
        row = plan_contributions([_contribution(amount=Decimal("2500.00"))])["rows"][0]
        assert row["amount"] == Decimal("2500.00")

    def test_none_date_propagates_error(self):
        with pytest.raises(ValueError, match="contribution_date is required"):
            plan_contributions([_contribution(contribution_date=None)])

    def test_invalid_committee_id_propagates_error(self):
        with pytest.raises(ValueError, match="Invalid recipient fec_committee_id"):
            plan_contributions([_contribution(fec_committee_id="BAD")])

    def test_donor_name_normalized(self):
        row = plan_contributions([_contribution(donor_name="DOE, JOHN MICHAEL JR")])["rows"][0]
        assert row["donor_name"] == "JOHN DOE"

    def test_donor_type_individual(self):
        row = plan_contributions([_contribution(entity_type="IND")])["rows"][0]
        assert row["donor_type"] == "individual"

    def test_contribution_type_direct(self):
        row = plan_contributions([_contribution(transaction_type="10")])["rows"][0]
        assert row["contribution_type"] == "contribution"


# ---------------------------------------------------------------------------
# plan_linkage_hints
# ---------------------------------------------------------------------------


class TestPlanLinkageHints:
    def test_op_dict_shape(self):
        op = plan_linkage_hints([])
        assert set(op.keys()) == {"table", "rows", "conflict_columns", "mode"}

    def test_table_sentinel(self):
        assert plan_linkage_hints([])["table"] == "_fec_linkage_hint"

    def test_conflict_columns(self):
        assert plan_linkage_hints([])["conflict_columns"] == [
            "fec_candidate_id",
            "fec_committee_id",
        ]

    def test_mode_hints(self):
        assert plan_linkage_hints([])["mode"] == "hints"

    def test_empty_records_empty_rows(self):
        assert plan_linkage_hints([])["rows"] == []

    def test_row_count_matches_input(self):
        records = [_linkage(), _linkage(election_year=2022)]
        assert len(plan_linkage_hints(records)["rows"]) == 2

    def test_required_row_keys_present(self):
        row = plan_linkage_hints([_linkage()])["rows"][0]
        assert set(row) >= {
            "fec_candidate_id",
            "fec_committee_id",
            "linkage_type",
            "election_year",
        }

    def test_no_member_resolution_keys(self):
        row = plan_linkage_hints([_linkage()])["rows"][0]
        assert "bioguide_id" not in row
        assert "member_id" not in row

    def test_official_candidate_id_preserved(self):
        row = plan_linkage_hints([_linkage()])["rows"][0]
        assert row["fec_candidate_id"] == VALID_CANDIDATE_ID

    def test_official_committee_id_preserved(self):
        row = plan_linkage_hints([_linkage()])["rows"][0]
        assert row["fec_committee_id"] == VALID_CID

    def test_committee_id_normalized(self):
        op = plan_linkage_hints([_linkage(fec_committee_id="  c00431445 ")])
        assert op["rows"][0]["fec_committee_id"] == VALID_CID

    def test_election_year_none_preserved(self):
        row = plan_linkage_hints([_linkage(election_year=None)])["rows"][0]
        assert row["election_year"] is None

    def test_invalid_committee_id_raises(self):
        with pytest.raises(ValueError, match="Invalid fec_committee_id"):
            plan_linkage_hints([_linkage(fec_committee_id="NOTVALID")])

    def test_empty_candidate_id_raises(self):
        with pytest.raises(ValueError, match="fec_candidate_id is required"):
            plan_linkage_hints([_linkage(fec_candidate_id="")])


# ---------------------------------------------------------------------------
# plan_fec_load
# ---------------------------------------------------------------------------


class TestPlanFecLoad:
    def test_returns_list(self):
        assert isinstance(plan_fec_load([], [], []), list)

    def test_returns_three_operations(self):
        assert len(plan_fec_load([], [], [])) == 3

    def test_order_fec_committee_first(self):
        assert plan_fec_load([], [], [])[0]["table"] == "fec_committee"

    def test_order_contribution_second(self):
        assert plan_fec_load([], [], [])[1]["table"] == "contribution"

    def test_order_linkage_hints_last(self):
        assert plan_fec_load([], [], [])[2]["table"] == "_fec_linkage_hint"

    def test_each_op_has_required_keys(self):
        for op in plan_fec_load([], [], []):
            assert set(op.keys()) == {"table", "rows", "conflict_columns", "mode"}

    def test_full_batch_row_counts(self):
        result = plan_fec_load([_committee()], [_contribution()], [_linkage()])
        assert len(result[0]["rows"]) == 1
        assert len(result[1]["rows"]) == 1
        assert len(result[2]["rows"]) == 1

    def test_multiple_committees_and_contributions(self):
        committees = [_committee(), _committee(fec_committee_id=VALID_CID_2)]
        contributions = [
            _contribution(sub_id="1"),
            _contribution(sub_id="2"),
            _contribution(sub_id="3"),
        ]
        result = plan_fec_load(committees, contributions, [])
        assert len(result[0]["rows"]) == 2
        assert len(result[1]["rows"]) == 3
        assert len(result[2]["rows"]) == 0

    def test_empty_batch_all_empty_rows(self):
        result = plan_fec_load([], [], [])
        for op in result:
            assert op["rows"] == []

    def test_committee_mode_upsert(self):
        assert plan_fec_load([], [], [])[0]["mode"] == "upsert"

    def test_contribution_mode_upsert(self):
        assert plan_fec_load([], [], [])[1]["mode"] == "upsert"

    def test_linkage_mode_hints(self):
        assert plan_fec_load([], [], [])[2]["mode"] == "hints"
