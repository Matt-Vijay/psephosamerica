from __future__ import annotations

import datetime as dt

import pytest

from src.export.contracts import SourceAnchor
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.prediction.dataset import (
    PredictionDatasetExamplePayload,
    PredictionDatasetSplitPayload,
    PredictionEvalDatasetPayload,
    build_prediction_eval_dataset,
)


def _feature_row(
    bioguide_id: str,
    *,
    vote_count: int,
    yea_count: int,
    latest_vote_date: dt.date = dt.date(2022, 12, 1),
) -> dict[str, object]:
    return {
        "bioguide_id": bioguide_id,
        "vote_count": vote_count,
        "yea_count": yea_count,
        "nay_count": vote_count - yea_count,
        "present_count": 0,
        "not_voting_count": 0,
        "latest_vote_date": latest_vote_date,
        "vote_event_count": vote_count,
        "party": "D",
        "chamber": "house",
    }


def _label_row(
    bioguide_id: str,
    *,
    vote_event_id: int,
    vote_date: dt.date,
    vote_option: str,
    question: str = "On Passage of H.R. 1, Energy Permitting Reform",
) -> dict[str, object]:
    return {
        "vote_event_id": vote_event_id,
        "chamber": "house",
        "congress": 118 if vote_date.year < 2025 else 119,
        "session_number": 1,
        "roll_call_number": vote_event_id,
        "vote_date": vote_date,
        "question": question,
        "result": "Passed",
        "source_url": f"https://clerk.house.gov/Votes/{vote_event_id}",
        "bioguide_id": bioguide_id,
        "member_slug": bioguide_id.lower(),
        "member_name": bioguide_id,
        "party": "D",
        "state": "CA",
        "vote_option": vote_option,
    }


def _source_anchor() -> SourceAnchor:
    return SourceAnchor(
        source_type="financial_disclosure",
        source_id="fd-2024",
        url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd.pdf",
        label="2024 disclosure",
    )


def _transaction_edge(transaction_date: str) -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id=f"tx-{transaction_date}",
        edge_type="member_sector_transaction_exposure",
        subject=OntologyNodeRef(node_type="member", node_id="C000003", label="C000003"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[_source_anchor()],
        attributes={"transaction_date": transaction_date},
    )


def _bill_signal_row(
    *,
    congress: int,
    sponsor_bioguide_id: str,
    sponsor_date: dt.date,
) -> dict[str, object]:
    return {
        "congress": congress,
        "bill_type": "hr",
        "bill_number": 1,
        "title": "Energy Permitting Reform Act",
        "short_title": "Energy Permitting Reform",
        "bill_source_url": f"https://api.congress.gov/v3/bill/{congress}/hr/1?format=json",
        "introduced_date": sponsor_date,
        "latest_action_date": sponsor_date,
        "sponsor_bioguide_id": sponsor_bioguide_id,
        "sponsor_party": "D",
        "sponsor_role": "cosponsor",
        "is_primary": False,
        "sponsor_date": sponsor_date,
    }


def test_prediction_eval_dataset_builds_dense_training_and_evaluation_matrices() -> None:
    dataset = build_prediction_eval_dataset(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            _feature_row("A000001", vote_count=10, yea_count=9),
            _feature_row("B000002", vote_count=10, yea_count=1),
        ],
        evaluation_feature_rows=[
            _feature_row("C000003", vote_count=10, yea_count=8)
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
            },
        ],
        training_label_rows=[
            _label_row(
                "A000001",
                vote_event_id=1,
                vote_date=dt.date(2023, 1, 10),
                vote_option="yea",
            ),
            _label_row(
                "B000002",
                vote_event_id=2,
                vote_date=dt.date(2023, 1, 10),
                vote_option="nay",
            ),
        ],
        evaluation_label_rows=[
            _label_row(
                "C000003",
                vote_event_id=3,
                vote_date=dt.date(2025, 2, 1),
                vote_option="yea",
            )
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            },
        ],
        ontology_edges=[_transaction_edge("2024-11-15")],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Permitting Reform Act",
                "short_title": "Energy Permitting Reform",
                "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
                "introduced_date": dt.date(2024, 12, 1),
                "latest_action_date": dt.date(2024, 12, 1),
                "sponsor_bioguide_id": "C000003",
                "sponsor_party": "D",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": dt.date(2024, 12, 15),
            }
        ],
    )

    assert dataset.feature_names == sorted(dataset.feature_names)
    assert "member_vote_history" in dataset.feature_names
    assert dataset.training.model_ready_example_count == 2
    assert dataset.evaluation.model_ready_example_count == 1
    assert dataset.training.examples[0].split == "training"
    assert dataset.evaluation.examples[0].split == "evaluation"
    assert dataset.evaluation.examples[0].bill_key == "119-hr-1"
    assert dataset.evaluation.examples[0].bill_context_key == "state_ca:119-hr-1"
    assert dataset.training.examples[0].binary_label == 1
    assert dataset.training.examples[1].binary_label == 0
    assert set(dataset.evaluation.examples[0].features) == set(dataset.feature_names)
    assert dataset.evaluation.examples[0].features["member_vote_history"] == 0.8
    assert dataset.evaluation.examples[0].question == (
        "On Passage of H.R. 1, Energy Permitting Reform"
    )
    assert dataset.evaluation.examples[0].source_url == (
        "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1"
    )
    assert dataset.evaluation.examples[0].jurisdiction_id == "state_ca"
    assert dataset.evaluation.examples[0].legislative_body_id == "ca_assembly"
    assert dataset.evaluation.examples[0].legislative_session_id == "2025_regular"
    assert "financial_sector_overlap" not in dataset.evaluation.examples[0].feature_source_anchors


def test_prediction_eval_dataset_preserves_skips_and_filters_future_ontology_edges() -> None:
    dataset = build_prediction_eval_dataset(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            _feature_row("A000001", vote_count=10, yea_count=9),
        ],
        evaluation_feature_rows=[
            _feature_row("C000003", vote_count=10, yea_count=8),
        ],
        training_label_rows=[
            _label_row(
                "A000001",
                vote_event_id=1,
                vote_date=dt.date(2023, 1, 10),
                vote_option="present",
            ),
        ],
        evaluation_label_rows=[
            _label_row(
                "C000003",
                vote_event_id=3,
                vote_date=dt.date(2025, 2, 1),
                vote_option="yea",
            ),
        ],
        ontology_edges=[_transaction_edge("2025-02-01")],
        bill_signal_rows=[],
    )

    non_binary = dataset.training.examples[0]
    assert non_binary.binary_label is None
    assert non_binary.skipped_reason is None
    assert dataset.training.non_binary_label_count == 1
    assert dataset.training.skipped_count == 0
    assert dataset.training.model_ready_example_count == 0
    evaluated = dataset.evaluation.examples[0]
    assert evaluated.features["financial_sector_overlap"] == 0.0
    assert evaluated.features["holding_recency"] == 0.0
    assert evaluated.features["transaction_recency"] == 0.0
    assert evaluated.features["source_anchor_strength"] == 0.0


def test_prediction_eval_dataset_applies_split_specific_signal_cutoffs() -> None:
    dataset = build_prediction_eval_dataset(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[
            _feature_row("A000001", vote_count=10, yea_count=9),
        ],
        evaluation_feature_rows=[
            _feature_row("C000003", vote_count=10, yea_count=8),
        ],
        training_label_rows=[
            _label_row(
                "A000001",
                vote_event_id=1,
                vote_date=dt.date(2023, 1, 10),
                vote_option="yea",
            ),
        ],
        evaluation_label_rows=[
            _label_row(
                "C000003",
                vote_event_id=3,
                vote_date=dt.date(2025, 2, 1),
                vote_option="yea",
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[
            _bill_signal_row(
                congress=118,
                sponsor_bioguide_id="A000001",
                sponsor_date=dt.date(2022, 12, 15),
            ),
            _bill_signal_row(
                congress=119,
                sponsor_bioguide_id="C000003",
                sponsor_date=dt.date(2024, 12, 15),
            ),
        ],
        contribution_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.9,
                "contribution_date": dt.date(2024, 12, 1),
                "source_anchors": [
                    SourceAnchor(
                        source_type="fec_contribution",
                        source_id="future-for-training",
                        url="https://www.fec.gov/data/receipts/future-for-training/",
                        label="future for training",
                    )
                ],
            },
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.4,
                "contribution_date": dt.date(2024, 12, 1),
                "source_anchors": [
                    SourceAnchor(
                        source_type="fec_contribution",
                        source_id="eval-visible",
                        url="https://www.fec.gov/data/receipts/eval-visible/",
                        label="evaluation visible",
                    )
                ],
            },
        ],
    )

    training = dataset.training.examples[0]
    evaluation = dataset.evaluation.examples[0]
    assert "donation_industry_alignment" in training.unavailable_signals
    assert "donation_industry_alignment" not in training.feature_source_anchors
    assert evaluation.features["donation_industry_alignment"] == 0.4
    assert evaluation.feature_source_anchors["donation_industry_alignment"][0].source_id == (
        "eval-visible"
    )


def test_prediction_eval_dataset_rejects_training_labels_after_evaluation_cutoff() -> None:
    with pytest.raises(ValueError, match="train_end must be on or before feature_cutoff"):
        build_prediction_eval_dataset(
            training_feature_cutoff=dt.date(2022, 12, 31),
            train_start=dt.date(2023, 1, 1),
            train_end=dt.date(2025, 1, 2),
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            training_feature_rows=[],
            evaluation_feature_rows=[],
            training_label_rows=[],
            evaluation_label_rows=[],
            ontology_edges=[],
            bill_signal_rows=[],
        )


def test_prediction_eval_dataset_rejects_orphan_feature_source_anchors() -> None:
    dataset = build_prediction_eval_dataset(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[_feature_row("A000001", vote_count=10, yea_count=9)],
        evaluation_feature_rows=[_feature_row("C000003", vote_count=10, yea_count=8)],
        training_label_rows=[
            _label_row(
                "A000001",
                vote_event_id=1,
                vote_date=dt.date(2023, 1, 10),
                vote_option="yea",
            )
        ],
        evaluation_label_rows=[
            _label_row(
                "C000003",
                vote_event_id=3,
                vote_date=dt.date(2025, 2, 1),
                vote_option="yea",
            )
        ],
        ontology_edges=[_transaction_edge("2024-11-15")],
        bill_signal_rows=[
            _bill_signal_row(
                congress=119,
                sponsor_bioguide_id="C000003",
                sponsor_date=dt.date(2024, 12, 15),
            )
        ],
    ).model_dump(mode="json")
    dataset["evaluation"]["examples"][0]["feature_source_anchors"] = {
        "not_a_feature_signal": [_source_anchor().model_dump(mode="json")]
    }

    with pytest.raises(ValueError, match="feature_source_anchors keys must be present"):
        PredictionEvalDatasetPayload.model_validate(dataset)


def test_prediction_dataset_example_rejects_unofficial_vote_source_url() -> None:
    with pytest.raises(ValueError, match="source_url must be an official vote source URL"):
        PredictionDatasetExamplePayload(
            split="evaluation",
            vote_event_id=3,
            event_key="house-119-1-3",
            vote_date=dt.date(2025, 2, 1),
            chamber="house",
            question="On Passage",
            source_url="https://example.invalid/votes/3",
            member_bioguide_id="C000003",
            actual_vote_option="yea",
            binary_label=1,
            features={"member_vote_history": 0.8},
        )


def test_prediction_dataset_example_accepts_legislative_vote_url_for_non_congress_rows() -> None:
    example = PredictionDatasetExamplePayload(
        split="evaluation",
        vote_event_id=3,
        event_key="state_ca-2025-assembly-3",
        jurisdiction_id="state_ca",
        legislative_body_id="state_ca_assembly",
        legislative_session_id="state_ca_2025_regular",
        vote_date=dt.date(2025, 2, 1),
        chamber="assembly",
        question="On Passage of A.B. 1",
        source_url="https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
        bill_key="state_ca-2025-ab-1",
        member_bioguide_id="CA-A001",
        actual_vote_option="yea",
        binary_label=1,
        features={"member_vote_history": 0.8},
    )

    assert example.source_url is not None


def test_prediction_dataset_example_rejects_non_congress_row_without_session_id() -> None:
    with pytest.raises(ValueError, match="non-Congress examples require legislative_session_id"):
        PredictionDatasetExamplePayload(
            split="evaluation",
            vote_event_id=3,
            event_key="state_ca-ca_assembly-2025_regular-3",
            jurisdiction_id="state_ca",
            legislative_body_id="ca_assembly",
            vote_date=dt.date(2025, 2, 1),
            chamber="assembly",
            question="On Passage of A.B. 1",
            source_url=(
                "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1"
            ),
            member_bioguide_id="CA-A001",
            actual_vote_option="yea",
            binary_label=1,
            features={"member_vote_history": 0.8},
        )


def test_prediction_dataset_example_rejects_non_congress_row_without_body_id() -> None:
    with pytest.raises(ValueError, match="non-Congress examples require legislative_body_id"):
        PredictionDatasetExamplePayload(
            split="evaluation",
            vote_event_id=3,
            event_key="state_ca-ca_assembly-2025_regular-3",
            jurisdiction_id="state_ca",
            legislative_session_id="2025_regular",
            vote_date=dt.date(2025, 2, 1),
            chamber="assembly",
            question="On Passage of A.B. 1",
            source_url=(
                "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1"
            ),
            member_bioguide_id="CA-A001",
            actual_vote_option="yea",
            binary_label=1,
            features={"member_vote_history": 0.8},
        )


def test_prediction_dataset_example_rejects_unofficial_feature_source_anchor() -> None:
    with pytest.raises(ValueError, match="official source URLs"):
        PredictionDatasetExamplePayload(
            split="evaluation",
            vote_event_id=3,
            event_key="house-119-1-3",
            vote_date=dt.date(2025, 2, 1),
            chamber="house",
            question="On Passage",
            source_url="https://clerk.house.gov/Votes/3",
            member_bioguide_id="C000003",
            actual_vote_option="yea",
            binary_label=1,
            features={"financial_sector_overlap": 0.7},
            feature_source_anchors={
                "financial_sector_overlap": [
                    SourceAnchor(
                        source_type="financial_disclosure",
                        source_id="fd-bad",
                        url="https://example.invalid/fd-bad.pdf",
                        label="bad disclosure source",
                    )
                ]
            },
        )


def test_prediction_dataset_example_rejects_legislative_anchor_without_context() -> None:
    with pytest.raises(ValueError, match="legislative source context"):
        PredictionDatasetExamplePayload(
            split="evaluation",
            vote_event_id=3,
            event_key="house-119-1-3",
            vote_date=dt.date(2025, 2, 1),
            chamber="house",
            question="On Passage",
            source_url="https://clerk.house.gov/Votes/3",
            member_bioguide_id="C000003",
            actual_vote_option="yea",
            binary_label=1,
            features={"member_vote_history": 0.7},
            feature_source_anchors={
                "member_vote_history": [
                    SourceAnchor(
                        source_type="legislative_vote",
                        source_id="state-vote-1",
                        url=(
                            "https://leginfo.legislature.ca.gov/faces/"
                            "billVotesClient.xhtml?bill_id=20250AB1"
                        ),
                        label="State vote",
                    )
                ]
            },
        )


def test_prediction_dataset_example_allows_internal_non_claim_feature_source_anchor() -> None:
    example = PredictionDatasetExamplePayload(
        split="evaluation",
        vote_event_id=3,
        event_key="house-119-1-3",
        vote_date=dt.date(2025, 2, 1),
        chamber="house",
        question="On Passage",
        source_url="https://clerk.house.gov/Votes/3",
        member_bioguide_id="C000003",
        actual_vote_option="yea",
        binary_label=1,
        features={"source_anchor_strength": 1.0},
        feature_source_anchors={
            "source_anchor_strength": [
                SourceAnchor(
                    source_type="rule_context",
                    source_id="rule-1",
                    url="https://example.com/rule-context",
                    label="Rule context",
                )
            ]
        },
    )

    assert example.feature_source_anchors["source_anchor_strength"][0].source_type == (
        "rule_context"
    )


def test_prediction_dataset_example_rejects_blank_or_duplicate_unavailable_signals() -> None:
    with pytest.raises(
        ValueError, match="unavailable_signals must be sorted, unique, and nonblank"
    ):
        PredictionDatasetExamplePayload(
            split="evaluation",
            vote_event_id=3,
            event_key="house-119-1-3",
            vote_date=dt.date(2025, 2, 1),
            chamber="house",
            question="On Passage",
            member_bioguide_id="C000003",
            actual_vote_option="yea",
            binary_label=1,
            features={"member_vote_history": 0.8},
            unavailable_signals=["public_statement_alignment", " "],
        )

    with pytest.raises(
        ValueError, match="unavailable_signals must be sorted, unique, and nonblank"
    ):
        PredictionDatasetExamplePayload(
            split="evaluation",
            vote_event_id=3,
            event_key="house-119-1-3",
            vote_date=dt.date(2025, 2, 1),
            chamber="house",
            question="On Passage",
            member_bioguide_id="C000003",
            actual_vote_option="yea",
            binary_label=1,
            features={"member_vote_history": 0.8},
            unavailable_signals=[
                "public_statement_alignment",
                "public_statement_alignment",
            ],
        )


@pytest.mark.parametrize("field_name", ["vote_event_id", "binary_label"])
def test_prediction_dataset_example_rejects_boolean_identity_and_label_fields(
    field_name: str,
) -> None:
    values = {
        "split": "evaluation",
        "vote_event_id": 3,
        "event_key": "house-119-1-3",
        "vote_date": dt.date(2025, 2, 1),
        "chamber": "house",
        "question": "On Passage",
        "member_bioguide_id": "C000003",
        "actual_vote_option": "yea",
        "binary_label": 1,
        "features": {"member_vote_history": 0.8},
    }
    values[field_name] = True

    with pytest.raises(ValueError, match=f"{field_name} must be an integer"):
        PredictionDatasetExamplePayload(**values)


@pytest.mark.parametrize(
    "field_name",
    [
        "label_count",
        "model_ready_example_count",
        "skipped_count",
        "non_binary_label_count",
        "source_url_count",
    ],
)
def test_prediction_dataset_split_rejects_boolean_count_fields(field_name: str) -> None:
    example = PredictionDatasetExamplePayload(
        split="training",
        vote_event_id=1,
        event_key="house-118-1-1",
        vote_date=dt.date(2023, 1, 10),
        chamber="house",
        question="On Passage",
        member_bioguide_id="A000001",
        actual_vote_option="yea",
        binary_label=1,
        features={},
    )
    values = {
        "split": "training",
        "feature_cutoff": dt.date(2022, 12, 31),
        "label_start": dt.date(2023, 1, 1),
        "label_end": dt.date(2024, 12, 31),
        "label_count": 1,
        "model_ready_example_count": 1,
        "skipped_count": 0,
        "non_binary_label_count": 0,
        "source_url_count": 0,
        "examples": [example],
    }
    values[field_name] = True

    with pytest.raises(ValueError, match=f"{field_name} must be an integer"):
        PredictionDatasetSplitPayload(**values)


def test_prediction_eval_dataset_rejects_blank_feature_names() -> None:
    dataset = build_prediction_eval_dataset(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[_feature_row("A000001", vote_count=10, yea_count=9)],
        evaluation_feature_rows=[_feature_row("C000003", vote_count=10, yea_count=8)],
        training_label_rows=[
            _label_row(
                "A000001",
                vote_event_id=1,
                vote_date=dt.date(2023, 1, 10),
                vote_option="yea",
            )
        ],
        evaluation_label_rows=[
            _label_row(
                "C000003",
                vote_event_id=3,
                vote_date=dt.date(2025, 2, 1),
                vote_option="yea",
            )
        ],
        ontology_edges=[_transaction_edge("2024-11-15")],
        bill_signal_rows=[
            _bill_signal_row(
                congress=119,
                sponsor_bioguide_id="C000003",
                sponsor_date=dt.date(2024, 12, 15),
            )
        ],
    ).model_dump(mode="json")
    dataset["feature_names"] = [" "]
    dataset["training"]["examples"][0]["features"] = {" ": 0.0}
    dataset["evaluation"]["examples"][0]["features"] = {" ": 0.0}
    dataset["training"]["examples"][0]["feature_source_anchors"] = {}
    dataset["evaluation"]["examples"][0]["feature_source_anchors"] = {}

    with pytest.raises(ValueError, match="feature_names must be sorted, unique, and nonblank"):
        PredictionEvalDatasetPayload.model_validate(dataset)


def test_prediction_dataset_split_rejects_examples_outside_label_window() -> None:
    example = PredictionDatasetExamplePayload(
        split="evaluation",
        vote_event_id=3,
        event_key="house-119-1-3",
        vote_date=dt.date(2024, 12, 31),
        chamber="house",
        question="On Passage",
        member_bioguide_id="C000003",
        actual_vote_option="yea",
        binary_label=1,
        features={},
    )

    with pytest.raises(ValueError, match="examples must stay inside the split label window"):
        PredictionDatasetSplitPayload(
            split="evaluation",
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            label_count=1,
            model_ready_example_count=1,
            skipped_count=0,
            non_binary_label_count=0,
            source_url_count=0,
            examples=[example],
        )


def test_prediction_eval_dataset_rejects_nested_split_window_drift() -> None:
    dataset = build_prediction_eval_dataset(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[_feature_row("A000001", vote_count=10, yea_count=9)],
        evaluation_feature_rows=[_feature_row("C000003", vote_count=10, yea_count=8)],
        training_label_rows=[
            _label_row(
                "A000001",
                vote_event_id=1,
                vote_date=dt.date(2023, 1, 10),
                vote_option="yea",
            )
        ],
        evaluation_label_rows=[
            _label_row(
                "C000003",
                vote_event_id=3,
                vote_date=dt.date(2025, 2, 1),
                vote_option="yea",
            )
        ],
        ontology_edges=[_transaction_edge("2024-11-15")],
        bill_signal_rows=[
            _bill_signal_row(
                congress=119,
                sponsor_bioguide_id="C000003",
                sponsor_date=dt.date(2024, 12, 15),
            )
        ],
    ).model_dump(mode="json")
    dataset["evaluation"]["label_start"] = "2025-02-01"

    with pytest.raises(ValueError, match="evaluation split window must match dataset"):
        PredictionEvalDatasetPayload.model_validate(dataset)
