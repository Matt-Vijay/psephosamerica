from __future__ import annotations

import datetime as dt
from decimal import Decimal

from src.export.contracts import SourceAnchor
from src.ontology.member_interest_edges import build_member_interest_edges


def _membership_row() -> dict[str, object]:
    return {
        "member_bioguide_id": "P000197",
        "member_name": "Nancy Pelosi",
        "committee_membership_id": 101,
        "committee_code": "HSEN",
        "committee_name": "Energy and Commerce",
        "committee_sector": "energy",
        "committee_mapping_tier": "deterministic",
        "committee_start_date": dt.date(2025, 1, 3),
        "committee_end_date": None,
        "committee_membership_source_url": "https://api.congress.gov/v3/committee/house/HSEN?format=json",
    }


def _holding_row() -> dict[str, object]:
    return {
        "member_bioguide_id": "P000197",
        "holding_id": 501,
        "financial_disclosure_id": 301,
        "filed_at": dt.date(2024, 5, 15),
        "issuer_name": "Exxon Mobil Corp",
        "issuer_ticker": "XOM",
        "holding_sector": "energy",
        "holding_value_min": 15001,
        "holding_value_max": 50000,
        "source_url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/301.pdf",
    }


def _transaction_row() -> dict[str, object]:
    return {
        "member_bioguide_id": "P000197",
        "transaction_id": 701,
        "financial_disclosure_id": 302,
        "issuer_name": "Exxon Mobil Corp",
        "issuer_ticker": "XOM",
        "transaction_type": "purchase",
        "transaction_date": dt.date(2025, 2, 10),
        "amount_min": 1001,
        "amount_max": 15000,
        "sector": "energy",
        "source_url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/302.pdf",
    }


def _contribution_row() -> dict[str, object]:
    return {
        "member_bioguide_id": "P000197",
        "member_name": "Nancy Pelosi",
        "contribution_id": 901,
        "source_record_id": "4073020241987654321",
        "donor_name": "SOLAR BUILDERS PAC",
        "donor_type": "committee",
        "contribution_type": "contribution",
        "contribution_date": dt.date(2024, 10, 15),
        "amount": Decimal("2500.00"),
        "memo": "primary contribution",
        "recipient_fec_committee_id": "C00431445",
        "fec_committee_name": "Pelosi for Congress",
        "sector": "energy",
        "alignment_score": 0.75,
        "source_url": (
            "https://www.fec.gov/data/receipts/individual-contributions/"
            "?two_year_transaction_period=2024"
        ),
    }


def _statement_row() -> dict[str, object]:
    return {
        "member_bioguide_id": "P000197",
        "member_name": "Nancy Pelosi",
        "statement_id": "stmt-901",
        "source_record_id": "pelosi-energy-2024-10-16",
        "statement_date": dt.date(2024, 10, 16),
        "statement_title": "Pelosi Statement on Energy Permitting",
        "statement_excerpt": "Congress should accelerate clean energy deployment.",
        "sector": "energy",
        "alignment_score": 0.7,
        "statement_source_url": (
            "https://pelosi.house.gov/news/press-releases/pelosi-statement-energy-permitting"
        ),
    }


def test_build_member_interest_edges_emits_typed_source_backed_edges() -> None:
    edges = build_member_interest_edges(
        membership_rows=[_membership_row()],
        holding_rows=[_holding_row()],
        transaction_rows=[_transaction_row()],
        contribution_rows=[_contribution_row()],
        statement_rows=[_statement_row()],
    )

    assert [edge.edge_type for edge in edges] == [
        "member_committee_assignment",
        "committee_sector_jurisdiction",
        "member_sector_holding_exposure",
        "member_sector_transaction_exposure",
        "member_sector_contribution_exposure",
        "member_sector_public_statement_alignment",
    ]
    assert all(edge.edge_id.startswith("ont-edge-") for edge in edges)
    assert all(edge.source_anchors for edge in edges)

    committee_edge = edges[0]
    assert committee_edge.subject.node_type == "member"
    assert committee_edge.subject.node_id == "P000197"
    assert committee_edge.object.node_type == "committee"
    assert committee_edge.object.node_id == "HSEN"
    assert committee_edge.source_anchors == [
        SourceAnchor(
            source_type="committee_membership",
            source_id="101",
            url="https://api.congress.gov/v3/committee/house/HSEN?format=json",
            label="Committee membership: Energy and Commerce",
        )
    ]

    holding_edge = edges[2]
    assert holding_edge.subject.node_id == "P000197"
    assert holding_edge.object.node_type == "sector"
    assert holding_edge.object.node_id == "energy"
    assert holding_edge.attributes["issuer_ticker"] == "XOM"
    assert holding_edge.attributes["filing_date"] == "2024-05-15"
    assert holding_edge.source_anchors[0].source_type == "financial_disclosure"

    committee_sector_edge = edges[1]
    assert committee_sector_edge.attributes["start_date"] == "2025-01-03"

    contribution_edge = edges[4]
    assert contribution_edge.subject.node_id == "P000197"
    assert contribution_edge.object.node_type == "sector"
    assert contribution_edge.object.node_id == "energy"
    assert contribution_edge.attributes["contribution_date"] == "2024-10-15"
    assert contribution_edge.attributes["alignment_score"] == 0.75
    assert contribution_edge.attributes["amount"] == Decimal("2500.00")
    assert contribution_edge.source_anchors == [
        SourceAnchor(
            source_type="fec_contribution",
            source_id="901",
            url=(
                "https://www.fec.gov/data/receipts/individual-contributions/"
                "?two_year_transaction_period=2024"
            ),
            label="FEC contribution: SOLAR BUILDERS PAC",
        )
    ]

    statement_edge = edges[5]
    assert statement_edge.subject.node_id == "P000197"
    assert statement_edge.object.node_type == "sector"
    assert statement_edge.object.node_id == "energy"
    assert statement_edge.attributes["statement_date"] == "2024-10-16"
    assert statement_edge.attributes["alignment_score"] == 0.7
    assert statement_edge.source_anchors == [
        SourceAnchor(
            source_type="public_statement",
            source_id="stmt-901",
            url=("https://pelosi.house.gov/news/press-releases/pelosi-statement-energy-permitting"),
            label="Public statement: Pelosi Statement on Energy Permitting",
        )
    ]


def test_build_member_interest_edges_skips_unresolved_or_unanchored_edges() -> None:
    membership = {
        **_membership_row(),
        "committee_sector": None,
        "committee_membership_source_url": None,
    }
    holding = {**_holding_row(), "holding_sector": None}
    transaction = {**_transaction_row(), "source_url": None}
    contribution = {**_contribution_row(), "sector": None}
    statement = {**_statement_row(), "sector": None}

    edges = build_member_interest_edges(
        membership_rows=[membership],
        holding_rows=[holding],
        transaction_rows=[transaction],
        contribution_rows=[contribution],
        statement_rows=[statement],
    )

    assert edges == []


def test_build_member_interest_edges_skips_unofficial_source_urls() -> None:
    membership = {**_membership_row(), "committee_membership_source_url": "HSEN"}
    holding = {**_holding_row(), "source_url": "https://example.com/holding.pdf"}
    transaction = {**_transaction_row(), "source_url": "302"}
    contribution = {**_contribution_row(), "source_url": "https://example.com/fec"}
    statement = {**_statement_row(), "statement_source_url": "https://example.com/statement"}

    edges = build_member_interest_edges(
        membership_rows=[membership],
        holding_rows=[holding],
        transaction_rows=[transaction],
        contribution_rows=[contribution],
        statement_rows=[statement],
    )

    assert edges == []


def test_build_member_interest_edges_skips_undated_contribution_edges() -> None:
    edges = build_member_interest_edges(
        membership_rows=[],
        holding_rows=[],
        transaction_rows=[],
        contribution_rows=[{**_contribution_row(), "contribution_date": None}],
    )

    assert edges == []


def test_build_member_interest_edges_skips_undated_statement_edges() -> None:
    edges = build_member_interest_edges(
        membership_rows=[],
        holding_rows=[],
        transaction_rows=[],
        statement_rows=[{**_statement_row(), "statement_date": None}],
    )

    assert edges == []


def test_build_member_interest_edges_deduplicates_stable_edges() -> None:
    membership = _membership_row()

    first = build_member_interest_edges(
        membership_rows=[membership, dict(membership)],
        holding_rows=[],
        transaction_rows=[],
    )
    second = build_member_interest_edges(
        membership_rows=[dict(membership)],
        holding_rows=[],
        transaction_rows=[],
    )

    assert len(first) == 2
    assert [edge.edge_id for edge in first] == [edge.edge_id for edge in second]
