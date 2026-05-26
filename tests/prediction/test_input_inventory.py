from __future__ import annotations

import datetime as dt

import pytest

from src.prediction.input_inventory import (
    PredictionInputInventoryPayload,
    build_prediction_input_inventory,
)


def _label(source_url: str | None = "https://clerk.house.gov/Votes/1") -> dict[str, object]:
    return {"source_url": source_url}


def _anchor(
    source_type: str = "financial_disclosure",
    url: str = "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/123.pdf",
) -> dict[str, object]:
    return {
        "source_type": source_type,
        "source_id": "source-1",
        "url": url,
        "label": "Source",
    }


def _valid_inventory_payload() -> dict[str, object]:
    return PredictionInputInventoryPayload(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_member_count=1,
        evaluation_feature_member_count=1,
        training_label_count=4,
        evaluation_label_count=4,
        training_labels_missing_feature_member_count=0,
        evaluation_labels_missing_feature_member_count=0,
        training_label_source_url_count=2,
        evaluation_label_source_url_count=2,
        training_label_source_url_coverage_rate=0.5,
        evaluation_label_source_url_coverage_rate=0.5,
        official_training_label_source_url_count=1,
        official_evaluation_label_source_url_count=1,
        training_label_official_source_url_coverage_rate=0.25,
        evaluation_label_official_source_url_coverage_rate=0.25,
        bill_count=4,
        bill_source_url_count=2,
        bill_source_url_coverage_rate=0.5,
        official_bill_source_url_count=1,
        bill_official_source_url_coverage_rate=0.25,
        ontology_edge_count=4,
        sourced_ontology_edge_count=2,
        ontology_source_anchor_coverage_rate=0.5,
        official_sourced_ontology_edge_count=1,
        ontology_official_source_anchor_coverage_rate=0.25,
        fec_contribution_count=4,
        member_attributed_fec_contribution_count=2,
        fec_member_attribution_rate=0.5,
        members_with_fec_candidate_id_count=1,
        public_statement_signal_count=1,
        members_with_public_statement_signal_count=1,
        public_statement_ontology_edge_count=1,
        public_statement_prediction_member_overlap_count=1,
        ok=True,
    ).model_dump(mode="json")


def test_input_inventory_payload_rejects_unscoped_legislative_body_ids() -> None:
    payload = _valid_inventory_payload()
    payload.update(
        {
            "jurisdiction_count": 1,
            "implemented_jurisdiction_count": 0,
            "portable_jurisdiction_count": 1,
            "jurisdiction_ids": ["state_ca"],
            "implemented_jurisdiction_ids": [],
            "portable_jurisdiction_ids": ["state_ca"],
            "legislative_body_count": 1,
            "legislative_body_ids": ["ca_assembly"],
            "legislative_session_count": 0,
            "legislative_session_ids": [],
        }
    )

    with pytest.raises(ValueError, match="legislative_body_ids must be jurisdiction scoped"):
        PredictionInputInventoryPayload.model_validate(payload)


def test_input_inventory_payload_rejects_unscoped_legislative_session_ids() -> None:
    payload = _valid_inventory_payload()
    payload.update(
        {
            "jurisdiction_count": 1,
            "implemented_jurisdiction_count": 0,
            "portable_jurisdiction_count": 1,
            "jurisdiction_ids": ["state_ca"],
            "implemented_jurisdiction_ids": [],
            "portable_jurisdiction_ids": ["state_ca"],
            "legislative_body_count": 1,
            "legislative_body_ids": ["state_ca:ca_assembly"],
            "legislative_session_count": 1,
            "legislative_session_ids": ["state_ca:2025_regular"],
        }
    )

    with pytest.raises(ValueError, match="legislative_session_ids must be body scoped"):
        PredictionInputInventoryPayload.model_validate(payload)


def test_input_inventory_reports_source_coverage_and_fec_attribution() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[_label(), _label(None)],
        evaluation_label_rows=[_label()],
        bill_rows=[
            {"bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json"},
            {"bill_source_url": None},
        ],
        ontology_edge_rows=[
            {"source_anchors": [_anchor(url="https://example.com/disclosure/1")]},
            {
                "edge_type": "member_sector_public_statement_alignment",
                "subject_node_type": "member",
                "subject_node_id": "A000001",
                "source_anchors": [_anchor("public_statement", "https://a.house.gov/news/energy")],
            },
            {"source_anchors": []},
        ],
        fec_inventory={
            "fec_contribution_count": 10,
            "member_attributed_fec_contribution_count": 5,
            "members_with_fec_candidate_id_count": 2,
            "public_statement_signal_count": 4,
            "members_with_public_statement_signal_count": 3,
        },
    )

    assert payload.ok is True
    assert payload.training_label_source_url_coverage_rate == 0.5
    assert payload.evaluation_label_source_url_coverage_rate == 1.0
    assert payload.bill_source_url_coverage_rate == 0.5
    assert payload.ontology_source_anchor_coverage_rate == 2 / 3
    assert payload.ontology_official_source_anchor_coverage_rate == 1 / 3
    assert payload.fec_member_attribution_rate == 0.5
    assert payload.public_statement_signal_count == 4
    assert payload.members_with_public_statement_signal_count == 3
    assert payload.public_statement_ontology_edge_count == 1
    assert payload.public_statement_prediction_member_overlap_count == 1
    assert payload.jurisdiction_count == 1
    assert payload.legislative_body_count == 1
    assert payload.source_family_ids == [
        "congress_bill",
        "congress_vote",
        "financial_disclosure",
        "public_statement",
    ]
    assert payload.source_family_count == 4
    assert payload.warning_reasons == []


def test_input_inventory_counts_feature_vote_history_source_families() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {
                "jurisdiction_id": "us_congress",
                "bioguide_id": "A000001",
                "latest_vote_source_url": "https://clerk.house.gov/Votes/2024001",
            }
        ],
        evaluation_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "bioguide_id": "B000002",
                "latest_vote_source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB1"
                ),
            }
        ],
        training_label_rows=[{"bioguide_id": "A000001", "source_url": None}],
        evaluation_label_rows=[{"bioguide_id": "B000002", "source_url": None}],
        bill_rows=[],
        ontology_edge_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_anchors": [
                    {
                        "source_type": "congress_vote",
                        "source_id": "state-ca-2025-20",
                        "url": (
                            "https://leginfo.legislature.ca.gov/faces/"
                            "billVotesClient.xhtml?bill_id=20250AB13"
                        ),
                    }
                ],
            }
        ],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 1,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.source_family_ids == ["congress_vote", "legislative_vote"]
    assert payload.source_family_count == 2


def test_input_inventory_reports_feature_vote_history_source_coverage() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {
                "bioguide_id": "A000001",
                "vote_count": 3,
                "vote_source_url_count": 2,
                "latest_vote_source_url": "https://clerk.house.gov/Votes/2024001",
            },
            {"bioguide_id": "B000002", "vote_count": 1, "vote_source_url_count": 0},
            {"bioguide_id": "C000003", "vote_count": 0, "vote_source_url_count": 0},
        ],
        evaluation_feature_rows=[
            {
                "bioguide_id": "A000001",
                "vote_count": 2,
                "vote_source_url_count": 1,
                "latest_vote_source_url": "https://clerk.house.gov/Votes/2024002",
            },
            {"bioguide_id": "B000002", "vote_count": 0, "vote_source_url_count": 0},
        ],
        training_label_rows=[
            {"bioguide_id": "A000001", "source_url": None},
            {"bioguide_id": "B000002", "source_url": None},
        ],
        evaluation_label_rows=[{"bioguide_id": "A000001", "source_url": None}],
        bill_rows=[],
        ontology_edge_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_anchors": [
                    {
                        "source_type": "congress_vote",
                        "source_id": "state-ca-2025-20",
                        "url": (
                            "https://leginfo.legislature.ca.gov/faces/"
                            "billVotesClient.xhtml?bill_id=20250AB13"
                        ),
                    }
                ],
            }
        ],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 1,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.training_feature_vote_history_member_count == 2
    assert payload.training_feature_vote_history_source_member_count == 1
    assert payload.training_feature_vote_history_source_coverage_rate == 0.5
    assert payload.evaluation_feature_vote_history_member_count == 1
    assert payload.evaluation_feature_vote_history_source_member_count == 1
    assert payload.evaluation_feature_vote_history_source_coverage_rate == 1.0


def test_input_inventory_feature_vote_history_source_coverage_requires_official_or_canonical_url() -> (
    None
):
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {
                "jurisdiction_id": "us_congress",
                "bioguide_id": "A000001",
                "vote_count": 3,
                "vote_source_url_count": 3,
                "latest_vote_source_url": "https://example.invalid/vote/1",
            },
            {
                "jurisdiction_id": "us_congress",
                "bioguide_id": "B000002",
                "vote_count": 2,
                "vote_source_url_count": 0,
                "latest_vote_event_key": "house-118-1-25",
                "latest_vote_date": dt.date(2024, 6, 1),
            },
            {
                "jurisdiction_id": "state_ca",
                "bioguide_id": "C000003",
                "vote_count": 1,
                "vote_source_url_count": 1,
                "latest_vote_source_url": "https://example.invalid/state-vote/1",
            },
        ],
        evaluation_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "bioguide_id": "C000003",
                "vote_count": 1,
                "latest_vote_source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB1"
                ),
            }
        ],
        training_label_rows=[{"bioguide_id": "A000001", "source_url": None}],
        evaluation_label_rows=[{"bioguide_id": "C000003", "source_url": None}],
        bill_rows=[],
        ontology_edge_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_anchors": [
                    {
                        "source_type": "congress_vote",
                        "source_id": "state-ca-2025-20",
                        "url": (
                            "https://leginfo.legislature.ca.gov/faces/"
                            "billVotesClient.xhtml?bill_id=20250AB13"
                        ),
                    }
                ],
            }
        ],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 1,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.training_feature_vote_history_member_count == 3
    assert payload.training_feature_vote_history_source_member_count == 1
    assert payload.training_feature_vote_history_source_coverage_rate == 1 / 3
    assert payload.evaluation_feature_vote_history_source_member_count == 1


def test_input_inventory_replaces_unofficial_congress_bill_url_for_official_coverage() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[_label()],
        evaluation_label_rows=[_label()],
        bill_rows=[
            {
                "jurisdiction_id": "us_congress",
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "bill_source_url": "https://example.invalid/bill/119/hr/1",
            }
        ],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 1,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.bill_source_url_count == 1
    assert payload.official_bill_source_url_count == 1
    assert payload.bill_official_source_url_coverage_rate == 1.0


def test_input_inventory_reports_cutoff_available_bill_sponsors() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[_label()],
        evaluation_label_rows=[_label()],
        bill_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "sponsor_bioguide_id": "A000001",
                "sponsor_role": "primary",
                "is_primary": True,
                "sponsor_date": None,
                "introduced_date": dt.date(2024, 12, 1),
            },
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "sponsor_bioguide_id": "B000002",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": None,
                "introduced_date": dt.date(2024, 12, 1),
            },
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "sponsor_bioguide_id": "C000003",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": dt.date(2025, 1, 3),
                "introduced_date": dt.date(2024, 12, 1),
            },
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "sponsor_bioguide_id": "D000004",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": dt.date(2024, 12, 15),
                "introduced_date": dt.date(2024, 12, 1),
            },
        ],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 1,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.bill_sponsor_count == 4
    assert payload.bill_available_sponsor_count == 2
    assert payload.bill_sponsor_availability_rate == 0.5
    assert payload.bill_primary_sponsor_introduced_date_fallback_count == 1


def test_input_inventory_does_not_count_future_introduced_primary_sponsor_as_available() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[_label()],
        evaluation_label_rows=[_label()],
        bill_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "sponsor_bioguide_id": "A000001",
                "sponsor_role": "primary",
                "is_primary": True,
                "sponsor_date": None,
                "introduced_date": dt.date(2025, 1, 3),
            }
        ],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 1,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.bill_sponsor_count == 1
    assert payload.bill_available_sponsor_count == 0
    assert payload.bill_sponsor_availability_rate == 0.0
    assert payload.bill_primary_sponsor_introduced_date_fallback_count == 0


def test_input_inventory_does_not_count_boolean_fec_inventory_values() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[_label()],
        evaluation_label_rows=[_label()],
        bill_rows=[{"bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1"}],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": True,
            "member_attributed_fec_contribution_count": True,
            "members_with_fec_candidate_id_count": True,
            "public_statement_signal_count": True,
            "members_with_public_statement_signal_count": True,
        },
    )

    assert payload.fec_contribution_count == 0
    assert payload.member_attributed_fec_contribution_count == 0
    assert payload.members_with_fec_candidate_id_count == 0
    assert payload.public_statement_signal_count == 0
    assert payload.members_with_public_statement_signal_count == 0
    assert "missing_public_statement_signals" in payload.warning_reasons


def test_input_inventory_reports_official_source_coverage() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[
            _label("https://clerk.house.gov/Votes/2024001"),
            _label("https://example.com/votes/2024002"),
        ],
        evaluation_label_rows=[
            _label(
                "https://www.senate.gov/legislative/LIS/roll_call_votes/vote1191/vote_119_1_00001.htm"
            ),
            _label("https://example.com/votes/2025002"),
        ],
        bill_rows=[
            {"bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json"},
            {"bill_source_url": "https://example.com/bill/119/hr/2"},
        ],
        ontology_edge_rows=[
            {"source_anchors": [_anchor()]},
            {"source_anchors": [_anchor(url="https://example.com/disclosure/1")]},
        ],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.training_label_source_url_count == 2
    assert payload.evaluation_label_source_url_count == 2
    assert payload.bill_source_url_count == 2
    assert payload.official_training_label_source_url_count == 1
    assert payload.official_evaluation_label_source_url_count == 1
    assert payload.official_bill_source_url_count == 1
    assert payload.official_sourced_ontology_edge_count == 1
    assert payload.training_label_official_source_url_coverage_rate == 0.5
    assert payload.evaluation_label_official_source_url_coverage_rate == 0.5
    assert payload.bill_official_source_url_coverage_rate == 0.5
    assert payload.ontology_official_source_anchor_coverage_rate == 0.5


def test_input_inventory_counts_synthesized_congress_bill_source_urls() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[_label("https://clerk.house.gov/Votes/2024001")],
        evaluation_label_rows=[_label("https://clerk.house.gov/Votes/2025001")],
        bill_rows=[
            {
                "jurisdiction_id": "us_congress",
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "bill_source_url": None,
            },
            {
                "jurisdiction_id": "state_ca",
                "bill_source_type": "legislative_bill",
                "bill_source_url": None,
            },
        ],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.bill_source_url_count == 1
    assert payload.official_bill_source_url_count == 1
    assert payload.bill_source_url_coverage_rate == 0.5
    assert payload.bill_official_source_url_coverage_rate == 0.5


def test_input_inventory_counts_legislative_bill_urls_with_row_source_type() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[_label("https://clerk.house.gov/Votes/2024001")],
        evaluation_label_rows=[
            {
                "jurisdiction_id": "us_congress",
                "legislative_body_id": "us_congress_house",
                "source_url": "https://clerk.house.gov/Votes/2025001",
            }
        ],
        bill_rows=[
            {
                "jurisdiction_id": "us_congress",
                "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
            },
            {
                "jurisdiction_id": "state_ca",
                "bill_source_type": "legislative_bill",
                "bill_source_url": "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12",
            },
        ],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.bill_source_url_count == 2
    assert payload.official_bill_source_url_count == 2
    assert payload.bill_official_source_url_coverage_rate == 1.0


def test_input_inventory_normalizes_congress_bill_alias_by_jurisdiction() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"jurisdiction_id": "state_ca", "bioguide_id": "CA0001"}],
        evaluation_feature_rows=[{"jurisdiction_id": "state_ca", "bioguide_id": "CA0001"}],
        training_label_rows=[{"jurisdiction_id": "state_ca", "bioguide_id": "CA0001"}],
        evaluation_label_rows=[{"jurisdiction_id": "state_ca", "bioguide_id": "CA0001"}],
        bill_rows=[
            {
                "jurisdiction_id": "state_ca",
                "bill_source_type": "congress_bill",
                "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
            },
            {
                "jurisdiction_id": "state_ca",
                "bill_source_type": "congress_bill",
                "bill_source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billTextClient.xhtml?bill_id=20250AB12"
                ),
            },
        ],
        ontology_edge_rows=[],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.source_family_ids == ["legislative_bill"]
    assert payload.bill_source_url_count == 2
    assert payload.official_bill_source_url_count == 1
    assert payload.bill_official_source_url_coverage_rate == 0.5


def test_input_inventory_counts_legislative_vote_urls_with_row_source_type() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[
            {
                "jurisdiction_id": "us_congress",
                "source_url": "https://clerk.house.gov/Votes/2024001",
            },
            {
                "jurisdiction_id": "state_ca",
                "source_type": "legislative_vote",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB12",
            },
        ],
        evaluation_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB13",
            },
        ],
        bill_rows=[
            {"bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json"},
        ],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.training_label_source_url_count == 2
    assert payload.evaluation_label_source_url_count == 1
    assert payload.official_training_label_source_url_count == 2
    assert payload.official_evaluation_label_source_url_count == 1
    assert payload.training_label_official_source_url_coverage_rate == 1.0
    assert payload.evaluation_label_official_source_url_coverage_rate == 1.0
    assert payload.source_family_ids == [
        "congress_bill",
        "congress_vote",
        "financial_disclosure",
        "legislative_vote",
    ]


def test_input_inventory_normalizes_congress_vote_alias_by_jurisdiction() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "bioguide_id": "CA0001",
                "source_type": "congress_vote",
                "latest_vote_source_url": "https://example.com/not-official",
            }
        ],
        evaluation_feature_rows=[{"jurisdiction_id": "state_ca", "bioguide_id": "CA0001"}],
        training_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_type": "congress_vote",
                "source_url": "https://example.com/not-official",
            }
        ],
        evaluation_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_type": "congress_vote",
                "source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB13"
                ),
            }
        ],
        bill_rows=[],
        ontology_edge_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_anchors": [
                    {
                        "source_type": "congress_vote",
                        "source_id": "state-ca-2025-20",
                        "url": (
                            "https://leginfo.legislature.ca.gov/faces/"
                            "billVotesClient.xhtml?bill_id=20250AB13"
                        ),
                    }
                ],
            }
        ],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.source_family_ids == ["legislative_vote"]
    assert payload.official_training_label_source_url_count == 0
    assert payload.official_evaluation_label_source_url_count == 1
    assert payload.official_sourced_ontology_edge_count == 1
    assert payload.ontology_official_source_anchor_coverage_rate == 1.0


def test_input_inventory_source_families_ignore_unofficial_label_and_bill_urls() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"jurisdiction_id": "state_ca", "bioguide_id": "CA0001"}],
        evaluation_feature_rows=[{"jurisdiction_id": "state_ca", "bioguide_id": "CA0001"}],
        training_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_type": "legislative_vote",
                "source_url": "https://example.invalid/state-vote",
            }
        ],
        evaluation_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_type": "legislative_vote",
                "source_url": "https://example.invalid/state-vote-2",
            }
        ],
        bill_rows=[
            {
                "jurisdiction_id": "state_ca",
                "bill_source_type": "legislative_bill",
                "bill_source_url": "https://example.invalid/state-bill",
            }
        ],
        ontology_edge_rows=[],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.training_label_source_url_count == 1
    assert payload.evaluation_label_source_url_count == 1
    assert payload.bill_source_url_count == 1
    assert payload.official_training_label_source_url_count == 0
    assert payload.official_evaluation_label_source_url_count == 0
    assert payload.official_bill_source_url_count == 0
    assert payload.source_family_ids == []
    assert payload.source_family_count == 0


def test_input_inventory_reports_portable_state_source_families() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"jurisdiction_id": "state_ca", "bioguide_id": "CA0001"}],
        evaluation_feature_rows=[{"jurisdiction_id": "state_ca", "bioguide_id": "CA0001"}],
        training_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_type": "legislative_vote",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB12",
            }
        ],
        evaluation_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_type": "legislative_vote",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB13",
            }
        ],
        bill_rows=[
            {
                "jurisdiction_id": "state_ca",
                "bill_source_type": "legislative_bill",
                "bill_source_url": "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12",
            }
        ],
        ontology_edge_rows=[],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.implemented_jurisdiction_ids == []
    assert payload.portable_jurisdiction_ids == ["state_ca"]
    assert payload.source_family_ids == ["legislative_bill", "legislative_vote"]
    assert payload.source_family_count == 2


def test_input_inventory_blocks_portable_source_rows_missing_jurisdiction_ids() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {
                "bioguide_id": "CA0001",
                "source_type": "legislative_vote",
                "latest_vote_source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB12"
                ),
            }
        ],
        evaluation_feature_rows=[
            {
                "bioguide_id": "CA0001",
                "source_type": "legislative_vote",
                "latest_vote_source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB13"
                ),
            }
        ],
        training_label_rows=[
            {
                "bioguide_id": "CA0001",
                "source_type": "legislative_vote",
                "source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB12"
                ),
            }
        ],
        evaluation_label_rows=[
            {
                "bioguide_id": "CA0001",
                "source_type": "legislative_vote",
                "source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB13"
                ),
            }
        ],
        bill_rows=[
            {
                "bill_source_type": "legislative_bill",
                "bill_source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billTextClient.xhtml?bill_id=20250AB12"
                ),
            }
        ],
        ontology_edge_rows=[
            {
                "source_anchors": [
                    {
                        "source_type": "legislative_vote",
                        "source_id": "state-ca-2025-20",
                        "url": None,
                        "label": "Assembly vote 20",
                    }
                ],
            }
        ],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.ok is False
    assert payload.portable_rows_missing_jurisdiction_id_count == 6
    assert "portable_source_rows_missing_jurisdiction_ids" in payload.blocking_reasons


def test_input_inventory_blocks_portable_jurisdictions_missing_session_ids() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
                "bioguide_id": "CA0001",
            }
        ],
        evaluation_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "bioguide_id": "CA0001",
            }
        ],
        training_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
                "bioguide_id": "CA0001",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB12",
            }
        ],
        evaluation_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "bioguide_id": "CA0001",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB13",
            }
        ],
        bill_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "bill_source_type": "legislative_bill",
                "bill_source_url": "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12",
            }
        ],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.ok is False
    assert payload.portable_rows_missing_session_id_count == 3
    assert "portable_jurisdiction_rows_missing_session_ids" in payload.blocking_reasons
    assert payload.legislative_session_ids == ["state_ca:ca_assembly:ca_2025_regular"]


def test_input_inventory_blocks_portable_jurisdictions_missing_body_ids() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_session_id": "ca_2025_regular",
                "bioguide_id": "CA0001",
            }
        ],
        evaluation_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
                "bioguide_id": "CA0001",
            }
        ],
        training_label_rows=[],
        evaluation_label_rows=[],
        bill_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_session_id": "ca_2025_regular",
                "bill_source_type": "legislative_bill",
                "bill_source_url": "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12",
            }
        ],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.ok is False
    assert payload.portable_rows_missing_body_id_count == 2
    assert "portable_jurisdiction_rows_missing_body_ids" in payload.blocking_reasons
    assert "state_ca:state_ca" not in payload.legislative_body_ids


def test_input_inventory_keeps_state_ontology_vote_anchor_portable() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"jurisdiction_id": "state_ca", "bioguide_id": "CA0001"}],
        evaluation_feature_rows=[{"jurisdiction_id": "state_ca", "bioguide_id": "CA0001"}],
        training_label_rows=[],
        evaluation_label_rows=[],
        bill_rows=[],
        ontology_edge_rows=[
            {
                "jurisdiction_id": "state_ca",
                "source_anchors": [
                    {
                        "source_type": "vote_event",
                        "source_id": "state-ca-2025-20",
                        "url": None,
                        "label": "Assembly vote 20",
                    }
                ],
            }
        ],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 0,
            "members_with_public_statement_signal_count": 0,
        },
    )

    assert payload.source_family_ids == ["legislative_vote"]
    assert payload.source_family_count == 1


def test_input_inventory_reports_jurisdiction_body_and_session_coverage() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {
                "jurisdiction_id": "us_congress",
                "legislative_body_id": "us_congress_house",
                "bioguide_id": "A000001",
            },
        ],
        evaluation_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
                "bioguide_id": "CA0001",
            },
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_senate",
                "legislative_session_id": "ca_2025_regular",
                "bioguide_id": "CA0002",
            },
        ],
        training_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB12",
            }
        ],
        evaluation_label_rows=[
            {
                "jurisdiction_id": "us_congress",
                "legislative_body_id": "us_congress_house",
                "source_url": "https://clerk.house.gov/Votes/2025001",
            }
        ],
        bill_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
                "bill_source_url": "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12",
            }
        ],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.jurisdiction_count == 2
    assert payload.implemented_jurisdiction_count == 1
    assert payload.portable_jurisdiction_count == 1
    assert payload.legislative_body_count == 3
    assert payload.legislative_session_count == 3


def test_input_inventory_uses_ontology_anchor_legislative_context() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {"jurisdiction_id": "us_congress", "bioguide_id": "A000001"},
        ],
        evaluation_feature_rows=[
            {"jurisdiction_id": "us_congress", "bioguide_id": "A000001"},
        ],
        training_label_rows=[],
        evaluation_label_rows=[],
        bill_rows=[],
        ontology_edge_rows=[
            {
                "source_anchors": [
                    {
                        "source_type": "legislative_vote",
                        "jurisdiction_id": "state_tx",
                        "legislative_body_id": "tx_house",
                        "legislative_session_id": "tx_2025_regular",
                        "url": "https://capitol.texas.gov/BillLookup/History.aspx?LegSess=89R&Bill=HB1",
                    }
                ],
            }
        ],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 0,
            "members_with_public_statement_signal_count": 0,
        },
    )

    assert payload.jurisdiction_ids == ["state_tx", "us_congress"]
    assert payload.portable_jurisdiction_ids == ["state_tx"]
    assert "state_tx:tx_house" in payload.legislative_body_ids
    assert "state_tx:tx_house:tx_2025_regular" in payload.legislative_session_ids
    assert payload.portable_rows_missing_jurisdiction_id_count == 0
    assert payload.portable_rows_missing_body_id_count == 0
    assert payload.portable_rows_missing_session_id_count == 0


def test_input_inventory_matches_feature_members_within_jurisdiction() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {
                "jurisdiction_id": "us_congress",
                "bioguide_id": "A000001",
            }
        ],
        evaluation_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "bioguide_id": "A000001",
            }
        ],
        training_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "bioguide_id": "A000001",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB12",
            }
        ],
        evaluation_label_rows=[
            {
                "jurisdiction_id": "us_congress",
                "bioguide_id": "A000001",
                "source_url": "https://clerk.house.gov/Votes/2025001",
            }
        ],
        bill_rows=[],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.ok is False
    assert payload.training_labels_missing_feature_member_count == 1
    assert payload.evaluation_labels_missing_feature_member_count == 1
    assert "training_labels_missing_feature_members" in payload.blocking_reasons
    assert "evaluation_labels_missing_feature_members" in payload.blocking_reasons


def test_input_inventory_matches_feature_members_within_legislative_body() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "bioguide_id": "CA0001",
            }
        ],
        evaluation_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "bioguide_id": "CA0001",
            }
        ],
        training_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_senate",
                "legislative_session_id": "2025_regular",
                "bioguide_id": "CA0001",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250SB12",
            }
        ],
        evaluation_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_senate",
                "legislative_session_id": "2025_regular",
                "bioguide_id": "CA0001",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250SB13",
            }
        ],
        bill_rows=[],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.ok is False
    assert payload.training_labels_missing_feature_member_count == 1
    assert payload.evaluation_labels_missing_feature_member_count == 1
    assert "training_labels_missing_feature_members" in payload.blocking_reasons
    assert "evaluation_labels_missing_feature_members" in payload.blocking_reasons


def test_input_inventory_preserves_jurisdiction_body_and_session_ids() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            {
                "jurisdiction_id": "us_congress",
                "legislative_body_id": "us_congress_house",
                "legislative_session_id": "congress_118_session_2",
                "bioguide_id": "A000001",
            }
        ],
        evaluation_feature_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
                "bioguide_id": "CA0001",
            }
        ],
        training_label_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB12",
            }
        ],
        evaluation_label_rows=[
            {
                "jurisdiction_id": "us_congress",
                "legislative_body_id": "us_congress_house",
                "legislative_session_id": "congress_119_session_1",
                "source_url": "https://clerk.house.gov/Votes/2025001",
            }
        ],
        bill_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "ca_2025_regular",
                "bill_source_url": "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12",
            }
        ],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.jurisdiction_ids == ["state_ca", "us_congress"]
    assert payload.implemented_jurisdiction_ids == ["us_congress"]
    assert payload.portable_jurisdiction_ids == ["state_ca"]
    assert payload.legislative_body_ids == [
        "state_ca:ca_assembly",
        "us_congress:us_congress_house",
    ]
    assert payload.legislative_session_ids == [
        "state_ca:ca_assembly:ca_2025_regular",
        "us_congress:us_congress_house:congress_118_session_2",
        "us_congress:us_congress_house:congress_119_session_1",
    ]


def test_input_inventory_derives_congress_bill_session_from_introduced_date() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[_label()],
        evaluation_label_rows=[_label()],
        bill_rows=[
            {
                "jurisdiction_id": "us_congress",
                "legislative_body_id": "us_congress",
                "congress": 119,
                "introduced_date": dt.date(2025, 1, 3),
            },
            {
                "jurisdiction_id": "us_congress",
                "legislative_body_id": "us_congress",
                "congress": 119,
                "introduced_date": dt.date(2026, 1, 3),
            },
        ],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.legislative_session_count == 3


def test_input_inventory_does_not_count_blank_source_urls() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[_label("  ")],
        evaluation_label_rows=[_label("")],
        bill_rows=[{"bill_source_url": "\t"}],
        ontology_edge_rows=[{"source_anchors": []}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.training_label_source_url_count == 0
    assert payload.evaluation_label_source_url_count == 0
    assert payload.bill_source_url_count == 0
    assert payload.training_label_source_url_coverage_rate == 0.0
    assert payload.evaluation_label_source_url_coverage_rate == 0.0
    assert payload.bill_source_url_coverage_rate == 0.0


def test_input_inventory_counts_synthesized_house_vote_source_urls() -> None:
    label_row = {
        "jurisdiction_id": "us_congress",
        "chamber": "house",
        "congress": 119,
        "session_number": 1,
        "roll_call_number": 7,
        "vote_date": dt.date(2025, 2, 1),
        "source_url": None,
    }
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[label_row],
        evaluation_label_rows=[label_row],
        bill_rows=[],
        ontology_edge_rows=[],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 0,
            "members_with_public_statement_signal_count": 0,
        },
    )

    assert payload.training_label_source_url_count == 1
    assert payload.evaluation_label_source_url_count == 1
    assert payload.official_training_label_source_url_count == 1
    assert payload.official_evaluation_label_source_url_count == 1
    assert payload.training_label_official_source_url_coverage_rate == 1.0
    assert payload.evaluation_label_official_source_url_coverage_rate == 1.0


def test_input_inventory_counts_synthesized_senate_vote_source_urls() -> None:
    label_row = {
        "jurisdiction_id": "us_congress",
        "chamber": "senate",
        "congress": 119,
        "session_number": 1,
        "roll_call_number": 7,
        "vote_date": dt.date(2025, 2, 1),
        "source_url": None,
    }
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[label_row],
        evaluation_label_rows=[label_row],
        bill_rows=[],
        ontology_edge_rows=[],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 0,
            "members_with_public_statement_signal_count": 0,
        },
    )

    assert payload.training_label_source_url_count == 1
    assert payload.evaluation_label_source_url_count == 1
    assert payload.official_training_label_source_url_count == 1
    assert payload.official_evaluation_label_source_url_count == 1
    assert payload.training_label_official_source_url_coverage_rate == 1.0
    assert payload.evaluation_label_official_source_url_coverage_rate == 1.0


def test_input_inventory_blocks_when_temporal_labels_are_missing() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[],
        evaluation_feature_rows=[],
        training_label_rows=[],
        evaluation_label_rows=[],
        bill_rows=[],
        ontology_edge_rows=[],
        fec_inventory={"public_statement_signal_count": 1},
    )

    assert payload.ok is False
    assert payload.blocking_reasons == [
        "missing_training_feature_members",
        "missing_evaluation_feature_members",
        "missing_training_labels",
        "missing_evaluation_labels",
    ]
    assert "missing_ontology_edges" in payload.warning_reasons


def test_input_inventory_blocks_when_labels_have_no_cutoff_feature_member() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[
            {"bioguide_id": "A000001", "source_url": "https://clerk.house.gov/Votes/1"}
        ],
        evaluation_label_rows=[
            {"bioguide_id": "B000002", "source_url": "https://clerk.house.gov/Votes/2"}
        ],
        bill_rows=[{"bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1"}],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 0,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 1,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.ok is False
    assert payload.training_labels_missing_feature_member_count == 0
    assert payload.evaluation_labels_missing_feature_member_count == 1
    assert payload.blocking_reasons == ["evaluation_labels_missing_feature_members"]


def test_input_inventory_warns_when_training_features_are_cutoff_or_term_excluded() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[
            {"bioguide_id": "A000001", "source_url": "https://clerk.house.gov/Votes/1"}
        ],
        evaluation_label_rows=[
            {"bioguide_id": "A000001", "source_url": "https://clerk.house.gov/Votes/2"}
        ],
        bill_rows=[{"bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1"}],
        ontology_edge_rows=[{"source_anchors": [_anchor()]}],
        fec_inventory={
            "fec_contribution_count": 1,
            "member_attributed_fec_contribution_count": 1,
            "members_with_fec_candidate_id_count": 1,
            "public_statement_signal_count": 1,
            "members_with_public_statement_signal_count": 1,
        },
    )

    assert payload.ok is False
    assert payload.blocking_reasons == [
        "missing_training_feature_members",
        "training_labels_missing_feature_members",
    ]
    assert "training_feature_members_excluded_by_cutoff_or_term_window" in payload.warning_reasons


def test_input_inventory_warns_when_fec_contributions_are_unattributed() -> None:
    payload = build_prediction_input_inventory(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[{"bioguide_id": "A000001"}],
        evaluation_feature_rows=[{"bioguide_id": "A000001"}],
        training_label_rows=[_label()],
        evaluation_label_rows=[_label()],
        bill_rows=[{"bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1"}],
        ontology_edge_rows=[{"source_anchors": [{}]}],
        fec_inventory={
            "fec_contribution_count": 3,
            "member_attributed_fec_contribution_count": 0,
            "members_with_fec_candidate_id_count": 0,
            "public_statement_signal_count": 0,
            "members_with_public_statement_signal_count": 0,
        },
    )

    assert payload.ok is True
    assert payload.warning_reasons == [
        "fec_contributions_not_attributed_to_members",
        "missing_member_fec_candidate_ids",
        "missing_public_statement_signals",
    ]


def test_input_inventory_rejects_ok_payload_with_blocking_reasons() -> None:
    with pytest.raises(ValueError, match="ok inventory cannot carry blocking reasons"):
        PredictionInputInventoryPayload(
            training_feature_cutoff=dt.date(2022, 12, 31),
            train_start=dt.date(2023, 1, 1),
            train_end=dt.date(2024, 12, 31),
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            training_feature_member_count=0,
            evaluation_feature_member_count=0,
            training_label_count=0,
            evaluation_label_count=0,
            training_label_source_url_count=0,
            evaluation_label_source_url_count=0,
            training_label_source_url_coverage_rate=None,
            evaluation_label_source_url_coverage_rate=None,
            bill_count=0,
            bill_source_url_count=0,
            bill_source_url_coverage_rate=None,
            ontology_edge_count=0,
            sourced_ontology_edge_count=0,
            ontology_source_anchor_coverage_rate=None,
            fec_contribution_count=0,
            member_attributed_fec_contribution_count=0,
            fec_member_attribution_rate=None,
            members_with_fec_candidate_id_count=0,
            public_statement_signal_count=0,
            members_with_public_statement_signal_count=0,
            ok=True,
            blocking_reasons=["missing_training_labels"],
        )


def test_input_inventory_rejects_blank_or_duplicate_reasons() -> None:
    payload = _valid_inventory_payload()
    payload["ok"] = False
    payload["blocking_reasons"] = ["missing_training_labels", " "]

    with pytest.raises(ValueError, match="blocking_reasons must be unique and nonblank"):
        PredictionInputInventoryPayload.model_validate(payload)

    payload = _valid_inventory_payload()
    payload["warning_reasons"] = [
        "missing_ontology_edges",
        "missing_ontology_edges",
    ]

    with pytest.raises(ValueError, match="warning_reasons must be unique and nonblank"):
        PredictionInputInventoryPayload.model_validate(payload)


def test_input_inventory_rejects_overlapping_blocking_and_warning_reasons() -> None:
    payload = _valid_inventory_payload()
    payload["ok"] = False
    payload["blocking_reasons"] = ["missing_training_labels"]
    payload["warning_reasons"] = ["missing_training_labels"]

    with pytest.raises(
        ValueError,
        match="blocking_reasons and warning_reasons must not overlap",
    ):
        PredictionInputInventoryPayload.model_validate(payload)


@pytest.mark.parametrize(
    ("count_field", "expected_reason"),
    [
        (
            "portable_rows_missing_jurisdiction_id_count",
            "portable_source_rows_missing_jurisdiction_ids",
        ),
        (
            "portable_rows_missing_body_id_count",
            "portable_jurisdiction_rows_missing_body_ids",
        ),
        (
            "portable_rows_missing_session_id_count",
            "portable_jurisdiction_rows_missing_session_ids",
        ),
    ],
)
def test_input_inventory_rejects_missing_portable_context_counts_without_blocking_reason(
    count_field: str,
    expected_reason: str,
) -> None:
    payload = _valid_inventory_payload()
    payload["ok"] = False
    payload["blocking_reasons"] = ["missing_training_labels"]
    payload[count_field] = 1

    with pytest.raises(ValueError, match=expected_reason):
        PredictionInputInventoryPayload.model_validate(payload)


def test_input_inventory_rejects_non_temporal_windows() -> None:
    with pytest.raises(ValueError, match="feature_cutoff must be before label_start"):
        PredictionInputInventoryPayload(
            training_feature_cutoff=dt.date(2022, 12, 31),
            train_start=dt.date(2023, 1, 1),
            train_end=dt.date(2024, 12, 31),
            feature_cutoff=dt.date(2025, 1, 1),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            training_feature_member_count=1,
            evaluation_feature_member_count=1,
            training_label_count=1,
            evaluation_label_count=1,
            training_label_source_url_count=1,
            evaluation_label_source_url_count=1,
            training_label_source_url_coverage_rate=1.0,
            evaluation_label_source_url_coverage_rate=1.0,
            bill_count=1,
            bill_source_url_count=1,
            bill_source_url_coverage_rate=1.0,
            ontology_edge_count=1,
            sourced_ontology_edge_count=1,
            ontology_source_anchor_coverage_rate=1.0,
            fec_contribution_count=1,
            member_attributed_fec_contribution_count=1,
            fec_member_attribution_rate=1.0,
            members_with_fec_candidate_id_count=1,
            public_statement_signal_count=1,
            members_with_public_statement_signal_count=1,
            ok=True,
        )


def test_input_inventory_rejects_impossible_counts_and_stale_rates() -> None:
    payload = _valid_inventory_payload()
    payload["training_label_source_url_count"] = 5
    payload["training_label_source_url_coverage_rate"] = 0.5

    with pytest.raises(ValueError, match="coverage count exceeds total"):
        PredictionInputInventoryPayload.model_validate(payload)

    payload = _valid_inventory_payload()
    payload["training_label_source_url_coverage_rate"] = 1.0

    with pytest.raises(ValueError, match="coverage rate mismatch"):
        PredictionInputInventoryPayload.model_validate(payload)


def test_input_inventory_rejects_official_counts_above_source_counts() -> None:
    payload = _valid_inventory_payload()
    payload["official_training_label_source_url_count"] = 3
    payload["training_label_official_source_url_coverage_rate"] = 0.75

    with pytest.raises(ValueError, match="official coverage count exceeds source count"):
        PredictionInputInventoryPayload.model_validate(payload)


@pytest.mark.parametrize(
    "field_name",
    [
        "training_feature_member_count",
        "evaluation_feature_member_count",
        "training_label_count",
        "evaluation_label_count",
        "training_label_source_url_count",
        "evaluation_label_source_url_count",
        "official_training_label_source_url_count",
        "official_evaluation_label_source_url_count",
        "bill_count",
        "bill_source_url_count",
        "official_bill_source_url_count",
        "ontology_edge_count",
        "sourced_ontology_edge_count",
        "official_sourced_ontology_edge_count",
        "fec_contribution_count",
        "member_attributed_fec_contribution_count",
        "members_with_fec_candidate_id_count",
        "public_statement_signal_count",
        "members_with_public_statement_signal_count",
    ],
)
def test_input_inventory_payload_rejects_boolean_count_fields(field_name: str) -> None:
    payload = _valid_inventory_payload()
    payload[field_name] = True

    with pytest.raises(ValueError, match=f"{field_name} must be an integer"):
        PredictionInputInventoryPayload.model_validate(payload)
