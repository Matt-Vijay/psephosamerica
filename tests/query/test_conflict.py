"""Tests for src/query/conflict.py.

Pure unit tests — no DB, no network, no I/O.
All four launch rule families are covered.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.query.conflict import (
    ConflictBundle,
    assemble_committee_sector_trade_bundle,
    assemble_late_or_amended_disclosure_bundle,
    assemble_repeated_committee_linked_trading_bundle,
    assemble_sector_holdings_overlap_bundle,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

BIOGUIDE = "A000001"
DISC_ID = "disc-101"
MEMB_ID = "memb-55"
COMMITTEE_MEMBERSHIP_URL = "https://api.congress.gov/v3/committee/house/HSEN?format=json"

BASE_COMMITTEE_ROW: dict = {
    "member_bioguide_id": BIOGUIDE,
    "financial_disclosure_id": DISC_ID,
    "committee_membership_id": MEMB_ID,
    "committee_name": "Committee on Energy",
    "committee_sector": "energy",
    "committee_start_date": dt.date(2022, 1, 3),
    "committee_end_date": dt.date(2023, 1, 3),
    "holding_sector": "energy",
    "sector_name": "Energy & Natural Resources",
    "disclosure_period_start": dt.date(2022, 1, 1),
    "disclosure_period_end": dt.date(2022, 12, 31),
    "committee_membership_source_url": COMMITTEE_MEMBERSHIP_URL,
}


# ---------------------------------------------------------------------------
# committee_sector_trade
# ---------------------------------------------------------------------------


class TestCommitteeSectorTrade:
    def test_returns_conflict_bundle(self):
        bundle = assemble_committee_sector_trade_bundle(BASE_COMMITTEE_ROW)
        assert isinstance(bundle, ConflictBundle)

    def test_member_bioguide_id_forwarded(self):
        bundle = assemble_committee_sector_trade_bundle(BASE_COMMITTEE_ROW)
        assert bundle.member_bioguide_id == BIOGUIDE

    def test_no_superseded_filing(self):
        bundle = assemble_committee_sector_trade_bundle(BASE_COMMITTEE_ROW)
        assert bundle.superseded_filing_id is None

    def test_two_source_anchors(self):
        bundle = assemble_committee_sector_trade_bundle(BASE_COMMITTEE_ROW)
        assert len(bundle.source_anchors) == 2

    def test_source_anchor_types(self):
        bundle = assemble_committee_sector_trade_bundle(BASE_COMMITTEE_ROW)
        types = {a.source_type for a in bundle.source_anchors}
        assert types == {"financial_disclosure", "committee_membership"}

    def test_disclosure_anchor_source_id(self):
        bundle = assemble_committee_sector_trade_bundle(BASE_COMMITTEE_ROW)
        disc = next(a for a in bundle.source_anchors if a.source_type == "financial_disclosure")
        assert disc.source_id == DISC_ID

    def test_disclosure_anchor_uses_source_url(self):
        row = {
            **BASE_COMMITTEE_ROW,
            "source_url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC.pdf",
        }
        bundle = assemble_committee_sector_trade_bundle(row)
        disc = next(a for a in bundle.source_anchors if a.source_type == "financial_disclosure")
        assert disc.url == row["source_url"]

    def test_committee_anchor_source_id(self):
        bundle = assemble_committee_sector_trade_bundle(BASE_COMMITTEE_ROW)
        mem = next(a for a in bundle.source_anchors if a.source_type == "committee_membership")
        assert mem.source_id == MEMB_ID

    def test_committee_anchor_uses_source_url(self):
        bundle = assemble_committee_sector_trade_bundle(BASE_COMMITTEE_ROW)
        mem = next(a for a in bundle.source_anchors if a.source_type == "committee_membership")
        assert mem.url == COMMITTEE_MEMBERSHIP_URL

    def test_context_contains_derived_overlap(self):
        bundle = assemble_committee_sector_trade_bundle(BASE_COMMITTEE_ROW)
        assert "committee_service_overlap_days" in bundle.context
        assert bundle.context["committee_service_overlap_days"] >= 0

    def test_context_sector_fields_present(self):
        bundle = assemble_committee_sector_trade_bundle(BASE_COMMITTEE_ROW)
        assert bundle.context["committee_sector"] == "energy"
        assert bundle.context["holding_sector"] == "energy"

    def test_committee_anchor_label_includes_name(self):
        bundle = assemble_committee_sector_trade_bundle(BASE_COMMITTEE_ROW)
        mem = next(a for a in bundle.source_anchors if a.source_type == "committee_membership")
        assert "Committee on Energy" in mem.label

    def test_open_ended_committee_end(self):
        row = {**BASE_COMMITTEE_ROW, "committee_end_date": None}
        bundle = assemble_committee_sector_trade_bundle(row)
        assert bundle.context["committee_service_overlap_days"] >= 0

    def test_both_open_ranges_require_snapshot_or_reference_date(self):
        row = {
            **BASE_COMMITTEE_ROW,
            "committee_end_date": None,
            "disclosure_period_end": None,
        }
        with pytest.raises(ValueError, match="reference_date"):
            assemble_committee_sector_trade_bundle(row)

    def test_snapshot_date_anchors_both_open_ranges(self):
        row = {
            **BASE_COMMITTEE_ROW,
            "committee_end_date": None,
            "disclosure_period_end": None,
            "snapshot_date": dt.date(2022, 6, 30),
        }
        bundle = assemble_committee_sector_trade_bundle(row)
        assert bundle.context["overlap_end"] == dt.date(2022, 6, 30)
        assert bundle.context["committee_service_overlap_days"] == 179
        assert bundle.context["holding_overlap_days"] == 179


# ---------------------------------------------------------------------------
# repeated_committee_linked_trading
# ---------------------------------------------------------------------------

TRANSACTIONS = [
    {
        "financial_disclosure_id": DISC_ID,
        "transaction_id": "tx-1",
        "transaction_date": dt.date(2022, 3, 10),
        "sector": "energy",
    },
    {
        "financial_disclosure_id": "disc-102",
        "transaction_id": "tx-2",
        "transaction_date": dt.date(2022, 6, 15),
        "sector": "energy",
    },
    {
        "financial_disclosure_id": "disc-102",
        "transaction_id": "tx-3",
        "transaction_date": dt.date(2022, 6, 15),
        "sector": "energy",
    },  # same day
    {"transaction_date": dt.date(2022, 9, 1), "sector": "healthcare"},  # different sector
]

TRADING_ROW: dict = {
    "member_bioguide_id": BIOGUIDE,
    "financial_disclosure_id": DISC_ID,
    "committee_membership_id": MEMB_ID,
    "committee_name": "Committee on Energy",
    "committee_sector": "energy",
    "committee_start_date": dt.date(2022, 1, 3),
    "committee_end_date": dt.date(2023, 1, 3),
    "transactions": TRANSACTIONS,
    "committee_membership_source_url": COMMITTEE_MEMBERSHIP_URL,
}


class TestRepeatedCommitteeLinkedTrading:
    def test_returns_conflict_bundle(self):
        bundle = assemble_repeated_committee_linked_trading_bundle(TRADING_ROW)
        assert isinstance(bundle, ConflictBundle)

    def test_member_bioguide_id_forwarded(self):
        bundle = assemble_repeated_committee_linked_trading_bundle(TRADING_ROW)
        assert bundle.member_bioguide_id == BIOGUIDE

    def test_no_superseded_filing(self):
        bundle = assemble_repeated_committee_linked_trading_bundle(TRADING_ROW)
        assert bundle.superseded_filing_id is None

    def test_source_anchors_include_distinct_disclosures_and_committee(self):
        bundle = assemble_repeated_committee_linked_trading_bundle(TRADING_ROW)
        assert len(bundle.source_anchors) == 3

    def test_matching_transaction_count(self):
        bundle = assemble_repeated_committee_linked_trading_bundle(TRADING_ROW)
        # 3 energy transactions within committee service window
        assert bundle.context["matching_transaction_count"] == 3

    def test_distinct_trade_days(self):
        bundle = assemble_repeated_committee_linked_trading_bundle(TRADING_ROW)
        # 2022-03-10 and 2022-06-15 → 2 distinct days
        assert bundle.context["distinct_trade_days"] == 2

    def test_service_overlap_days_positive(self):
        bundle = assemble_repeated_committee_linked_trading_bundle(TRADING_ROW)
        assert bundle.context["service_overlap_days"] > 0

    def test_empty_transactions(self):
        row = {**TRADING_ROW, "transactions": []}
        bundle = assemble_repeated_committee_linked_trading_bundle(row)
        assert bundle.context["matching_transaction_count"] == 0
        assert bundle.context["distinct_trade_days"] == 0

    def test_missing_sector_yields_zero_counts(self):
        row = {**TRADING_ROW, "committee_sector": None}
        bundle = assemble_repeated_committee_linked_trading_bundle(row)
        assert bundle.context["matching_transaction_count"] == 0

    def test_source_anchor_types(self):
        bundle = assemble_repeated_committee_linked_trading_bundle(TRADING_ROW)
        types = {a.source_type for a in bundle.source_anchors}
        assert types == {"financial_disclosure", "committee_membership"}

    def test_committee_anchor_uses_source_url(self):
        bundle = assemble_repeated_committee_linked_trading_bundle(TRADING_ROW)
        mem = next(a for a in bundle.source_anchors if a.source_type == "committee_membership")
        assert mem.url == COMMITTEE_MEMBERSHIP_URL

    def test_repeated_trading_anchors_distinct_disclosures_from_transactions(self):
        bundle = assemble_repeated_committee_linked_trading_bundle(TRADING_ROW)
        disclosure_ids = [
            a.source_id for a in bundle.source_anchors if a.source_type == "financial_disclosure"
        ]
        assert disclosure_ids == [DISC_ID, "disc-102"]

    def test_repeated_trading_disclosure_anchors_use_transaction_source_urls(self):
        row = {
            **TRADING_ROW,
            "transactions": [
                {
                    **TRANSACTIONS[0],
                    "source_url": "https://efdsearch.senate.gov/search/view/paper/DOC1/",
                },
                {
                    **TRANSACTIONS[1],
                    "source_url": "https://efdsearch.senate.gov/search/view/paper/DOC2/",
                },
            ],
        }
        bundle = assemble_repeated_committee_linked_trading_bundle(row)
        urls = [a.url for a in bundle.source_anchors if a.source_type == "financial_disclosure"]
        assert urls == [
            "https://efdsearch.senate.gov/search/view/paper/DOC1/",
            "https://efdsearch.senate.gov/search/view/paper/DOC2/",
        ]

    def test_repeated_trading_disclosure_anchors_skip_out_of_window_transactions(self):
        row = {
            **TRADING_ROW,
            "transactions": [
                {
                    **TRANSACTIONS[0],
                    "financial_disclosure_id": DISC_ID,
                    "source_url": "https://efdsearch.senate.gov/search/view/paper/IN_WINDOW/",
                },
                {
                    **TRANSACTIONS[1],
                    "financial_disclosure_id": "disc-outside",
                    "transaction_date": dt.date(2024, 1, 10),
                    "source_url": "https://efdsearch.senate.gov/search/view/paper/OUTSIDE/",
                },
            ],
        }

        bundle = assemble_repeated_committee_linked_trading_bundle(row)

        disclosure_anchors = [
            a for a in bundle.source_anchors if a.source_type == "financial_disclosure"
        ]
        assert [(a.source_id, a.url) for a in disclosure_anchors] == [
            (DISC_ID, "https://efdsearch.senate.gov/search/view/paper/IN_WINDOW/")
        ]

    def test_repeated_trading_skips_missing_disclosure_anchor(self):
        row = {**TRADING_ROW, "financial_disclosure_id": None, "transactions": []}
        bundle = assemble_repeated_committee_linked_trading_bundle(row)
        assert [a.source_type for a in bundle.source_anchors] == ["committee_membership"]

    def test_open_ended_service_requires_snapshot_or_reference_date(self):
        row = {**TRADING_ROW, "committee_end_date": None}
        with pytest.raises(ValueError, match="reference_date"):
            assemble_repeated_committee_linked_trading_bundle(row)

    def test_snapshot_date_anchors_open_service(self):
        row = {
            **TRADING_ROW,
            "committee_end_date": None,
            "transactions": [
                {"transaction_date": dt.date(2022, 3, 10), "sector": "energy"},
                {"transaction_date": dt.date(2022, 6, 15), "sector": "energy"},
                {"transaction_date": dt.date(2022, 9, 1), "sector": "energy"},
            ],
            "snapshot_date": dt.date(2022, 6, 30),
        }
        bundle = assemble_repeated_committee_linked_trading_bundle(row)
        assert bundle.context["service_overlap_days"] == 179
        assert bundle.context["matching_transaction_count"] == 2
        assert bundle.context["distinct_trade_days"] == 2


# ---------------------------------------------------------------------------
# late_or_amended_disclosure
# ---------------------------------------------------------------------------

ORIGINAL_FILING_ROW: dict = {
    "member_bioguide_id": BIOGUIDE,
    "financial_disclosure_id": DISC_ID,
    "filing_id": DISC_ID,
    "filed_at": dt.date(2022, 6, 20),
    "deadline": dt.date(2022, 5, 15),
    "filing_is_amendment": False,
    "superseded_filing_id": None,
    "filing_kind": "annual",
}

AMENDMENT_FILING_ROW: dict = {
    "member_bioguide_id": BIOGUIDE,
    "financial_disclosure_id": "disc-202",
    "filing_id": "disc-202",
    "filed_at": dt.date(2022, 8, 1),
    "deadline": dt.date(2022, 5, 15),
    "filing_is_amendment": True,
    "superseded_filing_id": DISC_ID,
    "filing_kind": "amendment",
}


class TestLateOrAmendedDisclosure:
    def test_returns_conflict_bundle_original(self):
        bundle = assemble_late_or_amended_disclosure_bundle(ORIGINAL_FILING_ROW)
        assert isinstance(bundle, ConflictBundle)

    def test_no_superseded_id_for_original(self):
        bundle = assemble_late_or_amended_disclosure_bundle(ORIGINAL_FILING_ROW)
        assert bundle.superseded_filing_id is None

    def test_superseded_id_set_for_amendment(self):
        bundle = assemble_late_or_amended_disclosure_bundle(AMENDMENT_FILING_ROW)
        assert bundle.superseded_filing_id == DISC_ID

    def test_member_bioguide_id_forwarded(self):
        bundle = assemble_late_or_amended_disclosure_bundle(ORIGINAL_FILING_ROW)
        assert bundle.member_bioguide_id == BIOGUIDE

    def test_one_source_anchor(self):
        bundle = assemble_late_or_amended_disclosure_bundle(ORIGINAL_FILING_ROW)
        assert len(bundle.source_anchors) == 1

    def test_source_anchor_type(self):
        bundle = assemble_late_or_amended_disclosure_bundle(ORIGINAL_FILING_ROW)
        assert bundle.source_anchors[0].source_type == "financial_disclosure"

    def test_disclosure_source_id(self):
        bundle = assemble_late_or_amended_disclosure_bundle(ORIGINAL_FILING_ROW)
        assert bundle.source_anchors[0].source_id == DISC_ID

    def test_disclosure_anchor_uses_source_url(self):
        row = {
            **ORIGINAL_FILING_ROW,
            "source_url": "https://efdsearch.senate.gov/search/view/paper/DOC/",
        }
        bundle = assemble_late_or_amended_disclosure_bundle(row)
        assert bundle.source_anchors[0].url == row["source_url"]

    def test_days_late_computed(self):
        bundle = assemble_late_or_amended_disclosure_bundle(ORIGINAL_FILING_ROW)
        # filed 2022-06-20, deadline 2022-05-15 → 36 days late
        assert bundle.context["days_late"] == 36

    def test_days_late_none_when_filed_at_missing(self):
        row = {**ORIGINAL_FILING_ROW, "filed_at": None}
        bundle = assemble_late_or_amended_disclosure_bundle(row)
        assert bundle.context["days_late"] is None

    def test_amendment_context_has_superseded_id(self):
        bundle = assemble_late_or_amended_disclosure_bundle(AMENDMENT_FILING_ROW)
        assert bundle.context["superseded_filing_id"] == DISC_ID

    def test_on_time_filing_zero_days_late(self):
        row = {**ORIGINAL_FILING_ROW, "filed_at": dt.date(2022, 5, 10)}
        bundle = assemble_late_or_amended_disclosure_bundle(row)
        assert bundle.context["days_late"] == 0

    def test_amendment_without_superseded_id_no_bundle_superseded(self):
        row = {**AMENDMENT_FILING_ROW, "superseded_filing_id": None}
        bundle = assemble_late_or_amended_disclosure_bundle(row)
        assert bundle.superseded_filing_id is None

    def test_source_anchor_label_includes_filing_kind(self):
        bundle = assemble_late_or_amended_disclosure_bundle(ORIGINAL_FILING_ROW)
        assert "annual" in bundle.source_anchors[0].label


# ---------------------------------------------------------------------------
# sector_holdings_overlap
# ---------------------------------------------------------------------------

HOLDINGS_ROW: dict = {
    "member_bioguide_id": BIOGUIDE,
    "financial_disclosure_id": DISC_ID,
    "committee_membership_id": MEMB_ID,
    "committee_name": "Committee on Energy",
    "committee_sector": "energy",
    "committee_start_date": dt.date(2022, 1, 3),
    "committee_end_date": dt.date(2023, 1, 3),
    "holding_sector": "energy",
    "sector_name": "Energy & Natural Resources",
    "holding_value_min": 15_001.0,
    "holding_value_max": 50_000.0,
    "disclosure_period_start": dt.date(2022, 1, 1),
    "disclosure_period_end": dt.date(2022, 12, 31),
    "committee_membership_source_url": COMMITTEE_MEMBERSHIP_URL,
}


class TestSectorHoldingsOverlap:
    def test_returns_conflict_bundle(self):
        bundle = assemble_sector_holdings_overlap_bundle(HOLDINGS_ROW)
        assert isinstance(bundle, ConflictBundle)

    def test_member_bioguide_id_forwarded(self):
        bundle = assemble_sector_holdings_overlap_bundle(HOLDINGS_ROW)
        assert bundle.member_bioguide_id == BIOGUIDE

    def test_no_superseded_filing(self):
        bundle = assemble_sector_holdings_overlap_bundle(HOLDINGS_ROW)
        assert bundle.superseded_filing_id is None

    def test_two_source_anchors(self):
        bundle = assemble_sector_holdings_overlap_bundle(HOLDINGS_ROW)
        assert len(bundle.source_anchors) == 2

    def test_source_anchor_types(self):
        bundle = assemble_sector_holdings_overlap_bundle(HOLDINGS_ROW)
        types = {a.source_type for a in bundle.source_anchors}
        assert types == {"financial_disclosure", "committee_membership"}

    def test_holding_value_midpoint(self):
        bundle = assemble_sector_holdings_overlap_bundle(HOLDINGS_ROW)
        # midpoint of (15001, 50000) = 32500.5
        assert bundle.context["holding_value_usd"] == pytest.approx(32_500.5)

    def test_overlap_days_positive(self):
        bundle = assemble_sector_holdings_overlap_bundle(HOLDINGS_ROW)
        assert bundle.context["overlap_days"] > 0

    def test_holding_value_none_when_both_bounds_missing(self):
        row = {**HOLDINGS_ROW, "holding_value_min": None, "holding_value_max": None}
        bundle = assemble_sector_holdings_overlap_bundle(row)
        assert bundle.context["holding_value_usd"] is None

    def test_holding_value_uses_single_bound(self):
        row = {**HOLDINGS_ROW, "holding_value_min": None, "holding_value_max": 50_000.0}
        bundle = assemble_sector_holdings_overlap_bundle(row)
        assert bundle.context["holding_value_usd"] == pytest.approx(50_000.0)

    def test_committee_anchor_label_includes_committee_name(self):
        bundle = assemble_sector_holdings_overlap_bundle(HOLDINGS_ROW)
        mem = next(a for a in bundle.source_anchors if a.source_type == "committee_membership")
        assert "Committee on Energy" in mem.label

    def test_committee_anchor_uses_source_url(self):
        bundle = assemble_sector_holdings_overlap_bundle(HOLDINGS_ROW)
        mem = next(a for a in bundle.source_anchors if a.source_type == "committee_membership")
        assert mem.url == COMMITTEE_MEMBERSHIP_URL

    def test_disclosure_anchor_uses_source_url(self):
        row = {
            **HOLDINGS_ROW,
            "source_url": "https://disclosures.house.gov/public_disc/financial-pdfs/2024/DOC.pdf",
        }
        bundle = assemble_sector_holdings_overlap_bundle(row)
        disc = next(a for a in bundle.source_anchors if a.source_type == "financial_disclosure")
        assert disc.url == row["source_url"]

    def test_open_ended_service_still_computes_overlap(self):
        row = {**HOLDINGS_ROW, "committee_end_date": None}
        bundle = assemble_sector_holdings_overlap_bundle(row)
        assert bundle.context["overlap_days"] >= 0

    def test_both_open_ranges_require_snapshot_or_reference_date(self):
        row = {
            **HOLDINGS_ROW,
            "committee_end_date": None,
            "disclosure_period_end": None,
        }
        with pytest.raises(ValueError, match="reference_date"):
            assemble_sector_holdings_overlap_bundle(row)

    def test_snapshot_date_anchors_both_open_ranges(self):
        row = {
            **HOLDINGS_ROW,
            "committee_end_date": None,
            "disclosure_period_end": None,
            "snapshot_date": dt.date(2022, 6, 30),
        }
        bundle = assemble_sector_holdings_overlap_bundle(row)
        assert bundle.context["overlap_days"] == 179
