"""Tests for src.ingest.fec.transform — FEC ingest-to-row transforms.

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
from src.ingest.fec.transform import (
    committee_to_row,
    contribution_to_row,
    linkage_to_row,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_FEC_CID = "C00431445"
VALID_CANDIDATE_ID = "H2TN05209"


def _committee(**overrides) -> CommitteeRecord:
    defaults = dict(
        fec_committee_id=VALID_FEC_CID,
        committee_name="FRIENDS OF JANE DOE",
        treasurer_name="John Treasurer",
        city="Nashville",
        state="TN",
        committee_type="H",
        designation_code="P",
        party="DEM",
        filing_frequency="Q",
    )
    return CommitteeRecord(**{**defaults, **overrides})


def _contribution(**overrides) -> ContributionRecord:
    defaults = dict(
        fec_committee_id=VALID_FEC_CID,
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
    return ContributionRecord(**{**defaults, **overrides})


def _linkage(**overrides) -> CandidateCommitteeLinkage:
    defaults = dict(
        fec_candidate_id=VALID_CANDIDATE_ID,
        fec_committee_id=VALID_FEC_CID,
        linkage_type="P",
        election_year=2024,
    )
    return CandidateCommitteeLinkage(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# committee_to_row
# ---------------------------------------------------------------------------


class TestCommitteeToRow:
    def test_canonical_keys_present(self):
        row = committee_to_row(_committee())
        assert set(row) >= {
            "fec_committee_id",
            "committee_name",
            "committee_type",
            "designation_code",
            "treasurer_name",
            "city",
            "state",
        }

    def test_no_provenance_keys(self):
        row = committee_to_row(_committee())
        assert "source_artifact_id" not in row
        assert "id" not in row

    def test_committee_id_normalized(self):
        row = committee_to_row(_committee(fec_committee_id="  c00431445 "))
        assert row["fec_committee_id"] == "C00431445"

    def test_state_upcased(self):
        row = committee_to_row(_committee(state="tn"))
        assert row["state"] == "TN"

    def test_state_invalid_length_becomes_none(self):
        row = committee_to_row(_committee(state="TNN"))
        assert row["state"] is None

    def test_none_optional_fields(self):
        row = committee_to_row(
            _committee(
                treasurer_name=None,
                city=None,
                state=None,
                committee_type=None,
                designation_code=None,
            )
        )
        assert row["treasurer_name"] is None
        assert row["city"] is None
        assert row["state"] is None
        assert row["committee_type"] is None
        assert row["designation_code"] is None

    def test_committee_name_stripped(self):
        row = committee_to_row(_committee(committee_name="  FRIENDS OF JANE  "))
        assert row["committee_name"] == "FRIENDS OF JANE"

    def test_invalid_committee_id_raises(self):
        with pytest.raises(ValueError, match="Invalid fec_committee_id"):
            committee_to_row(_committee(fec_committee_id="BADID"))

    def test_empty_committee_id_raises(self):
        with pytest.raises(ValueError, match="Invalid fec_committee_id"):
            committee_to_row(_committee(fec_committee_id=""))


# ---------------------------------------------------------------------------
# contribution_to_row
# ---------------------------------------------------------------------------


class TestContributionToRow:
    def test_canonical_keys_present(self):
        row = contribution_to_row(_contribution())
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

    def test_recipient_committee_id_normalized(self):
        row = contribution_to_row(_contribution(fec_committee_id="  c00431445 "))
        assert row["recipient_fec_committee_id_raw"] == "C00431445"

    def test_donor_name_normalized(self):
        row = contribution_to_row(_contribution(donor_name="DOE, JOHN MICHAEL JR"))
        assert row["donor_name"] == "JOHN DOE"

    def test_sub_id_becomes_source_record_id(self):
        row = contribution_to_row(_contribution(sub_id="999"))
        assert row["source_record_id"] == "999"

    def test_source_record_id_none(self):
        row = contribution_to_row(_contribution(sub_id=None))
        assert row["source_record_id"] is None

    # --- donor_type mapping ---

    def test_donor_type_individual(self):
        row = contribution_to_row(_contribution(entity_type="IND"))
        assert row["donor_type"] == "individual"

    def test_donor_type_committee(self):
        row = contribution_to_row(_contribution(entity_type="COM"))
        assert row["donor_type"] == "committee"

    def test_donor_type_organization(self):
        row = contribution_to_row(_contribution(entity_type="ORG"))
        assert row["donor_type"] == "organization"

    def test_donor_type_unknown_becomes_other(self):
        row = contribution_to_row(_contribution(entity_type="XYZ"))
        assert row["donor_type"] == "other"

    def test_donor_type_none_becomes_other(self):
        row = contribution_to_row(_contribution(entity_type=None))
        assert row["donor_type"] == "other"

    # --- contribution_type mapping ---

    def test_contribution_type_direct(self):
        for code in ("10", "20", "30"):
            row = contribution_to_row(_contribution(transaction_type=code))
            assert row["contribution_type"] == "contribution", code

    def test_contribution_type_refund(self):
        row = contribution_to_row(_contribution(transaction_type="24A"))
        assert row["contribution_type"] == "refund"

    def test_contribution_type_transfer(self):
        row = contribution_to_row(_contribution(transaction_type="24K"))
        assert row["contribution_type"] == "transfer"

    def test_contribution_type_in_kind(self):
        row = contribution_to_row(_contribution(transaction_type="21Y"))
        assert row["contribution_type"] == "in_kind"

    def test_contribution_type_unknown_becomes_other(self):
        row = contribution_to_row(_contribution(transaction_type="99Z"))
        assert row["contribution_type"] == "other"

    def test_contribution_type_none_becomes_other(self):
        row = contribution_to_row(_contribution(transaction_type=None))
        assert row["contribution_type"] == "other"

    # --- date and amount ---

    def test_contribution_date_preserved(self):
        d = datetime.date(2024, 11, 5)
        row = contribution_to_row(_contribution(contribution_date=d))
        assert row["contribution_date"] == d

    def test_none_date_raises(self):
        with pytest.raises(ValueError, match="contribution_date is required"):
            contribution_to_row(_contribution(contribution_date=None))

    def test_amount_preserved_as_decimal(self):
        row = contribution_to_row(_contribution(amount=Decimal("2500.00")))
        assert row["amount"] == Decimal("2500.00")

    def test_memo_text_preserved(self):
        row = contribution_to_row(_contribution(memo_text="earmarked for primary"))
        assert row["memo"] == "earmarked for primary"

    def test_memo_none(self):
        row = contribution_to_row(_contribution(memo_text=None))
        assert row["memo"] is None

    # --- supplemental donor fields ---

    def test_employer_normalized_in_supplemental(self):
        row = contribution_to_row(_contribution(employer="ACME INC"))
        assert row["donor_employer"] == "ACME"

    def test_occupation_normalized_in_supplemental(self):
        row = contribution_to_row(_contribution(occupation="ATTORNEY"))
        assert row["donor_occupation"] == "ATTORNEY"

    def test_occupation_generic_becomes_empty(self):
        row = contribution_to_row(_contribution(occupation="RETIRED"))
        assert row["donor_occupation"] == ""

    def test_donor_state_cleaned(self):
        row = contribution_to_row(_contribution(state="tn"))
        assert row["donor_state"] == "TN"

    def test_donor_state_invalid_becomes_none(self):
        row = contribution_to_row(_contribution(state="TNN"))
        assert row["donor_state"] is None

    def test_invalid_committee_id_raises(self):
        with pytest.raises(ValueError, match="Invalid recipient fec_committee_id"):
            contribution_to_row(_contribution(fec_committee_id="BAD"))


# ---------------------------------------------------------------------------
# linkage_to_row
# ---------------------------------------------------------------------------


class TestLinkageToRow:
    def test_canonical_keys_present(self):
        row = linkage_to_row(_linkage())
        assert set(row) == {
            "fec_candidate_id",
            "fec_committee_id",
            "linkage_type",
            "election_year",
        }

    def test_committee_id_normalized(self):
        row = linkage_to_row(_linkage(fec_committee_id="  c00431445 "))
        assert row["fec_committee_id"] == "C00431445"

    def test_candidate_id_upper_stripped(self):
        row = linkage_to_row(_linkage(fec_candidate_id="  h2tn05209  "))
        assert row["fec_candidate_id"] == "H2TN05209"

    def test_linkage_type_upper(self):
        row = linkage_to_row(_linkage(linkage_type="p"))
        assert row["linkage_type"] == "P"

    def test_election_year_preserved(self):
        row = linkage_to_row(_linkage(election_year=2024))
        assert row["election_year"] == 2024

    def test_election_year_none(self):
        row = linkage_to_row(_linkage(election_year=None))
        assert row["election_year"] is None

    def test_no_member_resolution_keys(self):
        # The transform must not resolve bioguide_id or any DB-state field.
        row = linkage_to_row(_linkage())
        assert "bioguide_id" not in row
        assert "member_id" not in row

    def test_invalid_committee_id_raises(self):
        with pytest.raises(ValueError, match="Invalid fec_committee_id"):
            linkage_to_row(_linkage(fec_committee_id="NOTVALID"))

    def test_empty_candidate_id_raises(self):
        with pytest.raises(ValueError, match="fec_candidate_id is required"):
            linkage_to_row(_linkage(fec_candidate_id=""))

    def test_none_candidate_id_raises(self):
        with pytest.raises(ValueError, match="fec_candidate_id is required"):
            linkage_to_row(_linkage(fec_candidate_id=None))
