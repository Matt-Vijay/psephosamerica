from __future__ import annotations

import datetime as dt

import pytest

from src.export.contracts import SourceAnchor
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.prediction.backtest import (
    PredictionBacktestPayload,
    PredictionBacktestPredictionPayload,
    build_vote_baseline_backtest,
    build_vote_ontology_backtest,
)
from src.prediction.llm_semantics import BillSemanticPayload, BillSectorSemanticPayload


def _feature_row(
    bioguide_id: str = "A000001",
    *,
    vote_count: int = 10,
    yea_count: int = 7,
    nay_count: int = 3,
    present_count: int = 0,
    not_voting_count: int = 0,
    latest_vote_date: dt.date | None = dt.date(2024, 12, 15),
    vote_event_count: int = 5,
) -> dict[str, object]:
    return {
        "bioguide_id": bioguide_id,
        "vote_count": vote_count,
        "yea_count": yea_count,
        "nay_count": nay_count,
        "present_count": present_count,
        "not_voting_count": not_voting_count,
        "latest_vote_date": latest_vote_date,
        "vote_source_url_count": vote_count,
        "latest_vote_source_url": "https://clerk.house.gov/Votes/2024999",
        "vote_event_count": vote_event_count,
        "party": "D",
        "chamber": "house",
    }


def _label_row(
    bioguide_id: str = "A000001",
    *,
    vote_event_id: int = 101,
    vote_date: dt.date = dt.date(2025, 2, 1),
    vote_option: str = "yea",
    question: str = "On Passage of H.R. 1, Energy Permitting Reform",
) -> dict[str, object]:
    return {
        "vote_event_id": vote_event_id,
        "chamber": "house",
        "congress": 119,
        "session_number": 1,
        "roll_call_number": 7,
        "vote_date": vote_date,
        "question": question,
        "result": "Passed",
        "source_url": "https://clerk.house.gov/Votes/2025007",
        "bioguide_id": bioguide_id,
        "member_slug": "jane-doe",
        "member_name": "Jane Doe",
        "party": "D",
        "state": "CA",
        "vote_option": vote_option,
    }


def _anchor(source_type: str, source_id: str) -> SourceAnchor:
    return SourceAnchor(
        source_type=source_type,
        source_id=source_id,
        url="https://api.congress.gov/v3/committee/house/HSEC?format=json"
        if source_type == "committee_membership"
        else "https://www.fec.gov/data/receipts/individual-contributions/"
        if source_type == "fec_contribution"
        else "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd.pdf",
        label=source_id,
    )


def _ontology_edge(
    edge_type: str,
    subject: OntologyNodeRef,
    object_: OntologyNodeRef,
    source_type: str,
    attributes: dict[str, object] | None = None,
) -> OntologyEdgePayload:
    edge_attributes = {"as_of_date": "2024-12-01"} if attributes is None else attributes
    return OntologyEdgePayload(
        edge_id=f"{edge_type}-{subject.node_id}-{object_.node_id}-{source_type}",
        edge_type=edge_type,  # type: ignore[arg-type]
        subject=subject,
        object=object_,
        source_anchors=[_anchor(source_type, f"{source_type}-1")],
        attributes=edge_attributes,
    )


def _member_node() -> OntologyNodeRef:
    return OntologyNodeRef(node_type="member", node_id="A000001", label="Jane Doe")


def _committee_node() -> OntologyNodeRef:
    return OntologyNodeRef(node_type="committee", node_id="HSEC", label="Energy")


def _sector_node() -> OntologyNodeRef:
    return OntologyNodeRef(node_type="sector", node_id="energy", label="Energy")


def _ontology_edges() -> list[OntologyEdgePayload]:
    return [
        _ontology_edge(
            "member_committee_assignment",
            _member_node(),
            _committee_node(),
            "committee_membership",
        ),
        _ontology_edge(
            "committee_sector_jurisdiction",
            _committee_node(),
            _sector_node(),
            "committee_membership",
        ),
        _ontology_edge(
            "member_sector_holding_exposure",
            _member_node(),
            _sector_node(),
            "financial_disclosure",
        ),
        _ontology_edge(
            "member_sector_transaction_exposure",
            _member_node(),
            _sector_node(),
            "financial_disclosure",
            attributes={
                "transaction_type": "purchase",
                "transaction_date": "2024-11-15",
                "amount_min": 15001,
                "amount_max": 50000,
            },
        ),
    ]


def _bill_signal_rows() -> list[dict[str, object]]:
    return [
        {
            "congress": 119,
            "bill_type": "hr",
            "bill_number": 1,
            "title": "Energy Permitting Reform Act",
            "short_title": "Energy Permitting Reform",
            "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
            "introduced_date": dt.date(2024, 12, 1),
            "latest_action_date": dt.date(2024, 12, 1),
            "sponsor_bioguide_id": "A000001",
            "sponsor_party": "D",
            "sponsor_role": "cosponsor",
            "is_primary": False,
            "sponsor_date": dt.date(2024, 12, 15),
            "sponsor_source_url": (
                "https://api.congress.gov/v3/bill/119/hr/1/cosponsors?format=json"
            ),
        },
        {
            "congress": 119,
            "bill_type": "hr",
            "bill_number": 1,
            "title": "Energy Permitting Reform Act",
            "short_title": "Energy Permitting Reform",
            "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
            "introduced_date": dt.date(2024, 12, 1),
            "latest_action_date": dt.date(2024, 12, 1),
            "sponsor_bioguide_id": "B000002",
            "sponsor_party": "D",
            "sponsor_role": "primary",
            "is_primary": True,
            "sponsor_date": dt.date(2024, 12, 15),
            "sponsor_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
        },
    ]


def test_build_vote_baseline_backtest_scores_future_votes_from_cutoff_features() -> None:
    result = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2026, 4, 25),
        feature_rows=[
            _feature_row()
            | {
                "latest_vote_source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB1"
                )
            }
        ],
        label_rows=[_label_row()],
    )

    assert result.feature_cutoff == dt.date(2024, 12, 31)
    assert result.metrics.evaluated_count == 1
    assert result.metrics.correct_count == 1
    assert result.metrics.accuracy == 1.0
    assert result.metrics.brier_score == pytest.approx(0.09)
    assert result.metrics.log_loss == pytest.approx(0.35667494)
    assert result.predictions[0].predicted_vote_option == "yea"
    assert result.predictions[0].predicted_probability_yea == 0.7
    assert result.predictions[0].predicted_vote_probabilities == {
        "yea": 0.7,
        "nay": 0.3,
        "present": 0.0,
        "not_voting": 0.0,
        "paired": 0.0,
        "abstain": 0.0,
    }
    assert result.predictions[0].log_loss == pytest.approx(0.35667494)


def test_build_vote_baseline_backtest_scores_non_binary_vote_options() -> None:
    result = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            _feature_row(
                vote_count=10,
                yea_count=1,
                nay_count=1,
                present_count=7,
                not_voting_count=1,
            )
        ],
        label_rows=[_label_row(vote_option="present")],
    )

    prediction = result.predictions[0]
    assert result.metrics.evaluated_count == 1
    assert result.metrics.skipped_count == 0
    assert result.metrics.correct_count == 1
    assert result.metrics.brier_score is None
    assert result.metrics.log_loss is None
    assert prediction.actual_vote_option == "present"
    assert prediction.predicted_vote_option == "present"
    assert prediction.predicted_probability_yea == 0.1
    assert prediction.predicted_vote_probabilities["present"] == 0.7
    assert prediction.correct is True
    assert prediction.skipped_reason is None
    assert prediction.brier_score is None
    assert prediction.log_loss is None


def test_build_vote_baseline_backtest_skips_members_without_prior_votes() -> None:
    result = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row(vote_count=0, yea_count=0, nay_count=0)],
        label_rows=[_label_row()],
    )

    assert result.metrics.evaluated_count == 0
    assert result.metrics.skipped_count == 1
    assert result.predictions[0].skipped_reason == "missing_pre_cutoff_vote_history"
    assert result.predictions[0].correct is None


def test_build_vote_baseline_backtest_does_not_count_boolean_vote_history() -> None:
    result = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "vote_count": True,
                "yea_count": True,
                "vote_event_count": True,
            },
        ],
        label_rows=[_label_row()],
    )

    assert result.feature_vote_event_count == 0
    assert result.metrics.evaluated_count == 0
    assert result.metrics.skipped_count == 1
    assert result.predictions[0].feature_vote_count == 0
    assert result.predictions[0].skipped_reason == "missing_pre_cutoff_vote_history"


def test_build_vote_baseline_backtest_rejects_boolean_label_vote_event_id() -> None:
    with pytest.raises(ValueError, match="label_rows\\[0\\]\\.vote_event_id must be an integer"):
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row()],
            label_rows=[{**_label_row(), "vote_event_id": True}],
        )


def test_build_vote_baseline_backtest_rejects_boolean_label_event_key_parts() -> None:
    with pytest.raises(ValueError, match="label_rows\\[0\\]\\.congress must be an integer"):
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row()],
            label_rows=[{**_label_row(), "congress": True}],
        )

    with pytest.raises(ValueError, match="label_rows\\[0\\]\\.roll_call_number must be an integer"):
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row()],
            label_rows=[{**_label_row(), "roll_call_number": True}],
        )


def test_build_vote_baseline_backtest_preserves_jurisdiction_context() -> None:
    result = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
            }
        ],
        label_rows=[
            {
                **_label_row(),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            }
        ],
    )

    prediction = result.predictions[0]
    assert prediction.jurisdiction_id == "state_ca"
    assert prediction.legislative_body_id == "ca_assembly"
    assert prediction.legislative_session_id == "2025_regular"
    assert prediction.event_key == "state_ca-ca_assembly-2025_regular-7"
    assert prediction.bill_key == "119-hr-1"
    assert prediction.bill_context_key == "state_ca:119-hr-1"


def test_build_vote_baseline_backtest_matches_features_within_jurisdiction() -> None:
    result = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(vote_count=10, yea_count=10, nay_count=0),
                "jurisdiction_id": "us_congress",
            }
        ],
        label_rows=[
            {
                **_label_row(),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            }
        ],
    )

    prediction = result.predictions[0]
    assert result.metrics.evaluated_count == 0
    assert result.metrics.skipped_count == 1
    assert prediction.feature_vote_count == 0
    assert prediction.predicted_probability_yea is None
    assert prediction.skipped_reason == "missing_pre_cutoff_vote_history"


def test_build_vote_baseline_backtest_matches_features_within_legislative_body() -> None:
    result = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(vote_count=10, yea_count=10, nay_count=0),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
            }
        ],
        label_rows=[
            {
                **_label_row(),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_senate",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250SB1",
            }
        ],
    )

    prediction = result.predictions[0]
    assert result.metrics.evaluated_count == 0
    assert result.metrics.skipped_count == 1
    assert prediction.feature_vote_count == 0
    assert prediction.predicted_probability_yea is None
    assert prediction.skipped_reason == "missing_pre_cutoff_vote_history"


def test_build_vote_baseline_backtest_does_not_match_us_congress_across_chambers() -> None:
    result = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(vote_count=10, yea_count=10, nay_count=0),
                "chamber": "house",
            }
        ],
        label_rows=[{**_label_row(), "chamber": "senate"}],
    )

    prediction = result.predictions[0]
    assert result.metrics.evaluated_count == 0
    assert result.metrics.skipped_count == 1
    assert prediction.feature_vote_count == 0
    assert prediction.predicted_probability_yea is None
    assert prediction.skipped_reason == "missing_pre_cutoff_vote_history"


def test_build_vote_baseline_backtest_synthesizes_house_vote_source_url() -> None:
    result = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[{**_label_row(), "source_url": None}],
    )

    assert result.predictions[0].source_url == "https://clerk.house.gov/Votes/2025007"


def test_build_vote_baseline_backtest_synthesizes_senate_vote_source_url() -> None:
    result = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[{**_feature_row(), "chamber": "senate"}],
        label_rows=[
            {
                **_label_row(),
                "chamber": "senate",
                "roll_call_number": 7,
                "source_url": None,
            }
        ],
    )

    assert (
        result.predictions[0].source_url
        == "https://www.senate.gov/legislative/LIS/roll_call_votes/vote1191/vote_119_1_00007.xml"
    )


def test_build_vote_baseline_backtest_rejects_non_congress_row_without_session_id() -> None:
    with pytest.raises(ValueError, match="non-Congress predictions require legislative_session_id"):
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[
                {
                    **_feature_row(),
                    "jurisdiction_id": "state_ca",
                    "legislative_body_id": "ca_assembly",
                }
            ],
            label_rows=[
                {
                    **_label_row(),
                    "jurisdiction_id": "state_ca",
                    "legislative_body_id": "ca_assembly",
                }
            ],
        )


def test_build_vote_baseline_backtest_rejects_non_congress_row_without_body_id() -> None:
    with pytest.raises(ValueError, match="non-Congress predictions require legislative_body_id"):
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[
                {
                    **_feature_row(),
                    "jurisdiction_id": "state_ca",
                    "legislative_session_id": "2025_regular",
                }
            ],
            label_rows=[
                {
                    **_label_row(),
                    "jurisdiction_id": "state_ca",
                    "legislative_session_id": "2025_regular",
                }
            ],
        )


def test_build_vote_ontology_backtest_uses_legislative_bill_anchor_for_non_congress_rows() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            _feature_row()
            | {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "latest_vote_source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB1"
                ),
            }
        ],
        label_rows=[
            {
                **_label_row(question="On Passage of H.R. 1, Energy Reliability"),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            }
        ],
        ontology_edges=[],
        bill_signal_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Reliability Act",
                "short_title": "Energy Reliability",
                "bill_source_url": "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB1",
                "introduced_date": dt.date(2024, 12, 1),
                "latest_action_date": dt.date(2024, 12, 1),
                "sponsor_bioguide_id": "A000001",
                "sponsor_party": "D",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": dt.date(2024, 12, 15),
            }
        ],
    )

    anchors = result.predictions[0].feature_source_anchors["bill_issue_sector_overlap"]
    assert anchors[0].source_type == "legislative_bill"
    assert anchors[0].source_id == "state_ca:119-hr-1"
    assert anchors[0].label == "Legislative bill state_ca:119-hr-1"
    assert anchors[0].jurisdiction_id == "state_ca"
    assert anchors[0].legislative_body_id == "ca_assembly"
    assert anchors[0].legislative_session_id == "2025_regular"


def test_build_vote_ontology_backtest_synthesizes_congress_bill_anchor_url() -> None:
    bill_rows = [
        {key: value for key, value in row.items() if key != "bill_source_url"}
        for row in _bill_signal_rows()
    ]

    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1")],
        ontology_edges=_ontology_edges(),
        bill_signal_rows=bill_rows,
    )

    anchors = result.predictions[0].feature_source_anchors["bill_issue_sector_overlap"]
    assert anchors[0].source_type == "congress_bill"
    assert anchors[0].source_id == "119-hr-1"
    assert anchors[0].url == "https://api.congress.gov/v3/bill/119/hr/1?format=json"


def test_build_vote_ontology_backtest_replaces_unofficial_congress_bill_anchor_url() -> None:
    bill_rows = [
        {
            **row,
            "bill_source_url": "https://example.invalid/bill/119/hr/1",
        }
        for row in _bill_signal_rows()
    ]

    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1")],
        ontology_edges=_ontology_edges(),
        bill_signal_rows=bill_rows,
    )

    anchors = result.predictions[0].feature_source_anchors["bill_issue_sector_overlap"]
    assert anchors[0].source_type == "congress_bill"
    assert anchors[0].source_id == "119-hr-1"
    assert anchors[0].url == "https://api.congress.gov/v3/bill/119/hr/1?format=json"


def test_build_vote_ontology_backtest_uses_legislative_vote_anchor_for_non_congress_history() -> (
    None
):
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "latest_vote_source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            }
        ],
        label_rows=[
            {
                **_label_row(question="On Passage of A.B. 1, Energy Reliability"),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            }
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    )

    anchors = result.predictions[0].feature_source_anchors["member_vote_history"]
    assert anchors[0].source_type == "legislative_vote"
    assert anchors[0].url == (
        "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1"
    )


def test_build_vote_ontology_backtest_vote_history_anchor_uses_feature_session() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "jurisdiction_id": "us_congress",
                "legislative_body_id": "us_congress_house",
                "latest_legislative_session_id": "congress_118_session_2",
                "latest_vote_event_key": "house-118-2-701",
                "latest_vote_source_url": "https://clerk.house.gov/Votes/2024701",
            }
        ],
        label_rows=[
            {
                **_label_row(),
                "jurisdiction_id": "us_congress",
                "legislative_body_id": "us_congress_house",
                "legislative_session_id": "congress_119_session_1",
                "source_url": "https://clerk.house.gov/Votes/2025007",
            }
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    )

    anchors = result.predictions[0].feature_source_anchors["member_vote_history"]
    assert anchors[0].source_id == "house-118-2-701"
    assert anchors[0].jurisdiction_id == "us_congress"
    assert anchors[0].legislative_body_id == "us_congress_house"
    assert anchors[0].legislative_session_id == "congress_118_session_2"
    assert anchors[0].url == "https://clerk.house.gov/Votes/2024701"


def test_build_vote_ontology_backtest_normalizes_congress_vote_alias_for_state_history() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "latest_vote_source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            }
        ],
        label_rows=[
            {
                **_label_row(question="On Passage of A.B. 1, Energy Reliability"),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_type": "congress_vote",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            }
        ],
        ontology_edges=[],
        bill_signal_rows=[],
    )

    anchors = result.predictions[0].feature_source_anchors["member_vote_history"]
    assert anchors[0].source_type == "legislative_vote"
    assert anchors[0].url == (
        "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1"
    )


def test_build_vote_ontology_backtest_replaces_unofficial_latest_vote_anchor_url() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "latest_vote_event_key": "house-118-2-999",
                "latest_vote_source_url": "https://example.invalid/votes/2024999",
            }
        ],
        label_rows=[_label_row()],
        ontology_edges=[],
        bill_signal_rows=[],
    )

    anchors = result.predictions[0].feature_source_anchors["member_vote_history"]
    assert anchors[0].source_type == "vote_event"
    assert anchors[0].source_id == "house-118-2-999"
    assert anchors[0].url == "https://clerk.house.gov/Votes/2024999"


def test_build_vote_ontology_backtest_uses_latest_vote_event_key_for_history_anchor() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "latest_vote_event_key": "house-118-2-999",
            }
        ],
        label_rows=[_label_row()],
        ontology_edges=[],
        bill_signal_rows=[],
    )

    anchors = result.predictions[0].feature_source_anchors["member_vote_history"]
    assert anchors[0].source_id == "house-118-2-999"


def test_build_vote_ontology_backtest_synthesizes_house_latest_vote_history_anchor_url() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "latest_vote_date": dt.date(2024, 12, 15),
                "latest_vote_event_key": "house-118-2-999",
                "latest_vote_source_url": None,
            }
        ],
        label_rows=[_label_row()],
        ontology_edges=[],
        bill_signal_rows=[],
    )

    anchors = result.predictions[0].feature_source_anchors["member_vote_history"]
    assert anchors[0].source_type == "vote_event"
    assert anchors[0].source_id == "house-118-2-999"
    assert anchors[0].url == "https://clerk.house.gov/Votes/2024999"


def test_build_vote_ontology_backtest_synthesizes_senate_latest_vote_history_anchor_url() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "chamber": "senate",
                "latest_vote_event_key": "senate-118-2-41",
                "latest_vote_source_url": None,
            }
        ],
        label_rows=[{**_label_row(), "chamber": "senate"}],
        ontology_edges=[],
        bill_signal_rows=[],
    )

    anchors = result.predictions[0].feature_source_anchors["member_vote_history"]
    assert anchors[0].source_type == "vote_event"
    assert anchors[0].source_id == "senate-118-2-41"
    assert anchors[0].url == (
        "https://www.senate.gov/legislative/LIS/roll_call_votes/vote1182/vote_118_2_00041.xml"
    )


def test_prediction_backtest_payload_rejects_metrics_that_do_not_match_predictions() -> None:
    payload = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row()],
    ).model_dump(mode="json")
    payload["metrics"]["correct_count"] = 0
    payload["metrics"]["accuracy"] = 0.0

    with pytest.raises(ValueError, match="metrics.correct_count must match predictions"):
        PredictionBacktestPayload.model_validate(payload)


def test_prediction_backtest_payload_rejects_boolean_metric_counts() -> None:
    payload = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row()],
    ).model_dump(mode="json")
    payload["metrics"]["label_count"] = True

    with pytest.raises(ValueError, match="label_count must be an integer"):
        PredictionBacktestPayload.model_validate(payload)


def test_prediction_backtest_payload_rejects_boolean_top_level_counts() -> None:
    payload = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row()],
    ).model_dump(mode="json")
    payload["feature_vote_event_count"] = True

    with pytest.raises(ValueError, match="feature_vote_event_count must be an integer"):
        PredictionBacktestPayload.model_validate(payload)


def test_prediction_backtest_payload_rejects_vote_event_count_mismatch() -> None:
    payload = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row()],
    ).model_dump(mode="json")
    payload["label_vote_event_count"] = 2

    with pytest.raises(ValueError, match="label_vote_event_count must match predictions"):
        PredictionBacktestPayload.model_validate(payload)


def test_prediction_backtest_payload_rejects_prediction_outside_label_window() -> None:
    payload = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row()],
    ).model_dump(mode="json")
    payload["predictions"][0]["vote_date"] = "2024-12-31"

    with pytest.raises(ValueError, match="predictions must stay inside the label window"):
        PredictionBacktestPayload.model_validate(payload)


def test_prediction_payload_rejects_orphan_feature_source_anchors() -> None:
    payload = (
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row()],
            label_rows=[_label_row()],
        )
        .predictions[0]
        .model_dump(mode="json")
    )
    payload["feature_source_anchors"] = {
        "not_a_feature_signal": [_anchor("financial_disclosure", "fd-orphan").model_dump()]
    }

    with pytest.raises(ValueError, match="feature_source_anchors keys must be present"):
        PredictionBacktestPredictionPayload.model_validate(payload)


def test_prediction_payload_rejects_empty_feature_source_anchor_entries() -> None:
    payload = (
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row()],
            label_rows=[_label_row()],
        )
        .predictions[0]
        .model_dump(mode="json")
    )
    payload["feature_signals"] = {"member_vote_history": 0.7}
    signal_name = "member_vote_history"
    payload["feature_source_anchors"] = {signal_name: []}

    with pytest.raises(ValueError, match="feature_source_anchors entries must not be empty"):
        PredictionBacktestPredictionPayload.model_validate(payload)


def test_prediction_payload_rejects_unofficial_vote_and_feature_source_urls() -> None:
    payload = (
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row()],
            label_rows=[_label_row()],
        )
        .predictions[0]
        .model_dump(mode="json")
    )
    payload["source_url"] = "https://example.com/votes/2025007"

    with pytest.raises(ValueError, match="source_url must be an official vote source URL"):
        PredictionBacktestPredictionPayload.model_validate(payload)

    payload["source_url"] = "https://clerk.house.gov/Votes/2025007"
    payload["feature_signals"] = {"member_vote_history": 0.7}
    payload["feature_source_anchors"] = {
        "member_vote_history": [
            SourceAnchor(
                source_type="vote_event",
                source_id="bad-vote",
                url="https://example.com/votes/2024999",
                label="bad vote source",
            ).model_dump()
        ]
    }

    with pytest.raises(
        ValueError,
        match="feature_source_anchors require official source URLs for member_vote_history",
    ):
        PredictionBacktestPredictionPayload.model_validate(payload)


def test_prediction_payload_rejects_legislative_feature_anchor_without_context() -> None:
    payload = (
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row()],
            label_rows=[_label_row()],
        )
        .predictions[0]
        .model_dump(mode="json")
    )
    payload["feature_signals"] = {"member_vote_history": 0.7}
    payload["feature_source_anchors"] = {
        "member_vote_history": [
            SourceAnchor(
                source_type="legislative_vote",
                source_id="state-vote-1",
                url=(
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB1"
                ),
                label="State vote",
            ).model_dump()
        ]
    }

    with pytest.raises(
        ValueError,
        match="feature_source_anchors require legislative source context for member_vote_history",
    ):
        PredictionBacktestPredictionPayload.model_validate(payload)


def test_prediction_payload_rejects_stale_event_key() -> None:
    payload = (
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row()],
            label_rows=[_label_row()],
        )
        .predictions[0]
        .model_dump(mode="json")
    )
    payload["event_key"] = "house-119-1-999"

    with pytest.raises(ValueError, match="event_key must match jurisdiction and roll call"):
        PredictionBacktestPredictionPayload.model_validate(payload)

    payload["event_key"] = "state_ca-ca_assembly-119-1-7"
    payload["jurisdiction_id"] = "state_ca"
    payload["legislative_body_id"] = "ca_senate"

    with pytest.raises(ValueError, match="event_key must match jurisdiction and roll call"):
        PredictionBacktestPredictionPayload.model_validate(payload)


@pytest.mark.parametrize(
    "field_name",
    [
        "vote_event_id",
        "congress",
        "session_number",
        "roll_call_number",
        "feature_vote_count",
    ],
)
def test_prediction_payload_rejects_boolean_identity_and_count_fields(field_name: str) -> None:
    payload = (
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row()],
            label_rows=[_label_row()],
        )
        .predictions[0]
        .model_dump(mode="json")
    )
    payload[field_name] = True

    with pytest.raises(ValueError, match=f"{field_name} must be an integer"):
        PredictionBacktestPredictionPayload.model_validate(payload)


def test_prediction_payload_rejects_blank_or_duplicate_unavailable_signals() -> None:
    payload = (
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row()],
            label_rows=[_label_row()],
        )
        .predictions[0]
        .model_dump(mode="json")
    )
    payload["unavailable_signals"] = ["donation_industry_alignment", " "]

    with pytest.raises(
        ValueError, match="unavailable_signals must be sorted, unique, and nonblank"
    ):
        PredictionBacktestPredictionPayload.model_validate(payload)

    payload["unavailable_signals"] = [
        "donation_industry_alignment",
        "donation_industry_alignment",
    ]

    with pytest.raises(
        ValueError, match="unavailable_signals must be sorted, unique, and nonblank"
    ):
        PredictionBacktestPredictionPayload.model_validate(payload)


def test_prediction_payload_normalizes_and_rejects_blank_skipped_reason() -> None:
    payload = (
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row(vote_count=0, yea_count=0, nay_count=0)],
            label_rows=[_label_row()],
        )
        .predictions[0]
        .model_dump(mode="json")
    )
    payload["skipped_reason"] = " missing_pre_cutoff_vote_history "

    prediction = PredictionBacktestPredictionPayload.model_validate(payload)
    assert prediction.skipped_reason == "missing_pre_cutoff_vote_history"

    payload["skipped_reason"] = " "

    with pytest.raises(ValueError, match="skipped predictions require skipped_reason"):
        PredictionBacktestPredictionPayload.model_validate(payload)


def test_prediction_backtest_payload_rejects_aggregate_score_mismatch() -> None:
    payload = build_vote_baseline_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row()],
    ).model_dump(mode="json")
    payload["metrics"]["brier_score"] = 0.5

    with pytest.raises(ValueError, match="metrics.brier_score must match predictions"):
        PredictionBacktestPayload.model_validate(payload)


def test_build_vote_baseline_backtest_rejects_feature_leakage_after_cutoff() -> None:
    with pytest.raises(ValueError, match="feature rows must not include votes after cutoff"):
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row(latest_vote_date=dt.date(2025, 1, 3))],
            label_rows=[_label_row()],
        )


def test_build_vote_baseline_backtest_rejects_label_rows_outside_window() -> None:
    with pytest.raises(ValueError, match="label rows must stay inside the evaluation window"):
        build_vote_baseline_backtest(
            feature_cutoff=dt.date(2024, 12, 31),
            label_start=dt.date(2025, 1, 1),
            label_end=dt.date(2025, 12, 31),
            feature_rows=[_feature_row()],
            label_rows=[_label_row(vote_date=dt.date(2024, 12, 31))],
        )


def test_build_vote_ontology_backtest_uses_rich_signals_over_vote_baseline() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2026, 4, 25),
        feature_rows=[
            _feature_row(
                vote_count=10,
                yea_count=3,
                nay_count=7,
                latest_vote_date=dt.date(2024, 12, 1),
            ),
            {
                **_feature_row(
                    "B000002",
                    vote_count=10,
                    yea_count=8,
                    nay_count=2,
                    latest_vote_date=dt.date(2024, 12, 1),
                ),
                "party": "D",
                "chamber": "house",
            },
        ],
        label_rows=[_label_row()],
        ontology_edges=_ontology_edges(),
        bill_signal_rows=_bill_signal_rows(),
    )

    prediction = result.predictions[0]
    assert result.model_name == "ontology_signal_model"
    assert prediction.predicted_vote_option == "yea"
    assert prediction.bill_key == "119-hr-1"
    assert prediction.predicted_probability_yea is not None
    assert prediction.predicted_probability_yea > 0.5
    assert prediction.feature_signals["member_vote_history"] == 0.3
    assert prediction.feature_signals["party_chamber_baseline"] == 0.55
    assert prediction.feature_signals["sponsor_cosponsor_alignment"] == 1.0
    assert prediction.feature_signals["bill_coalition_signal"] == 0.0
    assert prediction.feature_signals["bill_issue_sector_overlap"] == 1.0
    assert prediction.feature_signals["committee_jurisdiction_overlap"] == 1.0
    assert prediction.feature_signals["financial_sector_overlap"] == 1.0
    assert prediction.feature_signals["holding_recency"] == 1.0
    assert prediction.feature_signals["transaction_recency"] == 1.0
    assert prediction.feature_signals["source_anchor_strength"] == 1.0
    assert prediction.feature_source_anchors["bill_issue_sector_overlap"][0].source_type == (
        "congress_bill"
    )
    assert any(
        anchor.source_id == "119-hr-1:sponsor:A000001:cosponsor"
        for anchor in prediction.feature_source_anchors["sponsor_cosponsor_alignment"]
    )
    assert prediction.feature_source_anchors["member_vote_history"][0].source_type == ("vote_event")
    assert prediction.feature_source_anchors["party_chamber_baseline"][0].source_type == (
        "vote_event"
    )
    assert prediction.feature_source_anchors["committee_jurisdiction_overlap"][0].source_type == (
        "committee_membership"
    )
    assert prediction.feature_source_anchors["financial_sector_overlap"][0].source_type == (
        "financial_disclosure"
    )
    assert prediction.feature_source_anchors["holding_recency"][0].source_type == (
        "financial_disclosure"
    )
    assert prediction.feature_source_anchors["transaction_recency"][0].source_type == (
        "financial_disclosure"
    )


def test_build_vote_ontology_backtest_marks_unavailable_donation_and_statement_signals() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row()],
        ontology_edges=[],
        bill_signal_rows=[],
    )

    prediction = result.predictions[0]
    assert "donation_industry_alignment" in prediction.unavailable_signals
    assert "public_statement_alignment" in prediction.unavailable_signals


def test_build_vote_ontology_backtest_filters_future_donation_and_statement_signals() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row()],
        ontology_edges=_ontology_edges(),
        bill_signal_rows=_bill_signal_rows(),
        contribution_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.9,
                "contribution_date": dt.date(2025, 1, 2),
                "source_anchors": [
                    SourceAnchor(
                        source_type="fec_contribution",
                        source_id="fec-future",
                        url="https://www.fec.gov/data/receipts/future/",
                        label="future FEC contribution",
                    )
                ],
            }
        ],
        statement_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.8,
                "statement_date": dt.date(2025, 1, 2),
                "source_anchors": [
                    SourceAnchor(
                        source_type="public_statement",
                        source_id="statement-future",
                        url="https://example.house.gov/future-statement",
                        label="future statement",
                    )
                ],
            }
        ],
    )

    prediction = result.predictions[0]
    assert "donation_industry_alignment" in prediction.unavailable_signals
    assert "public_statement_alignment" in prediction.unavailable_signals
    assert "donation_industry_alignment" not in prediction.feature_signals
    assert "public_statement_alignment" not in prediction.feature_signals
    assert "donation_industry_alignment" not in prediction.feature_source_anchors
    assert "public_statement_alignment" not in prediction.feature_source_anchors


def test_build_vote_ontology_backtest_uses_precutoff_donation_and_statement_signals() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row()],
        ontology_edges=_ontology_edges(),
        bill_signal_rows=_bill_signal_rows(),
        contribution_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.9,
                "contribution_date": dt.date(2024, 12, 30),
                "source_anchors": [
                    SourceAnchor(
                        source_type="fec_contribution",
                        source_id="fec-1",
                        url="https://www.fec.gov/data/receipts/1/",
                        label="FEC contribution",
                    )
                ],
            }
        ],
        statement_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.8,
                "statement_date": dt.date(2024, 12, 30),
                "source_anchors": [
                    SourceAnchor(
                        source_type="public_statement",
                        source_id="statement-1",
                        url="https://example.house.gov/statement",
                        label="official statement",
                    )
                ],
            }
        ],
    )

    prediction = result.predictions[0]
    assert prediction.feature_signals["donation_industry_alignment"] == 0.9
    assert prediction.feature_signals["public_statement_alignment"] == 0.8
    assert prediction.feature_source_anchors["donation_industry_alignment"][0].source_id == (
        "fec-1"
    )
    assert prediction.feature_source_anchors["public_statement_alignment"][0].source_id == (
        "statement-1"
    )


def test_build_vote_ontology_backtest_matches_auxiliary_signals_within_jurisdiction() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
            }
        ],
        label_rows=[
            {
                **_label_row(question="On Passage of H.R. 1, Energy Bill"),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            }
        ],
        ontology_edges=[],
        bill_signal_rows=[
            {
                **_bill_signal_rows()[0],
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "bill_source_url": "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB1",
            }
        ],
        contribution_signal_rows=[
            {
                "jurisdiction_id": "us_congress",
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.9,
                "contribution_date": dt.date(2024, 12, 30),
                "source_anchors": [_anchor("fec_contribution", "fec-congress")],
            }
        ],
        statement_signal_rows=[
            {
                "jurisdiction_id": "us_congress",
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.8,
                "statement_date": dt.date(2024, 12, 30),
                "source_anchors": [_anchor("public_statement", "stmt-congress")],
            }
        ],
    )

    prediction = result.predictions[0]
    assert "donation_industry_alignment" not in prediction.feature_signals
    assert "public_statement_alignment" not in prediction.feature_signals
    assert "donation_industry_alignment" not in prediction.feature_source_anchors
    assert "public_statement_alignment" not in prediction.feature_source_anchors
    assert "donation_industry_alignment" in prediction.unavailable_signals
    assert "public_statement_alignment" in prediction.unavailable_signals


def test_build_vote_ontology_backtest_does_not_reuse_congress_financial_edges_for_state_labels() -> (
    None
):
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
            }
        ],
        label_rows=[
            {
                **_label_row(question="On Passage of H.R. 1, Energy Bill"),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            }
        ],
        ontology_edges=[
            _ontology_edge(
                "member_sector_holding_exposure",
                _member_node(),
                _sector_node(),
                "financial_disclosure",
            ),
            _ontology_edge(
                "member_sector_transaction_exposure",
                _member_node(),
                _sector_node(),
                "financial_disclosure",
                attributes={"transaction_date": "2024-11-15"},
            ),
        ],
        bill_signal_rows=[
            {
                **_bill_signal_rows()[0],
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "bill_source_url": "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB1",
            }
        ],
    )

    prediction = result.predictions[0]
    assert prediction.feature_signals["financial_sector_overlap"] == 0.0
    assert prediction.feature_signals["holding_recency"] == 0.0
    assert prediction.feature_signals["transaction_recency"] == 0.0
    assert "financial_sector_overlap" not in prediction.feature_source_anchors
    assert "holding_recency" not in prediction.feature_source_anchors
    assert "transaction_recency" not in prediction.feature_source_anchors


def test_build_vote_ontology_backtest_matches_sponsors_within_jurisdiction() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(yea_count=5, nay_count=5),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "party": "I",
            }
        ],
        label_rows=[
            {
                **_label_row(question="On Passage of H.R. 1, Energy Bill"),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
                "party": "I",
            }
        ],
        ontology_edges=[],
        bill_signal_rows=[
            {
                **_bill_signal_rows()[0],
                "jurisdiction_id": "us_congress",
                "sponsor_party": "D",
                "sponsor_bioguide_id": "A000001",
            }
        ],
    )

    prediction = result.predictions[0]
    assert prediction.feature_signals["sponsor_cosponsor_alignment"] == 0.5


def test_build_vote_ontology_backtest_matches_bill_signals_within_jurisdiction() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(yea_count=5, nay_count=5),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
            }
        ],
        label_rows=[
            {
                **_label_row(question="On Passage of H.R. 1, Energy Bill"),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
            }
        ],
        ontology_edges=[],
        bill_signal_rows=[
            {
                **_bill_signal_rows()[0],
                "jurisdiction_id": "us_congress",
                "title": "Energy Bill",
                "short_title": "Energy Bill",
            }
        ],
    )

    prediction = result.predictions[0]
    assert prediction.feature_signals["bill_issue_sector_overlap"] == 0.0
    assert prediction.feature_signals["sponsor_cosponsor_alignment"] == 0.5
    assert "bill_issue_sector_overlap" not in prediction.feature_source_anchors
    assert "sponsor_cosponsor_alignment" not in prediction.feature_source_anchors


def test_build_vote_ontology_backtest_matches_bill_semantics_within_jurisdiction() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(yea_count=5, nay_count=5),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
            }
        ],
        label_rows=[
            {
                **_label_row(question="On Passage of H.R. 1, Energy Bill"),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": "https://leginfo.legislature.ca.gov/faces/billVotesClient.xhtml?bill_id=20250AB1",
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
                        rationale="Congress-only semantic should not cross jurisdictions.",
                    )
                ],
                industries=["utilities"],
                affected_entities=["utilities"],
                coalition_summary="Energy infrastructure coalition.",
                ideological_valence="mixed",
                source_anchors=[_anchor("committee_membership", "cm-semantic")],
            )
        ],
    )

    prediction = result.predictions[0]
    assert prediction.feature_signals["bill_issue_sector_overlap"] == 0.0
    assert prediction.feature_signals["bill_coalition_signal"] == 0.0
    assert "bill_coalition_signal" not in prediction.feature_source_anchors


def test_build_vote_ontology_backtest_uses_llm_bill_semantic_sectors() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(vote_event_id=102, question="On Passage of H.R. 1")],
        ontology_edges=_ontology_edges(),
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
                        rationale="LLM identified energy infrastructure semantics.",
                    )
                ],
                industries=["utilities"],
                affected_entities=["utilities"],
                coalition_summary="Energy infrastructure coalition.",
                ideological_valence="mixed",
                source_anchors=[_anchor("committee_membership", "cm-semantic")],
            )
        ],
    )

    assert result.predictions[0].feature_signals["bill_issue_sector_overlap"] == 1.0
    assert result.predictions[0].feature_signals["bill_coalition_signal"] == 1.0
    assert result.predictions[0].feature_signals["committee_jurisdiction_overlap"] == 1.0
    assert result.predictions[0].feature_source_anchors["bill_coalition_signal"][0].source_id == (
        "cm-semantic"
    )


def test_build_vote_ontology_backtest_ignores_future_available_bill_semantics() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(vote_event_id=102, question="On Passage of H.R. 1")],
        ontology_edges=_ontology_edges(),
        bill_signal_rows=[],
        bill_semantics=[
            BillSemanticPayload(
                bill_key="119-hr-1",
                available_at=dt.date(2025, 1, 2),
                sectors=[
                    BillSectorSemanticPayload(
                        sector_id="energy",
                        label="Energy",
                        confidence=0.95,
                        rationale="Future semantic extraction should not leak.",
                    )
                ],
                source_anchors=[_anchor("committee_membership", "cm-semantic")],
            )
        ],
    )

    assert result.predictions[0].feature_signals["bill_issue_sector_overlap"] == 0.0
    assert result.predictions[0].feature_signals["committee_jurisdiction_overlap"] == 0.0


def test_build_vote_ontology_backtest_filters_future_dated_ontology_edges() -> None:
    future_holding = _ontology_edge(
        "member_sector_holding_exposure",
        _member_node(),
        _sector_node(),
        "financial_disclosure",
        attributes={
            "filing_date": "2025-01-15",
            "value_min": 15001,
            "value_max": 50000,
        },
    )
    future_transaction = _ontology_edge(
        "member_sector_transaction_exposure",
        _member_node(),
        _sector_node(),
        "financial_disclosure",
        attributes={
            "transaction_type": "purchase",
            "transaction_date": "2025-01-15",
            "amount_min": 15001,
            "amount_max": 50000,
        },
    )

    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1, Energy Bill")],
        ontology_edges=[future_holding, future_transaction],
        bill_signal_rows=[],
    )

    signals = result.predictions[0].feature_signals
    assert signals["financial_sector_overlap"] == 0.0
    assert signals["holding_recency"] == 0.0
    assert signals["transaction_recency"] == 0.0
    assert signals["source_anchor_strength"] == 0.0


def test_build_vote_ontology_backtest_filters_unknown_availability_ontology_edges() -> None:
    unknown_holding = _ontology_edge(
        "member_sector_holding_exposure",
        _member_node(),
        _sector_node(),
        "financial_disclosure",
        attributes={},
    )
    unknown_transaction = _ontology_edge(
        "member_sector_transaction_exposure",
        _member_node(),
        _sector_node(),
        "financial_disclosure",
        attributes={},
    )

    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1")],
        ontology_edges=[unknown_holding, unknown_transaction],
        bill_signal_rows=_bill_signal_rows(),
    )

    signals = result.predictions[0].feature_signals
    assert signals["financial_sector_overlap"] == 0.0
    assert signals["holding_recency"] == 0.0
    assert signals["transaction_recency"] == 0.0
    assert signals["source_anchor_strength"] == 0.0


def test_build_vote_ontology_backtest_uses_statement_date_for_ontology_edge_availability() -> None:
    statement_anchor = SourceAnchor(
        source_type="public_statement",
        source_id="stmt-1",
        url="https://a.house.gov/news/energy",
        label="Energy statement",
    )
    statement_edge = OntologyEdgePayload(
        edge_id="edge-statement",
        edge_type="member_sector_public_statement_alignment",
        subject=_member_node(),
        object=_sector_node(),
        source_anchors=[statement_anchor],
        attributes={"statement_date": "2024-11-06", "alignment_score": 0.7},
    )

    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1, Energy Bill")],
        ontology_edges=[statement_edge],
        bill_signal_rows=_bill_signal_rows(),
    )

    prediction = result.predictions[0]
    assert prediction.feature_signals["source_anchor_strength"] == 0.5
    assert prediction.feature_source_anchors["source_anchor_strength"] == [statement_anchor]


def test_build_vote_ontology_backtest_filters_future_dated_sponsor_rows() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1")],
        ontology_edges=[],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Permitting Reform Act",
                "short_title": "Energy Permitting Reform",
                "sponsor_bioguide_id": "A000001",
                "sponsor_party": "D",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": dt.date(2025, 1, 3),
            }
        ],
    )

    assert result.predictions[0].feature_signals["sponsor_cosponsor_alignment"] == 0.5


def test_build_vote_ontology_backtest_filters_undated_sponsor_rows() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1")],
        ontology_edges=[],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Permitting Reform Act",
                "short_title": "Energy Permitting Reform",
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

    prediction = result.predictions[0]
    assert prediction.feature_signals["sponsor_cosponsor_alignment"] == 0.5
    assert not any(
        ":sponsor:" in anchor.source_id
        for anchor in prediction.feature_source_anchors["sponsor_cosponsor_alignment"]
    )


def test_build_vote_ontology_backtest_uses_primary_sponsor_introduced_date_when_sponsor_date_missing() -> (
    None
):
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1")],
        ontology_edges=[],
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
                "sponsor_bioguide_id": "A000001",
                "sponsor_party": "D",
                "sponsor_role": "primary",
                "is_primary": True,
                "sponsor_date": None,
                "sponsor_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
            }
        ],
    )

    prediction = result.predictions[0]
    assert prediction.feature_signals["sponsor_cosponsor_alignment"] == 1.0
    assert any(
        anchor.source_id == "119-hr-1:sponsor:A000001:primary"
        for anchor in prediction.feature_source_anchors["sponsor_cosponsor_alignment"]
    )


def test_build_vote_ontology_backtest_filters_primary_sponsor_when_introduced_date_after_cutoff() -> (
    None
):
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1")],
        ontology_edges=[],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Permitting Reform Act",
                "short_title": "Energy Permitting Reform",
                "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
                "introduced_date": dt.date(2025, 1, 3),
                "latest_action_date": dt.date(2025, 1, 3),
                "sponsor_bioguide_id": "A000001",
                "sponsor_party": "D",
                "sponsor_role": "primary",
                "is_primary": True,
                "sponsor_date": None,
                "sponsor_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
            }
        ],
    )

    prediction = result.predictions[0]
    assert prediction.feature_signals["sponsor_cosponsor_alignment"] == 0.5
    assert "sponsor_cosponsor_alignment" not in prediction.feature_source_anchors


def test_build_vote_ontology_backtest_filters_future_bill_metadata_from_sector_matching() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1")],
        ontology_edges=_ontology_edges(),
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Permitting Reform Act",
                "short_title": "Energy Permitting Reform",
                "introduced_date": dt.date(2025, 1, 3),
                "latest_action_date": dt.date(2025, 1, 3),
                "sponsor_bioguide_id": "A000001",
                "sponsor_party": "D",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": dt.date(2025, 1, 3),
            }
        ],
    )

    assert result.predictions[0].feature_signals["bill_issue_sector_overlap"] == 0.0
    assert result.predictions[0].feature_signals["committee_jurisdiction_overlap"] == 0.0


def test_build_vote_ontology_backtest_does_not_match_us_congress_across_chambers() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(vote_count=10, yea_count=10, nay_count=0),
                "chamber": "house",
            }
        ],
        label_rows=[{**_label_row(), "chamber": "senate"}],
        ontology_edges=[],
        bill_signal_rows=[],
    )

    prediction = result.predictions[0]
    assert result.metrics.evaluated_count == 0
    assert result.metrics.skipped_count == 1
    assert prediction.feature_vote_count == 0
    assert prediction.feature_signals == {}
    assert prediction.predicted_probability_yea is None
    assert prediction.skipped_reason == "missing_pre_cutoff_vote_history"


def test_build_vote_ontology_backtest_filters_future_bill_latest_action_metadata() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1")],
        ontology_edges=_ontology_edges(),
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "Energy Permitting Reform Act",
                "short_title": "Energy Permitting Reform",
                "introduced_date": dt.date(2024, 12, 1),
                "latest_action_date": dt.date(2025, 1, 3),
                "sponsor_bioguide_id": "A000001",
                "sponsor_party": "D",
                "sponsor_role": "cosponsor",
                "is_primary": False,
                "sponsor_date": dt.date(2024, 12, 1),
            }
        ],
    )

    assert result.predictions[0].feature_signals["bill_issue_sector_overlap"] == 1.0
    assert result.predictions[0].feature_signals["committee_jurisdiction_overlap"] == 1.0
    assert result.predictions[0].feature_signals["sponsor_cosponsor_alignment"] == 1.0


def test_build_vote_ontology_backtest_uses_cutoff_member_party_for_party_signals() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            _feature_row(vote_count=10, yea_count=3),
            {**_feature_row("B000002", vote_count=10, yea_count=9), "party": "R"},
        ],
        label_rows=[
            {
                **_label_row(question="On Passage of H.R. 1"),
                "party": "R",
            }
        ],
        ontology_edges=[],
        bill_signal_rows=[
            {
                "congress": 119,
                "bill_type": "hr",
                "bill_number": 1,
                "title": "General Government Act",
                "short_title": "General Government",
                "bill_source_url": "https://api.congress.gov/v3/bill/119/hr/1?format=json",
                "introduced_date": dt.date(2024, 12, 1),
                "latest_action_date": dt.date(2024, 12, 1),
                "sponsor_bioguide_id": "C000003",
                "sponsor_party": "D",
                "sponsor_role": "primary",
                "is_primary": True,
                "sponsor_date": dt.date(2024, 12, 15),
            }
        ],
    )

    signals = result.predictions[0].feature_signals
    assert signals["party_chamber_baseline"] == 0.3
    assert signals["sponsor_cosponsor_alignment"] == pytest.approx(2 / 3)


def test_build_vote_ontology_backtest_does_not_infer_sectors_from_future_vote_label_text() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1, Energy Permitting Reform")],
        ontology_edges=_ontology_edges(),
        bill_signal_rows=[],
    )

    signals = result.predictions[0].feature_signals
    assert result.predictions[0].bill_key == "119-hr-1"
    assert signals["bill_issue_sector_overlap"] == 0.0
    assert signals["committee_jurisdiction_overlap"] == 0.0
    assert signals["financial_sector_overlap"] == 0.0
    assert signals["contribution_sector_overlap"] == 0.0
    assert signals["holding_recency"] == 0.0
    assert signals["transaction_recency"] == 0.0
    assert signals["source_anchor_strength"] == 0.0


def test_build_vote_ontology_backtest_filters_future_dated_contribution_and_statement_rows() -> (
    None
):
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1, Energy Bill")],
        ontology_edges=[],
        bill_signal_rows=_bill_signal_rows(),
        contribution_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 1.0,
                "contribution_date": dt.date(2025, 1, 5),
            }
        ],
        statement_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 1.0,
                "statement_date": dt.date(2025, 1, 5),
            }
        ],
    )

    prediction = result.predictions[0]
    assert "donation_industry_alignment" not in prediction.feature_signals
    assert "public_statement_alignment" not in prediction.feature_signals
    assert "donation_industry_alignment" in prediction.unavailable_signals
    assert "public_statement_alignment" in prediction.unavailable_signals


def test_build_vote_ontology_backtest_filters_undated_contribution_and_statement_rows() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1, Energy Bill")],
        ontology_edges=[],
        bill_signal_rows=_bill_signal_rows(),
        contribution_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 1.0,
            }
        ],
        statement_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 1.0,
            }
        ],
    )

    prediction = result.predictions[0]
    assert "donation_industry_alignment" not in prediction.feature_signals
    assert "public_statement_alignment" not in prediction.feature_signals
    assert "donation_industry_alignment" in prediction.unavailable_signals
    assert "public_statement_alignment" in prediction.unavailable_signals


def test_build_vote_ontology_backtest_uses_signal_row_source_anchors() -> None:
    donation_anchor = _anchor("fec_contribution", "fec-1")
    statement_anchor = _anchor("public_statement", "stmt-1")

    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1, Energy Bill")],
        ontology_edges=[],
        bill_signal_rows=_bill_signal_rows(),
        contribution_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.8,
                "contribution_date": dt.date(2024, 11, 5),
                "source_anchors": [donation_anchor],
            }
        ],
        statement_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.7,
                "statement_date": dt.date(2024, 11, 6),
                "source_anchors": [statement_anchor],
            }
        ],
    )

    prediction = result.predictions[0]
    assert prediction.feature_signals["donation_industry_alignment"] == 0.8
    assert prediction.feature_signals["public_statement_alignment"] == 0.7
    assert prediction.feature_source_anchors["donation_industry_alignment"] == [donation_anchor]
    assert prediction.feature_source_anchors["public_statement_alignment"] == [statement_anchor]


def test_build_vote_ontology_backtest_preserves_source_anchor_legislative_context() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1, Energy Bill")],
        ontology_edges=[],
        bill_signal_rows=_bill_signal_rows(),
        contribution_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.8,
                "contribution_date": dt.date(2024, 11, 5),
                "source_anchors": [
                    {
                        "source_type": "legislative_vote",
                        "source_id": "tx-house-89r-hb1-vote",
                        "jurisdiction_id": "state_tx",
                        "legislative_body_id": "tx_house",
                        "legislative_session_id": "89r",
                        "url": (
                            "https://capitol.texas.gov/BillLookup/History.aspx?LegSess=89R&Bill=HB1"
                        ),
                        "label": "Texas House HB 1 vote",
                    }
                ],
            }
        ],
    )

    anchor = result.predictions[0].feature_source_anchors["donation_industry_alignment"][0]
    assert anchor.jurisdiction_id == "state_tx"
    assert anchor.legislative_body_id == "tx_house"
    assert anchor.legislative_session_id == "89r"


def test_build_vote_ontology_backtest_fills_legislative_anchor_context_from_signal_row() -> None:
    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[
            {
                **_feature_row(),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
            }
        ],
        label_rows=[
            {
                **_label_row(question="On Passage of H.R. 1, Energy Bill"),
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "source_url": (
                    "https://leginfo.legislature.ca.gov/faces/"
                    "billVotesClient.xhtml?bill_id=20250AB1"
                ),
            }
        ],
        ontology_edges=[],
        bill_signal_rows=[
            {
                **_bill_signal_rows()[0],
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "bill_source_url": (
                    "https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=20250AB1"
                ),
            }
        ],
        contribution_signal_rows=[
            {
                "jurisdiction_id": "state_ca",
                "legislative_body_id": "ca_assembly",
                "legislative_session_id": "2025_regular",
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.8,
                "contribution_date": dt.date(2024, 11, 5),
                "source_anchors": [
                    {
                        "source_type": "legislative_vote",
                        "source_id": "ca-assembly-2025-ab1-vote",
                        "url": (
                            "https://leginfo.legislature.ca.gov/faces/"
                            "billVotesClient.xhtml?bill_id=20250AB1"
                        ),
                        "label": "California Assembly AB 1 vote",
                    }
                ],
            }
        ],
    )

    anchor = result.predictions[0].feature_source_anchors["donation_industry_alignment"][0]
    assert anchor.jurisdiction_id == "state_ca"
    assert anchor.legislative_body_id == "ca_assembly"
    assert anchor.legislative_session_id == "2025_regular"


def test_build_vote_ontology_backtest_scores_only_source_backed_signal_rows() -> None:
    donation_anchor = _anchor("fec_contribution", "fec-1")
    statement_anchor = _anchor("public_statement", "stmt-1")

    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1, Energy Bill")],
        ontology_edges=[],
        bill_signal_rows=_bill_signal_rows(),
        contribution_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.9,
                "contribution_date": dt.date(2024, 11, 1),
                "source_anchors": [],
            },
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.2,
                "contribution_date": dt.date(2024, 11, 5),
                "source_anchors": [donation_anchor],
            },
        ],
        statement_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.8,
                "statement_date": dt.date(2024, 11, 1),
                "source_anchors": [],
            },
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.3,
                "statement_date": dt.date(2024, 11, 6),
                "source_anchors": [statement_anchor],
            },
        ],
    )

    prediction = result.predictions[0]
    assert prediction.feature_signals["donation_industry_alignment"] == 0.2
    assert prediction.feature_signals["public_statement_alignment"] == 0.3
    assert prediction.feature_source_anchors["donation_industry_alignment"] == [donation_anchor]
    assert prediction.feature_source_anchors["public_statement_alignment"] == [statement_anchor]


def test_build_vote_ontology_backtest_uses_loaded_contribution_sector_edges() -> None:
    contribution_edge = _ontology_edge(
        "member_sector_contribution_exposure",
        _member_node(),
        _sector_node(),
        "fec_contribution",
        attributes={
            "contribution_id": 901,
            "donor_name": "SOLAR BUILDERS PAC",
            "donor_type": "PAC",
            "contribution_date": "2024-10-15",
            "amount": 2500,
            "alignment_score": 0.75,
        },
    )

    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1, Energy Bill")],
        ontology_edges=[contribution_edge],
        bill_signal_rows=_bill_signal_rows(),
    )

    prediction = result.predictions[0]
    assert prediction.feature_signals["contribution_sector_overlap"] == 1.0
    assert prediction.feature_source_anchors["contribution_sector_overlap"][0].source_type == (
        "fec_contribution"
    )
    assert "donation_industry_alignment" in prediction.unavailable_signals


def test_build_vote_ontology_backtest_dedupes_signal_source_identities() -> None:
    donation_anchor = _anchor("fec_contribution", "fec-1")
    duplicate_identity = SourceAnchor(
        source_type=donation_anchor.source_type,
        source_id=donation_anchor.source_id,
        url=donation_anchor.url,
        label="Duplicate display label",
    )

    result = build_vote_ontology_backtest(
        feature_cutoff=dt.date(2024, 12, 31),
        label_start=dt.date(2025, 1, 1),
        label_end=dt.date(2025, 12, 31),
        feature_rows=[_feature_row()],
        label_rows=[_label_row(question="On Passage of H.R. 1, Energy Bill")],
        ontology_edges=[],
        bill_signal_rows=_bill_signal_rows(),
        contribution_signal_rows=[
            {
                "bioguide_id": "A000001",
                "sector": "energy",
                "alignment_score": 0.8,
                "contribution_date": dt.date(2024, 11, 5),
                "source_anchors": [duplicate_identity, donation_anchor],
            }
        ],
    )

    prediction = result.predictions[0]
    assert prediction.feature_source_anchors["donation_industry_alignment"] == [donation_anchor]
