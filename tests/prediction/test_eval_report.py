from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from src.export.contracts import SourceAnchor
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.prediction.backtest import PredictionBacktestMetricsPayload
from src.prediction.dataset import PredictionDatasetExamplePayload
from src.prediction.eval_report import (
    LearnedSignalModelPayload,
    PredictionBillMetadataCoveragePayload,
    PredictionBillSemanticCoveragePayload,
    PredictionDatasetSplitQualityPayload,
    PredictionEvalBackfillRecommendationPayload,
    PredictionEvalBackfillSampleCasePayload,
    PredictionEvalCalibrationBinPayload,
    PredictionEvalComparisonPayload,
    PredictionEvalModelPayload,
    PredictionEvalReadinessCheckPayload,
    PredictionEvalReadinessPayload,
    PredictionEvalReportPayload,
    PredictionFeatureSourceCoveragePayload,
    _bill_semantic_coverage_key,
    _bill_signal_row_source_url,
    _feature_source_coverage_from_comparisons,
    build_prediction_eval_report,
)
from src.prediction.llm_semantics import BillSectorSemanticPayload, BillSemanticPayload


def test_prediction_eval_backfill_recommendation_rejects_coerced_fields() -> None:
    with pytest.raises(ValueError, match="action must be nonblank"):
        PredictionEvalBackfillRecommendationPayload(
            action=" ",
            priority_score=1,
            reason=" ",
            affected_case_count=1,
            missing_bill_keys=["119-hr-1"],
            unavailable_signal_counts={"committee_alignment": 1},
            sample_vote_event_ids=[101],
            sample_cases=[],
        )
    with pytest.raises(ValueError, match="priority_score must be an integer"):
        PredictionEvalBackfillRecommendationPayload(
            action="backfill_feature_source_urls",
            priority_score=True,
            reason="source gaps",
            affected_case_count=1,
        )
    with pytest.raises(ValueError, match="sample_vote_event_ids must be an integer"):
        PredictionEvalBackfillRecommendationPayload(
            action="backfill_feature_source_urls",
            priority_score=1,
            reason="source gaps",
            affected_case_count=1,
            sample_vote_event_ids=["101"],
        )
    with pytest.raises(ValueError, match="missing_bill_keys must be sorted, unique, and nonblank"):
        PredictionEvalBackfillRecommendationPayload(
            action="backfill_feature_source_urls",
            priority_score=1,
            reason="source gaps",
            affected_case_count=1,
            missing_bill_keys=[" 119-hr-1 "],
        )


def test_prediction_eval_backfill_sample_case_rejects_coerced_scope_fields() -> None:
    with pytest.raises(ValueError, match="vote_event_id must be an integer"):
        PredictionEvalBackfillSampleCasePayload(
            vote_event_id=False,
            event_key="state_ca-ca_assembly-2025_regular-7",
            jurisdiction_id="state_ca",
            legislative_body_id="ca_assembly",
            legislative_session_id="2025_regular",
            bill_key="state_ca:2025:ab:1",
            member_bioguide_id="A000001",
        )
    with pytest.raises(ValueError, match="event_key must not have surrounding whitespace"):
        PredictionEvalBackfillSampleCasePayload(
            vote_event_id=101,
            event_key=" state_ca-ca_assembly-2025_regular-7 ",
            jurisdiction_id="state_ca",
        )
    with pytest.raises(ValueError, match="vote_event_id must be an integer"):
        PredictionEvalBackfillSampleCasePayload(vote_event_id="101")


def _feature_row(
    bioguide_id: str,
    *,
    vote_count: int,
    yea_count: int,
    party: str = "D",
) -> dict[str, object]:
    return {
        "bioguide_id": bioguide_id,
        "vote_count": vote_count,
        "yea_count": yea_count,
        "nay_count": vote_count - yea_count,
        "present_count": 0,
        "not_voting_count": 0,
        "latest_vote_date": dt.date(2022, 12, 1),
        "vote_event_count": vote_count,
        "party": party,
        "chamber": "house",
    }


def _label_row(
    bioguide_id: str,
    *,
    vote_event_id: int,
    vote_date: dt.date,
    vote_option: str,
    question: str = "On Passage of H.R. 1",
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


def _future_transaction_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="edge-future-tx",
        edge_type="member_sector_transaction_exposure",
        subject=OntologyNodeRef(node_type="member", node_id="C000003", label="C000003"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-future",
                url="https://disclosures.house.gov/public_disc/ptr-pdfs/2025/fd.pdf",
                label="Future disclosure",
            )
        ],
        attributes={"transaction_date": "2025-02-01"},
    )


def _current_transaction_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="edge-current-tx",
        edge_type="member_sector_transaction_exposure",
        subject=OntologyNodeRef(node_type="member", node_id="C000003", label="C000003"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-current",
                url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd.pdf",
                label="Current disclosure",
            )
        ],
        attributes={"transaction_date": "2024-11-15"},
    )


def _unknown_availability_transaction_edge() -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id="edge-unknown-tx",
        edge_type="member_sector_transaction_exposure",
        subject=OntologyNodeRef(node_type="member", node_id="C000003", label="C000003"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-unknown",
                url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd.pdf",
                label="Unknown-date disclosure",
            )
        ],
        attributes={},
    )


def test_prediction_eval_report_trains_learned_signal_model_and_compares_every_eval_vote() -> None:
    congress_feature = _feature_row("C000003", vote_count=10, yea_count=9)
    report = build_prediction_eval_report(
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
        evaluation_feature_rows=[congress_feature],
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
            ),
        ],
        ontology_edges=[_current_transaction_edge()],
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
        bill_semantics=[
            BillSemanticPayload(
                bill_key="119-hr-1",
                available_at=dt.date(2024, 12, 1),
                sectors=[
                    BillSectorSemanticPayload(
                        sector_id="energy",
                        label="Energy",
                        confidence=0.95,
                        rationale="Bill text concerns energy permitting.",
                    )
                ],
                industries=["utilities"],
                affected_entities=["utilities"],
                coalition_summary="Energy permitting coalition.",
                ideological_valence="mixed",
                source_anchors=[
                    SourceAnchor(
                        source_type="congress_bill",
                        source_id="119-hr-1",
                        url="https://api.congress.gov/v3/bill/119/hr/1?format=json",
                        label="Congress.gov bill 119-hr-1",
                    )
                ],
            )
        ],
    )

    assert report.readiness.status == "partial"
    assert report.readiness.ok is True
    assert report.readiness.blocking_reasons == []
    assert "missing_training_bill_semantic_coverage" in report.readiness.warning_reasons
    assert report.data_quality.training.model_ready_rate == 1.0
    assert report.data_quality.training.skipped_rate == 0.0
    assert report.data_quality.training.source_url_coverage_rate == 1.0
    assert report.data_quality.training.binary_yea_count == 1
    assert report.data_quality.training.binary_nay_count == 1
    assert report.data_quality.training.non_binary_vote_count == 0
    assert report.data_quality.evaluation.model_ready_rate == 1.0
    assert report.data_quality.evaluation.source_url_coverage_rate == 1.0
    assert report.data_quality.evaluation.feature_count == len(report.dataset.feature_names)
    assert report.bill_semantic_coverage.required_bill_count == 1
    assert report.bill_semantic_coverage.covered_bill_count == 1
    assert report.bill_semantic_coverage.missing_bill_keys == []
    assert report.bill_semantic_coverage.coverage_rate == 1.0
    assert report.training_bill_semantic_coverage.required_bill_keys == ["118-hr-1"]
    assert report.training_bill_semantic_coverage.missing_bill_keys == ["118-hr-1"]
    assert report.evaluation_bill_semantic_coverage.required_bill_keys == ["119-hr-1"]
    assert report.evaluation_bill_semantic_coverage.covered_bill_keys == ["119-hr-1"]
    assert report.bill_metadata_coverage.required_bill_keys == ["118-hr-1", "119-hr-1"]
    assert report.bill_metadata_coverage.loaded_bill_keys == ["119-hr-1"]
    assert report.bill_metadata_coverage.missing_bill_keys == ["118-hr-1"]
    metadata_recommendation = next(
        recommendation
        for recommendation in report.backfill_recommendations
        if recommendation.action == "load_missing_bill_metadata"
    )
    assert metadata_recommendation.missing_bill_keys == ["118-hr-1"]
    assert metadata_recommendation.sample_vote_event_ids == [1, 2]
    assert [case.model_dump(mode="json") for case in metadata_recommendation.sample_cases] == [
        {
            "vote_event_id": 1,
            "event_key": "house-118-1-1",
            "jurisdiction_id": "us_congress",
            "legislative_body_id": "us_congress_house",
            "legislative_session_id": "congress_118_session_1",
            "bill_key": "118-hr-1",
            "member_bioguide_id": "A000001",
        },
        {
            "vote_event_id": 2,
            "event_key": "house-118-1-2",
            "jurisdiction_id": "us_congress",
            "legislative_body_id": "us_congress_house",
            "legislative_session_id": "congress_118_session_1",
            "bill_key": "118-hr-1",
            "member_bioguide_id": "B000002",
        },
    ]
    semantic_recommendation = next(
        recommendation
        for recommendation in report.backfill_recommendations
        if recommendation.action == "materialize_missing_bill_semantics"
    )
    assert semantic_recommendation.action == "materialize_missing_bill_semantics"
    assert semantic_recommendation.missing_bill_keys == ["118-hr-1"]
    assert semantic_recommendation.sample_vote_event_ids == [1, 2]
    assert [case.model_dump(mode="json") for case in semantic_recommendation.sample_cases] == [
        {
            "vote_event_id": 1,
            "event_key": "house-118-1-1",
            "jurisdiction_id": "us_congress",
            "legislative_body_id": "us_congress_house",
            "legislative_session_id": "congress_118_session_1",
            "bill_key": "118-hr-1",
            "member_bioguide_id": "A000001",
        },
        {
            "vote_event_id": 2,
            "event_key": "house-118-1-2",
            "jurisdiction_id": "us_congress",
            "legislative_body_id": "us_congress_house",
            "legislative_session_id": "congress_118_session_1",
            "bill_key": "118-hr-1",
            "member_bioguide_id": "B000002",
        },
    ]
    assert report.training_example_count == 2
    assert report.dataset.training.model_ready_example_count == 2
    assert report.dataset.evaluation.model_ready_example_count == 1
    assert report.dataset.evaluation.examples[0].features["member_vote_history"] == 0.9
    assert report.dataset.evaluation.examples[0].source_url == "https://clerk.house.gov/Votes/3"
    assert [model.model_name for model in report.models] == [
        "member_vote_rate_baseline",
        "ontology_signal_model",
        "learned_signal_logistic",
        "per_member_signal_model",
    ]
    # The per-member partial-pooling model is scored on every evaluation vote
    # alongside the global models, so its metrics are reported like the rest.
    per_member_summary = next(
        model for model in report.models if model.model_name == "per_member_signal_model"
    )
    assert per_member_summary.metrics.log_loss is not None
    assert all(model.metrics.log_loss is not None for model in report.models)
    # Per-slice benchmark metrics for the per-member model are published for the
    # dashboards and the no-regression gate (overall slice always present).
    benchmark_slice_names = {item.slice_name for item in report.benchmark_slices}
    assert "overall" in benchmark_slice_names
    # The report also emits the calibration dashboard payload for the frontend.
    assert report.calibration_dashboard is not None
    assert report.calibration_dashboard.model_name == "per_member_signal_model"
    assert all(model.calibration_bins for model in report.models)
    learned_model_summary = next(
        model for model in report.models if model.model_name == "learned_signal_logistic"
    )
    assert learned_model_summary.calibration_bins[0].prediction_count == 1
    assert learned_model_summary.calibration_bins[0].actual_yea_rate == 1.0
    assert report.learned_model.model_name == "learned_signal_logistic"
    assert report.learned_model.training_example_count == 2
    assert "member_vote_history" in report.learned_model.signal_names
    learned_lift = next(
        lift for lift in report.model_lifts if lift.model_name == "learned_signal_logistic"
    )
    assert learned_lift.baseline_model_name == "member_vote_rate_baseline"
    assert learned_lift.accuracy_delta == 0.0
    assert learned_lift.log_loss_improvement is not None
    assert len(report.comparisons) == 1
    comparison = report.comparisons[0]
    assert comparison.vote_event_id == 3
    assert comparison.bill_key == "119-hr-1"
    assert comparison.bill_context_key == "119-hr-1"
    assert comparison.member_bioguide_id == "C000003"
    assert comparison.model_probabilities["learned_signal_logistic"] > 0.5
    assert comparison.model_predicted_vote_options["learned_signal_logistic"] == "yea"
    assert comparison.model_vote_probabilities["learned_signal_logistic"]["yea"] > 0.5
    assert comparison.model_correct["learned_signal_logistic"] is True
    assert (
        comparison.feature_signals_by_model["ontology_signal_model"]["member_vote_history"] == 0.9
    )
    assert (
        comparison.feature_source_anchors_by_model["ontology_signal_model"][
            "financial_sector_overlap"
        ][0].source_type
        == "financial_disclosure"
    )
    financial_source_coverage = next(
        row
        for row in report.feature_source_coverage
        if row.model_name == "ontology_signal_model"
        and row.signal_name == "financial_sector_overlap"
    )
    assert financial_source_coverage.prediction_count == 1
    assert financial_source_coverage.sourced_prediction_count == 1
    assert financial_source_coverage.source_anchor_count == 1
    assert financial_source_coverage.url_sourced_prediction_count == 1
    assert financial_source_coverage.url_source_anchor_count == 1
    assert financial_source_coverage.url_source_coverage_rate == 1.0
    assert financial_source_coverage.official_source_sourced_prediction_count == 1
    assert financial_source_coverage.official_source_anchor_count == 1
    assert financial_source_coverage.official_source_coverage_rate == 1.0
    training_vote_source_coverage = next(
        row
        for row in report.training_feature_source_coverage
        if row.model_name == "ontology_signal_model" and row.signal_name == "member_vote_history"
    )
    assert training_vote_source_coverage.prediction_count == 2
    assert training_vote_source_coverage.sourced_prediction_count == 2
    assert training_vote_source_coverage.url_sourced_prediction_count == 0
    assert training_vote_source_coverage.url_source_coverage_rate == 0.0
    assert training_vote_source_coverage.official_source_sourced_prediction_count == 0
    assert training_vote_source_coverage.official_source_anchor_count == 0
    assert training_vote_source_coverage.official_source_coverage_rate == 0.0
    assert report.evaluation_feature_source_coverage == report.feature_source_coverage
    assert (
        "donation_industry_alignment"
        in comparison.unavailable_signals_by_model["ontology_signal_model"]
    )
    member_vote_weight = next(
        coefficient
        for coefficient in report.learned_signal_coefficients
        if coefficient.signal_name == "member_vote_history"
    )
    assert member_vote_weight.coefficient > 0.0
    assert any(
        recommendation.action == "load_fec_donations_and_member_crosswalks"
        for recommendation in report.backfill_recommendations
    )
    donation_recommendation = next(
        recommendation
        for recommendation in report.backfill_recommendations
        if recommendation.action == "load_fec_donations_and_member_crosswalks"
    )
    assert donation_recommendation.sample_vote_event_ids == [3]
    assert [case.model_dump(mode="json") for case in donation_recommendation.sample_cases] == [
        {
            "vote_event_id": 3,
            "event_key": "house-119-1-3",
            "jurisdiction_id": "us_congress",
            "legislative_body_id": "us_congress_house",
            "legislative_session_id": "congress_119_session_1",
            "bill_key": "119-hr-1",
            "member_bioguide_id": "C000003",
        },
    ]
    statement_recommendation = next(
        recommendation
        for recommendation in report.backfill_recommendations
        if recommendation.action == "load_source_backed_public_statement_signals"
    )
    assert statement_recommendation.sample_vote_event_ids == [3]
    assert [case.model_dump(mode="json") for case in statement_recommendation.sample_cases] == [
        {
            "vote_event_id": 3,
            "event_key": "house-119-1-3",
            "jurisdiction_id": "us_congress",
            "legislative_body_id": "us_congress_house",
            "legislative_session_id": "congress_119_session_1",
            "bill_key": "119-hr-1",
            "member_bioguide_id": "C000003",
        },
    ]
    source_url_recommendation = next(
        recommendation
        for recommendation in report.backfill_recommendations
        if recommendation.action == "backfill_feature_source_urls"
    )
    assert source_url_recommendation.sample_vote_event_ids == [3]
    assert [case.model_dump(mode="json") for case in source_url_recommendation.sample_cases] == [
        {
            "vote_event_id": 3,
            "event_key": "house-119-1-3",
            "jurisdiction_id": "us_congress",
            "legislative_body_id": "us_congress_house",
            "legislative_session_id": "congress_119_session_1",
            "bill_key": "119-hr-1",
            "member_bioguide_id": "C000003",
        },
    ]


def test_prediction_eval_report_blocks_empty_or_untrainable_runs() -> None:
    report = build_prediction_eval_report(
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
        ontology_edges=[],
        bill_signal_rows=[],
    )

    assert report.readiness.status == "blocked"
    assert report.readiness.ok is False
    assert set(report.readiness.blocking_reasons) >= {
        "missing_training_labels",
        "missing_evaluation_labels",
        "missing_learned_training_examples",
    }
    assert "missing_ontology_edges" in report.readiness.warning_reasons
    assert "missing_bill_semantics" in report.readiness.warning_reasons


def test_prediction_eval_report_warns_when_bill_semantics_do_not_cover_eval_bills() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=9),
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
            ),
        ],
        ontology_edges=[_current_transaction_edge()],
        bill_signal_rows=[],
        bill_semantics=[
            BillSemanticPayload(
                bill_key="119-hr-99",
                available_at=dt.date(2024, 12, 1),
                sectors=[
                    BillSectorSemanticPayload(
                        sector_id="energy",
                        label="Energy",
                        confidence=0.95,
                        rationale="Unrelated bill semantics.",
                    )
                ],
                source_anchors=[
                    SourceAnchor(
                        source_type="congress_bill",
                        source_id="119-hr-99",
                        url="https://api.congress.gov/v3/bill/119/hr/99?format=json",
                        label="Congress.gov bill 119-hr-99",
                    )
                ],
            )
        ],
    )

    assert report.readiness.status == "partial"
    assert report.readiness.ok is True
    assert "missing_bill_semantic_coverage" in report.readiness.warning_reasons
    assert "missing_training_bill_semantic_coverage" in report.readiness.warning_reasons
    assert report.bill_semantic_coverage.required_bill_keys == ["119-hr-1"]
    assert report.bill_semantic_coverage.covered_bill_keys == []
    assert report.bill_semantic_coverage.missing_bill_keys == ["119-hr-1"]
    recommendation = next(
        recommendation
        for recommendation in report.backfill_recommendations
        if recommendation.action == "materialize_missing_bill_semantics"
    )
    assert recommendation.action == "materialize_missing_bill_semantics"
    assert recommendation.missing_bill_keys == ["118-hr-1", "119-hr-1"]


def test_prediction_eval_report_does_not_count_congress_semantics_for_state_labels() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("CA-A001", vote_count=10, yea_count=5)
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "chamber": "assembly",
            }
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
                "CA-A001",
                vote_event_id=3,
                vote_date=dt.date(2025, 2, 1),
                vote_option="yea",
                question="On Passage of H.R. 1",
            )
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "chamber": "assembly",
                "source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB1"
                ),
            }
        ],
        ontology_edges=[],
        bill_signal_rows=[],
        bill_semantics=[
            BillSemanticPayload(
                bill_key="119-hr-1",
                available_at=dt.date(2024, 12, 1),
                sectors=[
                    BillSectorSemanticPayload(
                        sector_id="energy",
                        label="Energy",
                        confidence=0.95,
                        rationale="Congress semantic should not cover state labels.",
                    )
                ],
                source_anchors=[
                    SourceAnchor(
                        source_type="congress_bill",
                        source_id="119-hr-1",
                        url="https://api.congress.gov/v3/bill/119/hr/1?format=json",
                        label="Congress.gov bill 119-hr-1",
                    )
                ],
            )
        ],
    )

    assert report.evaluation_bill_semantic_coverage.required_bill_keys == ["state_ca:119-hr-1"]
    assert report.evaluation_bill_semantic_coverage.covered_bill_keys == []
    assert report.evaluation_bill_semantic_coverage.missing_bill_keys == ["state_ca:119-hr-1"]
    assert "missing_bill_semantic_coverage" in report.readiness.warning_reasons
    recommendation = next(
        item
        for item in report.backfill_recommendations
        if item.action == "materialize_missing_bill_semantics"
    )
    assert recommendation.missing_bill_keys == ["118-hr-1", "state_ca:119-hr-1"]
    assert recommendation.sample_vote_event_ids == [1, 2, 3]
    assert recommendation.sample_cases[-1].model_dump(mode="json") == {
        "vote_event_id": 3,
        "event_key": "state_ca-ca_assembly-2025_regular-3",
        "jurisdiction_id": "state_ca",
        "legislative_body_id": "ca_assembly",
        "legislative_session_id": "2025_regular",
        "bill_key": "119-hr-1",
        "bill_context_key": "state_ca:119-hr-1",
        "member_bioguide_id": "CA-A001",
    }


def test_prediction_eval_report_does_not_count_future_bill_metadata_as_covered() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=9)
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
            ),
        ],
        ontology_edges=[_current_transaction_edge()],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Future Energy Bill",
                "short_title": "Future Energy",
                "introduced_date": dt.date(2025, 1, 3),
                "latest_action_date": dt.date(2025, 1, 3),
                "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
            }
        ],
    )

    assert report.bill_metadata_coverage.required_bill_keys == ["118-hr-1"]
    assert report.bill_metadata_coverage.loaded_bill_keys == []
    assert report.bill_metadata_coverage.missing_bill_keys == ["118-hr-1"]
    assert report.bill_metadata_coverage.cutoff_ineligible_bill_keys == ["119-hr-1"]
    assert report.cutoff_audit.bill_signal_row_count == 1
    assert report.cutoff_audit.cutoff_bill_signal_row_count == 0
    assert report.cutoff_audit.excluded_future_bill_signal_row_count == 1


def test_prediction_eval_report_does_not_recommend_future_bill_semantics() -> None:
    report = build_prediction_eval_report(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[],
        evaluation_feature_rows=[_feature_row("C000003", vote_count=10, yea_count=9)],
        training_label_rows=[],
        evaluation_label_rows=[
            _label_row(
                "C000003",
                vote_event_id=3,
                vote_date=dt.date(2025, 2, 1),
                vote_option="yea",
            ),
        ],
        ontology_edges=[_current_transaction_edge()],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Future Energy Bill",
                "introduced_date": dt.date(2025, 1, 3),
                "latest_action_date": dt.date(2025, 1, 3),
                "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
            }
        ],
        bill_semantics=[
            BillSemanticPayload(
                bill_key="119-hr-1",
                available_at=dt.date(2025, 1, 3),
                sectors=[
                    BillSectorSemanticPayload(
                        sector_id="energy",
                        label="Energy",
                        confidence=0.95,
                        rationale="Future bill semantics.",
                    )
                ],
                source_anchors=[
                    SourceAnchor(
                        source_type="congress_bill",
                        source_id="119-hr-1",
                        url="https://api.congress.gov/v3/bill/119/hr/1?format=json",
                        label="Congress.gov bill 119-hr-1",
                    )
                ],
            )
        ],
    )

    assert report.bill_semantic_coverage.required_bill_keys == []
    assert report.bill_semantic_coverage.missing_bill_keys == []
    assert report.bill_semantic_coverage.cutoff_ineligible_bill_keys == ["119-hr-1"]
    assert report.evaluation_bill_semantic_coverage.cutoff_ineligible_bill_keys == ["119-hr-1"]
    semantic_recommendations = [
        recommendation
        for recommendation in report.backfill_recommendations
        if recommendation.action == "materialize_missing_bill_semantics"
    ]
    assert semantic_recommendations == []


def test_prediction_eval_report_does_not_count_congress_metadata_for_state_labels() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("CA-A001", vote_count=10, yea_count=5)
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "chamber": "assembly",
            }
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
                "CA-A001",
                vote_event_id=3,
                vote_date=dt.date(2025, 2, 1),
                vote_option="yea",
                question="On Passage of H.R. 1",
            )
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "chamber": "assembly",
                "source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB1"
                ),
            }
        ],
        ontology_edges=[],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Congress Energy Bill",
                "short_title": "Congress Energy",
                "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
                "introduced_date": dt.date(2024, 12, 1),
                "latest_action_date": dt.date(2024, 12, 1),
            }
        ],
    )

    assert report.bill_metadata_coverage.required_bill_keys == [
        "118-hr-1",
        "state_ca:119-hr-1",
    ]
    assert report.bill_metadata_coverage.loaded_bill_keys == []
    assert report.bill_metadata_coverage.missing_bill_keys == [
        "118-hr-1",
        "state_ca:119-hr-1",
    ]
    recommendation = next(
        item
        for item in report.backfill_recommendations
        if item.action == "load_missing_bill_metadata"
    )
    assert recommendation.sample_vote_event_ids == [1, 2, 3]


def test_prediction_eval_report_does_not_count_unsourced_non_congress_bill_metadata_as_covered() -> (
    None
):
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=9),
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
            ),
        ],
        ontology_edges=[_current_transaction_edge()],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 99,
                "jurisdiction_id": "state_ca",
                "title": "Unsourced Energy Bill",
                "short_title": "Unsourced Energy",
                "introduced_date": dt.date(2024, 12, 1),
                "latest_action_date": dt.date(2024, 12, 1),
                "bill_source_url": None,
            }
        ],
    )

    assert report.bill_metadata_coverage.required_bill_keys == ["118-hr-1", "119-hr-1"]
    assert report.bill_metadata_coverage.loaded_bill_keys == []
    assert report.bill_metadata_coverage.missing_bill_keys == ["118-hr-1", "119-hr-1"]
    recommendation = next(
        recommendation
        for recommendation in report.backfill_recommendations
        if recommendation.action == "load_missing_bill_metadata"
    )
    assert recommendation.missing_bill_keys == ["118-hr-1", "119-hr-1"]


def test_prediction_eval_report_counts_synthesized_congress_bill_metadata_source() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=9),
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
            ),
        ],
        ontology_edges=[_current_transaction_edge()],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Canonical Energy Bill",
                "short_title": "Canonical Energy",
                "introduced_date": dt.date(2024, 12, 1),
                "latest_action_date": dt.date(2024, 12, 1),
                "bill_source_url": None,
            }
        ],
    )

    assert report.bill_metadata_coverage.loaded_bill_keys == ["119-hr-1"]
    assert report.bill_metadata_coverage.missing_bill_keys == ["118-hr-1"]


def test_prediction_eval_report_normalizes_congress_bill_alias_for_state_rows() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=9),
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
            ),
        ],
        ontology_edges=[_current_transaction_edge()],
        bill_signal_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "bill_source_type": "congress_bill",
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "State Energy Bill",
                "short_title": "State Energy",
                "introduced_date": dt.date(2024, 12, 1),
                "latest_action_date": dt.date(2024, 12, 1),
                "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
            }
        ],
    )

    assert report.bill_metadata_coverage.loaded_bill_keys == []
    assert "state_ca:119-hr-1" not in report.bill_metadata_coverage.loaded_bill_keys


def test_bill_signal_row_source_url_normalizes_congress_bill_alias_for_state_rows() -> None:
    assert (
        _bill_signal_row_source_url(
            {
                "jurisdiction_id": "state_ca",
                "bill_source_type": "congress_bill",
                "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
            }
        )
        is None
    )
    assert _bill_signal_row_source_url(
        {
            "jurisdiction_id": "state_ca",
            "bill_source_type": "congress_bill",
            "bill_source_url": (
                "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12"
            ),
            "congress": 119,
            "bill_type": "hr",
            "bill_number": 1,
        }
    ) == ("https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB12")


def test_bill_keys_already_scoped_with_colon_are_not_double_prefixed() -> None:
    example = PredictionDatasetExamplePayload(
        split="evaluation",
        vote_event_id=3,
        event_key="state_ca-ca_assembly-2025_regular-3",
        jurisdiction_id="state_ca",
        legislative_body_id="ca_assembly",
        legislative_session_id="2025_regular",
        vote_date=dt.date(2025, 2, 1),
        chamber="assembly",
        question="On Passage of AB 1",
        source_url=(
            "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1"
        ),
        bill_key="state_ca:2025:ab:1",
        member_bioguide_id="CA-A001",
        actual_vote_option="yea",
    )

    assert _bill_semantic_coverage_key(example) == "state_ca:2025:ab:1"


def test_prediction_eval_report_warns_on_unknown_bill_semantic_availability() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=9),
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
            ),
        ],
        ontology_edges=[_current_transaction_edge()],
        bill_signal_rows=[],
        bill_semantics=[
            BillSemanticPayload(
                bill_key="119-hr-1",
                source_anchors=[
                    SourceAnchor(
                        source_type="congress_bill",
                        source_id="119-hr-1",
                        url="https://api.congress.gov/v3/bill/119/hr/1?format=json",
                        label="Congress.gov bill 119-hr-1",
                    )
                ],
            )
        ],
    )

    assert report.cutoff_audit.unknown_availability_bill_semantic_count == 1
    assert "unknown_bill_semantic_availability" in report.readiness.warning_reasons


def test_prediction_eval_report_warns_on_unknown_ontology_edge_availability() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=9),
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
            ),
        ],
        ontology_edges=[_unknown_availability_transaction_edge()],
        bill_signal_rows=[],
    )

    assert report.cutoff_audit.unknown_availability_ontology_edge_count == 1
    assert report.cutoff_audit.cutoff_ontology_edge_count == 0
    assert "unknown_ontology_edge_availability" in report.readiness.warning_reasons


def test_prediction_eval_report_treats_statement_date_as_known_ontology_availability() -> None:
    statement_edge = OntologyEdgePayload(
        edge_id="edge-statement",
        edge_type="member_sector_public_statement_alignment",
        subject=OntologyNodeRef(node_type="member", node_id="C000003", label="C000003"),
        object=OntologyNodeRef(node_type="sector", node_id="energy", label="Energy"),
        source_anchors=[
            SourceAnchor(
                source_type="public_statement",
                source_id="stmt-1",
                url="https://c.house.gov/news/energy",
                label="Energy statement",
            )
        ],
        attributes={"statement_date": "2024-11-06", "alignment_score": 0.7},
    )

    report = build_prediction_eval_report(
        training_feature_cutoff=dt.date(2022, 12, 31),
        train_start=dt.date(2023, 1, 1),
        train_end=dt.date(2024, 12, 31),
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        training_feature_rows=[],
        evaluation_feature_rows=[_feature_row("C000003", vote_count=10, yea_count=9)],
        training_label_rows=[],
        evaluation_label_rows=[
            _label_row(
                "C000003",
                vote_event_id=3,
                vote_date=dt.date(2025, 2, 1),
                vote_option="yea",
            ),
        ],
        ontology_edges=[statement_edge],
        bill_signal_rows=[],
    )

    assert report.cutoff_audit.unknown_availability_ontology_edge_count == 0
    assert report.cutoff_audit.cutoff_ontology_edge_count == 1
    assert "unknown_ontology_edge_availability" not in report.readiness.warning_reasons


def test_prediction_eval_report_summarizes_coverage_skips_sources_and_failures() -> None:
    state_feature = _feature_row("C000003", vote_count=10, yea_count=9) | {
        "jurisdiction_id": "state_ca",
        "legislative_body_id": "ca_assembly",
        "legislative_session_id": "2025_regular",
    }
    report = build_prediction_eval_report(
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
        evaluation_feature_rows=[state_feature],
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
                vote_option="nay",
                question="On Passage of H.R. 1",
            )
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            },
            _label_row(
                "D000004",
                vote_event_id=4,
                vote_date=dt.date(2025, 2, 2),
                vote_option="yea",
                question="On Passage of H.R. 2",
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    )

    baseline = next(
        model for model in report.models if model.model_name == "member_vote_rate_baseline"
    )
    assert baseline.coverage_rate == 0.5
    assert baseline.skip_reason_counts == {"missing_pre_cutoff_vote_history": 1}
    assert report.unavailable_signal_counts["donation_industry_alignment"] >= 1
    assert report.unavailable_signal_counts["public_statement_alignment"] >= 1
    assert report.comparisons[0].question == "On Passage of H.R. 1"
    assert report.comparisons[0].source_url == (
        "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1"
    )
    assert report.comparisons[0].jurisdiction_id == "state_ca"
    assert report.comparisons[0].legislative_body_id == "ca_assembly"
    assert report.comparisons[0].legislative_session_id == "2025_regular"
    assert report.comparisons[0].bill_key == "119-hr-1"
    assert report.comparisons[0].bill_context_key == "state_ca:119-hr-1"
    assert report.top_failure_cases
    assert {case.failure_kind for case in report.top_failure_cases} >= {
        "wrong_prediction",
        "skipped",
    }
    wrong_case = next(
        case for case in report.top_failure_cases if case.failure_kind == "wrong_prediction"
    )
    assert wrong_case.log_loss is not None
    assert report.failure_groups
    wrong_groups = [
        group for group in report.failure_groups if group.failure_kind == "wrong_prediction"
    ]
    skipped_groups = [group for group in report.failure_groups if group.failure_kind == "skipped"]
    assert wrong_groups
    assert skipped_groups
    assert wrong_groups[0].case_count >= 1
    assert wrong_groups[0].sample_vote_event_ids
    assert [case.model_dump(mode="json") for case in wrong_groups[0].sample_cases] == [
        {
            "vote_event_id": 3,
            "event_key": "state_ca-ca_assembly-2025_regular-3",
            "jurisdiction_id": "state_ca",
            "legislative_body_id": "ca_assembly",
            "legislative_session_id": "2025_regular",
            "bill_key": "119-hr-1",
            "bill_context_key": "state_ca:119-hr-1",
            "member_bioguide_id": "C000003",
        }
    ]
    assert wrong_groups[0].has_source_url is True
    assert any(group.skipped_reason for group in skipped_groups)
    assert any(
        "donation_industry_alignment" in group.unavailable_signal_counts
        for group in report.failure_groups
    )
    recommendation_actions = {
        recommendation.action for recommendation in report.backfill_recommendations
    }
    assert "load_fec_donations_and_member_crosswalks" in recommendation_actions
    assert "load_source_backed_public_statement_signals" in recommendation_actions


def test_prediction_eval_report_includes_cutoff_audit_for_future_features() -> None:
    report = build_prediction_eval_report(
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
        ontology_edges=[_future_transaction_edge()],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 2,
                "title": "Pre Cutoff Bill With Future Action",
                "short_title": "Pre Cutoff Bill",
                "introduced_date": dt.date(2024, 12, 1),
                "latest_action_date": dt.date(2025, 1, 3),
                "sponsor_bioguide_id": "C000003",
                "sponsor_party": "D",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": dt.date(2024, 12, 1),
            },
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Future Sponsor Bill",
                "short_title": "Future Sponsor",
                "introduced_date": dt.date(2025, 1, 3),
                "latest_action_date": dt.date(2025, 1, 3),
                "sponsor_bioguide_id": "C000003",
                "sponsor_party": "D",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": dt.date(2025, 1, 3),
            },
        ],
        bill_semantics=[
            BillSemanticPayload(
                bill_key="118-hr-1",
                available_at=dt.date(2022, 12, 1),
                source_anchors=[
                    SourceAnchor(
                        source_type="congress_bill",
                        source_id="118-hr-1",
                        url="https://api.congress.gov/v3/bill/118/hr/1?format=json",
                        label="Congress.gov bill 118-hr-1",
                    )
                ],
            ),
            BillSemanticPayload(
                bill_key="119-hr-1",
                available_at=dt.date(2024, 12, 1),
                source_anchors=[
                    SourceAnchor(
                        source_type="congress_bill",
                        source_id="119-hr-1",
                        url="https://api.congress.gov/v3/bill/119/hr/1?format=json",
                        label="Congress.gov bill 119-hr-1",
                    )
                ],
            ),
            BillSemanticPayload(
                bill_key="119-hr-2",
                available_at=dt.date(2025, 1, 2),
                source_anchors=[
                    SourceAnchor(
                        source_type="congress_bill",
                        source_id="119-hr-2",
                        url="https://api.congress.gov/v3/bill/119/hr/2?format=json",
                        label="Congress.gov bill 119-hr-2",
                    )
                ],
            ),
            BillSemanticPayload(
                bill_key="119-hr-3",
                source_anchors=[
                    SourceAnchor(
                        source_type="congress_bill",
                        source_id="119-hr-3",
                        url="https://api.congress.gov/v3/bill/119/hr/3?format=json",
                        label="Congress.gov bill 119-hr-3",
                    )
                ],
            ),
        ],
        training_contribution_signal_rows=[
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.9,
                "contribution_date": dt.date(2022, 12, 1),
            },
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.4,
                "contribution_date": dt.date(2023, 1, 2),
            },
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.1,
            },
        ],
        evaluation_contribution_signal_rows=[
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.8,
                "contribution_date": dt.date(2024, 12, 1),
            },
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.3,
                "contribution_date": dt.date(2025, 1, 2),
            },
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.1,
            },
        ],
        training_statement_signal_rows=[
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.7,
                "statement_date": dt.date(2022, 12, 1),
            },
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.2,
                "statement_date": dt.date(2023, 1, 2),
            },
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.1,
            },
        ],
        evaluation_statement_signal_rows=[
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.6,
                "statement_date": dt.date(2024, 12, 1),
            },
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.1,
                "statement_date": dt.date(2025, 1, 2),
            },
            {
                "bioguide_id": "C000003",
                "sector": "energy",
                "alignment_score": 0.1,
            },
        ],
    )

    assert report.cutoff_audit.ontology_edge_count == 1
    assert report.cutoff_audit.cutoff_ontology_edge_count == 0
    assert report.cutoff_audit.excluded_future_ontology_edge_count == 1
    assert report.cutoff_audit.bill_signal_row_count == 2
    assert report.cutoff_audit.cutoff_bill_signal_row_count == 1
    assert report.cutoff_audit.excluded_future_bill_signal_row_count == 1
    assert report.cutoff_audit.bill_semantic_count == 4
    assert report.cutoff_audit.cutoff_training_bill_semantic_count == 1
    assert report.cutoff_audit.excluded_future_training_bill_semantic_count == 2
    assert report.cutoff_audit.cutoff_evaluation_bill_semantic_count == 2
    assert report.cutoff_audit.excluded_future_evaluation_bill_semantic_count == 1
    assert report.cutoff_audit.unknown_availability_bill_semantic_count == 1
    assert report.cutoff_audit.training_contribution_signal_row_count == 3
    assert report.cutoff_audit.cutoff_training_contribution_signal_row_count == 1
    assert report.cutoff_audit.unknown_availability_training_contribution_signal_row_count == 1
    assert report.cutoff_audit.excluded_future_training_contribution_signal_row_count == 1
    assert report.cutoff_audit.evaluation_contribution_signal_row_count == 3
    assert report.cutoff_audit.cutoff_evaluation_contribution_signal_row_count == 1
    assert report.cutoff_audit.unknown_availability_evaluation_contribution_signal_row_count == 1
    assert report.cutoff_audit.excluded_future_evaluation_contribution_signal_row_count == 1
    assert report.cutoff_audit.training_statement_signal_row_count == 3
    assert report.cutoff_audit.cutoff_training_statement_signal_row_count == 1
    assert report.cutoff_audit.unknown_availability_training_statement_signal_row_count == 1
    assert report.cutoff_audit.excluded_future_training_statement_signal_row_count == 1
    assert report.cutoff_audit.evaluation_statement_signal_row_count == 3
    assert report.cutoff_audit.cutoff_evaluation_statement_signal_row_count == 1
    assert report.cutoff_audit.unknown_availability_evaluation_statement_signal_row_count == 1
    assert report.cutoff_audit.excluded_future_evaluation_statement_signal_row_count == 1
    assert "unknown_contribution_signal_availability" in report.readiness.warning_reasons
    assert "unknown_statement_signal_availability" in report.readiness.warning_reasons
    recommendation_actions = {
        recommendation.action for recommendation in report.backfill_recommendations
    }
    assert "timestamp_contribution_signal_availability" in recommendation_actions
    assert "timestamp_statement_signal_availability" in recommendation_actions


def test_prediction_eval_report_audits_unknown_bill_signal_availability() -> None:
    report = build_prediction_eval_report(
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
        ontology_edges=[],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Undated Sponsor Bill",
                "short_title": "Undated Sponsor",
                "introduced_date": dt.date(2024, 12, 1),
                "latest_action_date": dt.date(2024, 12, 1),
                "sponsor_bioguide_id": "A000001",
                "sponsor_party": "D",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": None,
            }
        ],
    )

    assert report.cutoff_audit.bill_signal_row_count == 1
    assert report.cutoff_audit.cutoff_bill_signal_row_count == 0
    assert report.cutoff_audit.unknown_availability_bill_signal_row_count == 1
    assert report.cutoff_audit.excluded_future_bill_signal_row_count == 0
    assert "unknown_bill_signal_availability" in report.readiness.warning_reasons
    recommendation_actions = {
        recommendation.action for recommendation in report.backfill_recommendations
    }
    assert "timestamp_bill_signal_availability" in recommendation_actions


def test_prediction_eval_report_rejects_non_temporal_windows() -> None:
    with pytest.raises(ValueError, match="feature_cutoff must be before label_start"):
        build_prediction_eval_report(
            training_feature_cutoff=dt.date(2022, 12, 31),
            train_start=dt.date(2023, 1, 1),
            train_end=dt.date(2024, 12, 31),
            feature_cutoff=dt.date(2025, 1, 1),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            training_feature_rows=[],
            evaluation_feature_rows=[],
            training_label_rows=[],
            evaluation_label_rows=[],
            ontology_edges=[],
            bill_signal_rows=[],
        )


def test_prediction_eval_report_rejects_headline_count_mismatches() -> None:
    report = build_prediction_eval_report(
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
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["training_example_count"] = 999

    with pytest.raises(ValueError, match="training_example_count must match"):
        PredictionEvalReportPayload.model_validate(report)

    report["training_example_count"] = report["dataset"]["training"]["model_ready_example_count"]
    report["evaluation_label_count"] = len(report["comparisons"])
    report["dataset"]["evaluation"]["label_count"] = 0
    report["dataset"]["evaluation"]["model_ready_example_count"] = 0
    report["dataset"]["evaluation"]["skipped_count"] = 0
    report["dataset"]["evaluation"]["source_url_count"] = 0
    report["dataset"]["evaluation"]["examples"] = []

    with pytest.raises(ValueError, match="evaluation_label_count must match dataset"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_stale_data_quality_summary() -> None:
    report = build_prediction_eval_report(
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
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["data_quality"]["training"]["source_url_count"] = 0
    report["data_quality"]["training"]["source_url_coverage_rate"] = 0.0

    with pytest.raises(ValueError, match="data_quality.training must match dataset"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_inconsistent_cutoff_audit_counts() -> None:
    report = build_prediction_eval_report(
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
        ontology_edges=[_future_transaction_edge()],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["cutoff_audit"]["cutoff_ontology_edge_count"] = 2

    with pytest.raises(ValueError, match="ontology edge cutoff audit counts must partition total"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_boolean_cutoff_audit_counts() -> None:
    report = build_prediction_eval_report(
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
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["cutoff_audit"]["training_feature_row_count"] = False

    with pytest.raises(ValueError, match="training_feature_row_count must be an integer"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_model_payload_rejects_inconsistent_summary_counts() -> None:
    metrics = PredictionBacktestMetricsPayload(
        label_count=4,
        evaluated_count=2,
        correct_count=1,
        skipped_count=2,
        accuracy=0.5,
        brier_score=None,
        log_loss=None,
    )

    with pytest.raises(ValueError, match="coverage_rate must match"):
        PredictionEvalModelPayload(
            model_name="ontology_signal_model",
            metrics=metrics,
            coverage_rate=0.25,
            skip_reason_counts={"missing_pre_cutoff_vote_history": 2},
        )

    with pytest.raises(ValueError, match="skip_reason_counts total must match"):
        PredictionEvalModelPayload(
            model_name="ontology_signal_model",
            metrics=metrics,
            coverage_rate=0.5,
            skip_reason_counts={"missing_pre_cutoff_vote_history": 1},
        )


def test_prediction_eval_readiness_rejects_blank_duplicate_or_overlapping_reasons() -> None:
    with pytest.raises(ValueError, match="blocking_reasons must be unique and nonblank"):
        PredictionEvalReadinessPayload(
            status="blocked",
            ok=False,
            blocking_reasons=["missing_training_labels", " "],
        )

    with pytest.raises(ValueError, match="warning_reasons must be unique and nonblank"):
        PredictionEvalReadinessPayload(
            status="partial",
            ok=True,
            warning_reasons=[
                "missing_bill_semantics",
                "missing_bill_semantics",
            ],
        )

    with pytest.raises(
        ValueError,
        match="blocking_reasons and warning_reasons must not overlap",
    ):
        PredictionEvalReadinessPayload(
            status="blocked",
            ok=False,
            blocking_reasons=["missing_training_labels"],
            warning_reasons=["missing_training_labels"],
        )


def test_prediction_eval_readiness_check_normalizes_and_rejects_blank_text() -> None:
    check = PredictionEvalReadinessCheckPayload(
        name=" training labels ",
        status="pass",
        reason=" enough labels ",
        observed_count=10,
        minimum_required=1,
    )

    assert check.name == "training labels"
    assert check.reason == "enough labels"

    with pytest.raises(ValueError, match="name must be nonblank"):
        PredictionEvalReadinessCheckPayload(
            name=" ",
            status="pass",
            reason="enough labels",
            observed_count=10,
            minimum_required=1,
        )

    with pytest.raises(ValueError, match="reason must be nonblank"):
        PredictionEvalReadinessCheckPayload(
            name="training labels",
            status="pass",
            reason=" ",
            observed_count=10,
            minimum_required=1,
        )


def test_prediction_eval_readiness_check_rejects_boolean_counts() -> None:
    with pytest.raises(ValueError, match="observed_count must be an integer"):
        PredictionEvalReadinessCheckPayload(
            name="training labels",
            status="pass",
            reason="enough labels",
            observed_count=True,
            minimum_required=1,
        )

    with pytest.raises(ValueError, match="minimum_required must be an integer"):
        PredictionEvalReadinessCheckPayload(
            name="training labels",
            status="pass",
            reason="enough labels",
            observed_count=10,
            minimum_required=False,
        )


def test_prediction_eval_calibration_bin_rejects_invalid_shape() -> None:
    with pytest.raises(ValueError, match="bin_start must be before bin_end"):
        PredictionEvalCalibrationBinPayload(
            bin_start=0.5,
            bin_end=0.5,
            prediction_count=1,
            average_predicted_probability=0.5,
            actual_yea_rate=1.0,
        )


def test_prediction_eval_calibration_bin_rejects_boolean_prediction_count() -> None:
    with pytest.raises(ValueError, match="prediction_count must be an integer"):
        PredictionEvalCalibrationBinPayload(
            bin_start=0.0,
            bin_end=0.1,
            prediction_count=True,
            average_predicted_probability=0.05,
            actual_yea_rate=1.0,
        )


def test_learned_signal_model_rejects_mismatched_coefficient_names() -> None:
    with pytest.raises(ValueError, match="coefficient signal names must match"):
        LearnedSignalModelPayload(
            training_example_count=3,
            signal_names=["member_vote_history"],
            intercept=0.1,
            coefficients=[
                {
                    "signal_name": "committee_jurisdiction_overlap",
                    "coefficient": 0.2,
                }
            ],
        )


def test_prediction_eval_report_rejects_stale_learned_coefficients() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["learned_signal_coefficients"] = []

    with pytest.raises(ValueError, match="learned_signal_coefficients must match"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_stale_model_lifts() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["model_lifts"][0]["accuracy_delta"] = 999

    with pytest.raises(ValueError, match="model_lifts must match models"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_stale_top_level_coverage_aliases() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["feature_source_coverage"] = report["training_feature_source_coverage"]

    with pytest.raises(ValueError, match="feature_source_coverage must match evaluation"):
        PredictionEvalReportPayload.model_validate(report)

    report["feature_source_coverage"] = report["evaluation_feature_source_coverage"]
    report["bill_semantic_coverage"] = report["training_bill_semantic_coverage"]

    with pytest.raises(ValueError, match="bill_semantic_coverage must match evaluation"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_comparison_model_map_mismatch() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    del report["comparisons"][0]["model_probabilities"]["learned_signal_logistic"]

    with pytest.raises(ValueError, match="comparison model maps must align"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_unofficial_comparison_source_url() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["comparisons"][0]["source_url"] = "https://example.com/votes/3"

    with pytest.raises(ValueError, match="comparison source_url must be official"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_comparison_identity_drift_from_dataset() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["comparisons"][0]["event_key"] = "state_ca-ca_senate-119-1-3"
    report["comparisons"][0]["legislative_body_id"] = "ca_senate"

    with pytest.raises(ValueError, match="comparisons must match dataset evaluation examples"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_comparison_maps_missing_report_model() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=9),
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
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    comparison = report["comparisons"][0]
    for key in (
        "model_probabilities",
        "model_predicted_vote_options",
        "model_vote_probabilities",
        "model_correct",
        "model_skipped_reasons",
        "feature_signals_by_model",
        "feature_source_anchors_by_model",
        "unavailable_signals_by_model",
    ):
        del comparison[key]["learned_signal_logistic"]

    with pytest.raises(ValueError, match="comparison model maps must match models"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_source_coverage_that_disagrees_with_comparisons() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
            ),
        ],
        ontology_edges=[_current_transaction_edge()],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Permitting Reform Act",
                "short_title": "Energy Permitting Reform",
                "introduced_date": dt.date(2024, 12, 1),
                "latest_action_date": dt.date(2024, 12, 1),
                "sponsor_bioguide_id": "C000003",
                "sponsor_party": "D",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": dt.date(2024, 12, 15),
            }
        ],
        bill_semantics=[
            BillSemanticPayload(
                bill_key="119-hr-1",
                available_at=dt.date(2024, 12, 1),
                sectors=[
                    BillSectorSemanticPayload(
                        sector_id="energy",
                        label="Energy",
                        confidence=0.95,
                        rationale="Bill text concerns energy permitting.",
                    )
                ],
                industries=["utilities"],
                affected_entities=["utilities"],
                coalition_summary="Energy permitting coalition.",
                ideological_valence="mixed",
                source_anchors=[
                    SourceAnchor(
                        source_type="congress_bill",
                        source_id="119-hr-1",
                        url="https://api.congress.gov/v3/bill/119/hr/1?format=json",
                        label="Congress.gov bill 119-hr-1",
                    )
                ],
            )
        ],
    ).model_dump(mode="json")
    stale_rows = []
    for row in report["evaluation_feature_source_coverage"]:
        if (
            row["model_name"] == "ontology_signal_model"
            and row["signal_name"] == "financial_sector_overlap"
        ):
            row = {
                **row,
                "sourced_prediction_count": 0,
                "source_anchor_count": 0,
                "source_coverage_rate": 0.0,
                "url_sourced_prediction_count": 0,
                "url_source_anchor_count": 0,
                "url_source_coverage_rate": 0.0,
                "official_source_sourced_prediction_count": 0,
                "official_source_anchor_count": 0,
                "official_source_coverage_rate": 0.0,
            }
        stale_rows.append(row)
    report["evaluation_feature_source_coverage"] = stale_rows
    report["feature_source_coverage"] = stale_rows

    with pytest.raises(
        ValueError,
        match="evaluation_feature_source_coverage must match comparisons",
    ):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_backfill_samples_distinguish_legislative_body() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("CA-A001", vote_count=10, yea_count=8)
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
            },
            _feature_row("CA-A001", vote_count=10, yea_count=2)
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_senate",
            },
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
                "CA-A001",
                vote_event_id=7,
                vote_date=dt.date(2025, 2, 1),
                vote_option="yea",
                question="On Passage of A.B. 1",
            )
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            },
            _label_row(
                "CA-A001",
                vote_event_id=7,
                vote_date=dt.date(2025, 2, 1),
                vote_option="nay",
                question="On Passage of S.B. 1",
            )
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_senate",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250SB1",
            },
        ],
        ontology_edges=[],
        bill_signal_rows=[],
        bill_semantics=[],
    )

    recommendation = next(
        item
        for item in report.backfill_recommendations
        if item.action == "backfill_feature_source_urls"
    )
    state_samples = [
        sample
        for sample in recommendation.sample_cases
        if sample.jurisdiction_id == "state_ca" and sample.vote_event_id == 7
    ]
    assert [sample.legislative_body_id for sample in state_samples] == [
        "ca_assembly",
        "ca_senate",
    ]


def test_prediction_eval_report_rejects_stale_unavailable_signal_counts() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["unavailable_signal_counts"] = {}

    with pytest.raises(ValueError, match="unavailable_signal_counts must match comparisons"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_stale_top_failure_cases() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
                vote_option="nay",
            ),
            _label_row(
                "D000004",
                vote_event_id=4,
                vote_date=dt.date(2025, 2, 2),
                vote_option="yea",
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["top_failure_cases"] = []

    with pytest.raises(ValueError, match="top_failure_cases must match comparisons"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_stale_failure_groups() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
                vote_option="nay",
            ),
            _label_row(
                "D000004",
                vote_event_id=4,
                vote_date=dt.date(2025, 2, 2),
                vote_option="yea",
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["failure_groups"] = []

    with pytest.raises(ValueError, match="failure_groups must match comparisons"):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_missing_unavailable_signal_backfill() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    report["backfill_recommendations"] = [
        recommendation
        for recommendation in report["backfill_recommendations"]
        if recommendation["action"] != "load_fec_donations_and_member_crosswalks"
    ]

    with pytest.raises(
        ValueError,
        match="backfill_recommendations must include unavailable signal actions",
    ):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_stale_unavailable_signal_backfill_reason() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    for recommendation in report["backfill_recommendations"]:
        if recommendation["action"] == "load_fec_donations_and_member_crosswalks":
            recommendation["reason"] = "Generic backfill needed."
            break

    with pytest.raises(
        ValueError,
        match="backfill_recommendations must include unavailable signal actions",
    ):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_stale_unavailable_signal_backfill_samples() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    for recommendation in report["backfill_recommendations"]:
        if recommendation["action"] == "load_fec_donations_and_member_crosswalks":
            recommendation["sample_cases"] = [{"vote_event_id": 999}]
            break

    with pytest.raises(
        ValueError,
        match="backfill_recommendations must include unavailable signal sample cases",
    ):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_report_rejects_stale_source_url_backfill_samples() -> None:
    report = build_prediction_eval_report(
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
            _feature_row("C000003", vote_count=10, yea_count=8),
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
            ),
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    ).model_dump(mode="json")
    for recommendation in report["backfill_recommendations"]:
        if recommendation["action"] == "backfill_feature_source_urls":
            recommendation["sample_cases"] = [{"vote_event_id": 999}]
            recommendation["sample_vote_event_ids"] = [999]
            break

    with pytest.raises(
        ValueError,
        match="backfill_recommendations must include source URL sample cases",
    ):
        PredictionEvalReportPayload.model_validate(report)


def test_prediction_eval_comparison_rejects_internal_model_map_mismatch() -> None:
    with pytest.raises(ValueError, match="comparison model maps must align"):
        PredictionEvalComparisonPayload(
            vote_event_id=1,
            event_key="house-119-1-1",
            vote_date=dt.date(2025, 1, 5),
            chamber="house",
            question="On Passage",
            member_bioguide_id="A000001",
            actual_vote_option="yea",
            model_probabilities={"ontology_signal_model": 0.7},
            model_predicted_vote_options={"ontology_signal_model": "yea"},
            model_vote_probabilities={
                "ontology_signal_model": {
                    "yea": 0.7,
                    "nay": 0.3,
                    "present": 0.0,
                    "not_voting": 0.0,
                    "paired": 0.0,
                    "abstain": 0.0,
                }
            },
            model_correct={},
            model_skipped_reasons={"ontology_signal_model": None},
            feature_signals_by_model={"ontology_signal_model": {"member_vote_history": 0.7}},
            feature_source_anchors_by_model={"ontology_signal_model": {}},
            unavailable_signals_by_model={"ontology_signal_model": []},
        )


def test_prediction_eval_comparison_rejects_non_congress_row_without_session_id() -> None:
    with pytest.raises(ValueError, match="non-Congress comparisons require legislative_session_id"):
        PredictionEvalComparisonPayload(
            vote_event_id=1,
            event_key="state_ca-ca_assembly-2025_regular-1",
            jurisdiction_id="state_ca",
            legislative_body_id="ca_assembly",
            vote_date=dt.date(2025, 1, 5),
            chamber="assembly",
            question="On Passage",
            source_url=(
                "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1"
            ),
            member_bioguide_id="CA-A001",
            actual_vote_option="yea",
            model_probabilities={"ontology_signal_model": 0.7},
            model_predicted_vote_options={"ontology_signal_model": "yea"},
            model_vote_probabilities={"ontology_signal_model": {"yea": 0.7, "nay": 0.3}},
            model_correct={"ontology_signal_model": True},
            model_skipped_reasons={"ontology_signal_model": None},
            feature_signals_by_model={"ontology_signal_model": {"member_vote_history": 0.7}},
            feature_source_anchors_by_model={"ontology_signal_model": {}},
            unavailable_signals_by_model={"ontology_signal_model": []},
        )


def test_prediction_eval_comparison_rejects_non_congress_row_without_body_id() -> None:
    with pytest.raises(ValueError, match="non-Congress comparisons require legislative_body_id"):
        PredictionEvalComparisonPayload(
            vote_event_id=1,
            event_key="state_ca-ca_assembly-2025_regular-1",
            jurisdiction_id="state_ca",
            legislative_session_id="2025_regular",
            vote_date=dt.date(2025, 1, 5),
            chamber="assembly",
            question="On Passage",
            source_url=(
                "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1"
            ),
            member_bioguide_id="CA-A001",
            actual_vote_option="yea",
            model_probabilities={"ontology_signal_model": 0.7},
            model_predicted_vote_options={"ontology_signal_model": "yea"},
            model_vote_probabilities={"ontology_signal_model": {"yea": 0.7, "nay": 0.3}},
            model_correct={"ontology_signal_model": True},
            model_skipped_reasons={"ontology_signal_model": None},
            feature_signals_by_model={"ontology_signal_model": {"member_vote_history": 0.7}},
            feature_source_anchors_by_model={"ontology_signal_model": {}},
            unavailable_signals_by_model={"ontology_signal_model": []},
        )


def test_prediction_eval_comparison_rejects_orphan_source_anchor_signals() -> None:
    with pytest.raises(ValueError, match="feature source anchor signals must be present"):
        PredictionEvalComparisonPayload(
            vote_event_id=1,
            event_key="house-119-1-1",
            vote_date=dt.date(2025, 1, 5),
            chamber="house",
            question="On Passage",
            member_bioguide_id="A000001",
            actual_vote_option="yea",
            model_probabilities={"ontology_signal_model": 0.7},
            model_predicted_vote_options={"ontology_signal_model": "yea"},
            model_vote_probabilities={
                "ontology_signal_model": {
                    "yea": 0.7,
                    "nay": 0.3,
                    "present": 0.0,
                    "not_voting": 0.0,
                    "paired": 0.0,
                    "abstain": 0.0,
                }
            },
            model_correct={"ontology_signal_model": True},
            model_skipped_reasons={"ontology_signal_model": None},
            feature_signals_by_model={"ontology_signal_model": {"member_vote_history": 0.7}},
            feature_source_anchors_by_model={
                "ontology_signal_model": {
                    "not_a_signal": [
                        SourceAnchor(
                            source_type="financial_disclosure",
                            source_id="fd-1",
                            url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd.pdf",
                            label="Disclosure",
                        )
                    ]
                }
            },
            unavailable_signals_by_model={"ontology_signal_model": []},
        )


def test_prediction_eval_comparison_rejects_unofficial_feature_source_anchor_urls() -> None:
    with pytest.raises(
        ValueError,
        match="feature source anchors require official source URLs for member_vote_history",
    ):
        PredictionEvalComparisonPayload(
            vote_event_id=1,
            event_key="house-119-1-1",
            vote_date=dt.date(2025, 1, 5),
            chamber="house",
            question="On Passage",
            member_bioguide_id="A000001",
            actual_vote_option="yea",
            model_probabilities={"ontology_signal_model": 0.7},
            model_predicted_vote_options={"ontology_signal_model": "yea"},
            model_vote_probabilities={
                "ontology_signal_model": {
                    "yea": 0.7,
                    "nay": 0.3,
                    "present": 0.0,
                    "not_voting": 0.0,
                    "paired": 0.0,
                    "abstain": 0.0,
                }
            },
            model_correct={"ontology_signal_model": True},
            model_skipped_reasons={"ontology_signal_model": None},
            feature_signals_by_model={"ontology_signal_model": {"member_vote_history": 0.7}},
            feature_source_anchors_by_model={
                "ontology_signal_model": {
                    "member_vote_history": [
                        SourceAnchor(
                            source_type="vote_event",
                            source_id="house-118-2-999",
                            url="https://example.invalid/votes/2024999",
                            label="Latest vote",
                        )
                    ]
                }
            },
            unavailable_signals_by_model={"ontology_signal_model": []},
        )


def test_prediction_eval_comparison_rejects_legislative_anchor_without_context() -> None:
    with pytest.raises(
        ValueError,
        match="feature source anchors require legislative source context for member_vote_history",
    ):
        PredictionEvalComparisonPayload(
            vote_event_id=1,
            event_key="house-119-1-1",
            vote_date=dt.date(2025, 1, 5),
            chamber="house",
            question="On Passage",
            member_bioguide_id="A000001",
            actual_vote_option="yea",
            model_probabilities={"ontology_signal_model": 0.7},
            model_predicted_vote_options={"ontology_signal_model": "yea"},
            model_vote_probabilities={
                "ontology_signal_model": {
                    "yea": 0.7,
                    "nay": 0.3,
                    "present": 0.0,
                    "not_voting": 0.0,
                    "paired": 0.0,
                    "abstain": 0.0,
                }
            },
            model_correct={"ontology_signal_model": True},
            model_skipped_reasons={"ontology_signal_model": None},
            feature_signals_by_model={"ontology_signal_model": {"member_vote_history": 0.7}},
            feature_source_anchors_by_model={
                "ontology_signal_model": {
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
                }
            },
            unavailable_signals_by_model={"ontology_signal_model": []},
        )


def test_prediction_eval_source_coverage_counts_state_vote_alias_official_url() -> None:
    coverage = _feature_source_coverage_from_comparisons(
        [
            SimpleNamespace(
                jurisdiction_id="state_ca",
                feature_signals_by_model={
                    "ontology_signal_model": {
                        "member_vote_history": 1.0,
                    },
                },
                feature_source_anchors_by_model={
                    "ontology_signal_model": {
                        "member_vote_history": [
                            SimpleNamespace(
                                source_type="congress_vote",
                                source_id="state-ca-2025-20",
                                url=(
                                    "https://leginfo.legislature.ca.gov/"
                                    "faces/billVotesClient.xhtml?bill_id=202520260AB1"
                                ),
                            )
                        ],
                    },
                },
            )
        ],
        model_names=["ontology_signal_model"],
    )

    assert coverage[0].official_source_sourced_prediction_count == 1
    assert coverage[0].official_source_anchor_count == 1
    assert coverage[0].official_source_coverage_rate == 1.0


def test_prediction_eval_source_coverage_rejects_inconsistent_counts_and_rates() -> None:
    with pytest.raises(ValueError, match="source_coverage_rate must match"):
        PredictionFeatureSourceCoveragePayload(
            model_name="ontology_signal_model",
            signal_name="financial_exposure_energy",
            prediction_count=4,
            sourced_prediction_count=2,
            source_anchor_count=2,
            source_coverage_rate=0.25,
        )

    with pytest.raises(ValueError, match="url sourced predictions cannot exceed"):
        PredictionFeatureSourceCoveragePayload(
            model_name="ontology_signal_model",
            signal_name="financial_exposure_energy",
            prediction_count=4,
            sourced_prediction_count=2,
            source_anchor_count=2,
            source_coverage_rate=0.5,
            url_sourced_prediction_count=3,
            url_source_anchor_count=3,
            url_source_coverage_rate=0.75,
        )


def test_prediction_eval_source_coverage_rejects_boolean_counts() -> None:
    with pytest.raises(ValueError, match="prediction_count must be an integer"):
        PredictionFeatureSourceCoveragePayload(
            model_name="ontology_signal_model",
            signal_name="financial_exposure_energy",
            prediction_count=True,
            sourced_prediction_count=1,
            source_anchor_count=1,
            source_coverage_rate=1.0,
            url_sourced_prediction_count=1,
            url_source_anchor_count=1,
            url_source_coverage_rate=1.0,
            official_source_sourced_prediction_count=1,
            official_source_anchor_count=1,
            official_source_coverage_rate=1.0,
        )


def test_prediction_eval_data_quality_rejects_inconsistent_counts_and_rates() -> None:
    with pytest.raises(ValueError, match="model_ready_rate must match"):
        PredictionDatasetSplitQualityPayload(
            label_count=4,
            model_ready_example_count=3,
            skipped_count=1,
            feature_count=3,
            binary_yea_count=2,
            binary_nay_count=1,
            non_binary_vote_count=1,
            source_url_count=2,
            model_ready_rate=0.5,
            skipped_rate=0.25,
            source_url_coverage_rate=0.5,
        )

    with pytest.raises(ValueError, match="binary vote counts cannot exceed"):
        PredictionDatasetSplitQualityPayload(
            label_count=4,
            model_ready_example_count=3,
            skipped_count=1,
            feature_count=3,
            binary_yea_count=3,
            binary_nay_count=2,
            non_binary_vote_count=0,
            source_url_count=2,
            model_ready_rate=0.75,
            skipped_rate=0.25,
            source_url_coverage_rate=0.5,
        )


def test_prediction_eval_data_quality_rejects_boolean_counts() -> None:
    with pytest.raises(ValueError, match="label_count must be an integer"):
        PredictionDatasetSplitQualityPayload(
            label_count=True,
            model_ready_example_count=1,
            skipped_count=0,
            feature_count=3,
            binary_yea_count=1,
            binary_nay_count=0,
            non_binary_vote_count=0,
            source_url_count=1,
            model_ready_rate=1.0,
            skipped_rate=0.0,
            source_url_coverage_rate=1.0,
        )


def test_prediction_eval_bill_coverage_rejects_inconsistent_totals_and_rates() -> None:
    with pytest.raises(ValueError, match="required bill count must equal covered plus missing"):
        PredictionBillSemanticCoveragePayload(
            required_bill_count=3,
            covered_bill_count=1,
            missing_bill_count=1,
            available_semantic_count=1,
            coverage_rate=0.5,
            required_bill_keys=["118-hr-1", "119-hr-1", "119-hr-2"],
            covered_bill_keys=["119-hr-1"],
            missing_bill_keys=["118-hr-1"],
        )

    with pytest.raises(ValueError, match="coverage_rate must match"):
        PredictionBillMetadataCoveragePayload(
            required_bill_count=2,
            loaded_bill_count=1,
            missing_bill_count=1,
            coverage_rate=1.0,
            required_bill_keys=["118-hr-1", "119-hr-1"],
            loaded_bill_keys=["119-hr-1"],
            missing_bill_keys=["118-hr-1"],
        )


def test_prediction_eval_bill_coverage_rejects_boolean_counts() -> None:
    with pytest.raises(ValueError, match="required_bill_count must be an integer"):
        PredictionBillSemanticCoveragePayload(
            required_bill_count=True,
            covered_bill_count=1,
            missing_bill_count=0,
            available_semantic_count=1,
            coverage_rate=1.0,
            required_bill_keys=["119-hr-1"],
            covered_bill_keys=["119-hr-1"],
            missing_bill_keys=[],
        )

    with pytest.raises(ValueError, match="required_bill_count must be an integer"):
        PredictionBillMetadataCoveragePayload(
            required_bill_count=True,
            loaded_bill_count=1,
            missing_bill_count=0,
            coverage_rate=1.0,
            required_bill_keys=["119-hr-1"],
            loaded_bill_keys=["119-hr-1"],
            missing_bill_keys=[],
        )


def test_prediction_eval_bill_coverage_rejects_duplicate_or_blank_bill_keys() -> None:
    with pytest.raises(ValueError, match="required_bill_keys must be sorted, unique, and nonblank"):
        PredictionBillSemanticCoveragePayload(
            required_bill_count=2,
            covered_bill_count=2,
            missing_bill_count=0,
            available_semantic_count=2,
            coverage_rate=1.0,
            required_bill_keys=["119-hr-1", "119-hr-1"],
            covered_bill_keys=["119-hr-1", "119-hr-1"],
            missing_bill_keys=[],
        )

    with pytest.raises(ValueError, match="loaded_bill_keys must be sorted, unique, and nonblank"):
        PredictionBillMetadataCoveragePayload(
            required_bill_count=2,
            loaded_bill_count=2,
            missing_bill_count=0,
            coverage_rate=1.0,
            required_bill_keys=["119-hr-1", "119-hr-2"],
            loaded_bill_keys=["119-hr-1", " "],
            missing_bill_keys=[],
        )
