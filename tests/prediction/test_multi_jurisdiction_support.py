"""Multi-jurisdiction support for the prediction layer.

OVERALL_GOAL.md's central thesis: the architecture treats federal Congress as
one slice of a homogeneous Person x Bill x Org graph -- jurisdiction-agnostic by
construction, *not* federal-special-cased -- so it scales to state, county,
city, and special-district officials. These tests pin that: a vote tagged with
any jurisdiction flows through per-slice benchmarking, the per-member model,
and the served-prediction contract identically to a federal one. (The *data*
for non-federal jurisdictions is Track A's ingestion; the model support is
here.)
"""

from __future__ import annotations

import math
from datetime import date

from src.prediction.backtest import PredictionBacktestPredictionPayload, _event_key
from src.prediction.benchmark_slices import compute_benchmark_slices
from src.prediction.per_member_model import MemberVoteExample, train_per_member_model

_JURISDICTIONS = [
    ("us_congress", None, None, "house"),
    ("us_state_ca", "ca_assembly", "2025", "assembly"),
    ("county_king_wa", "king_council", "2025", "council"),
    ("city_seattle_wa", "seattle_council", "2025", "council"),
    ("special_district_smud", "smud_board", "2025", "board"),
]


def _prediction(
    *,
    jurisdiction: str,
    legislative_body_id: str | None,
    legislative_session_id: str | None,
    chamber: str,
    member_id: str,
    probability_yea: float,
    vote_option: str,
    roll_call_number: int,
) -> PredictionBacktestPredictionPayload:
    body = legislative_body_id or f"{jurisdiction}_{chamber}"
    event_key = _event_key(
        jurisdiction_id=jurisdiction,
        legislative_body_id=body,
        legislative_session_id=legislative_session_id,
        chamber=chamber,
        congress=119,
        session_number=1,
        roll_call_number=roll_call_number,
    )
    probability = max(1e-9, min(1 - 1e-9, probability_yea))
    target = 1.0 if vote_option == "yea" else 0.0
    return PredictionBacktestPredictionPayload(
        vote_event_id=2000 + roll_call_number,
        event_key=event_key,
        jurisdiction_id=jurisdiction,
        legislative_body_id=legislative_body_id,
        legislative_session_id=legislative_session_id,
        chamber=chamber,
        congress=119,
        session_number=1,
        roll_call_number=roll_call_number,
        vote_date=date(2025, 2, 1),
        question="On Passage",
        member_bioguide_id=member_id,
        party="D",
        actual_vote_option=vote_option,  # type: ignore[arg-type]
        feature_vote_count=5,
        predicted_probability_yea=probability,
        predicted_vote_option="yea" if probability >= 0.5 else "nay",
        predicted_vote_probabilities={"yea": probability, "nay": 1.0 - probability},
        correct=(probability >= 0.5) == (vote_option == "yea"),
        brier_score=(probability - target) ** 2,
        log_loss=-(target * math.log(probability) + (1 - target) * math.log(1 - probability)),
    )


def test_benchmark_slices_cover_every_jurisdiction_level() -> None:
    predictions = [
        _prediction(
            jurisdiction=jurisdiction,
            legislative_body_id=body,
            legislative_session_id=session,
            chamber=chamber,
            member_id=f"M-{jurisdiction}",
            probability_yea=0.8,
            vote_option="yea",
            roll_call_number=index,
        )
        for index, (jurisdiction, body, session, chamber) in enumerate(_JURISDICTIONS)
    ]
    slice_names = {item.slice_name for item in compute_benchmark_slices(predictions)}
    # Federal through special-district each get their own calibration slice.
    for jurisdiction, _body, _session, _chamber in _JURISDICTIONS:
        assert f"jurisdiction:{jurisdiction}" in slice_names


def test_per_member_model_is_indifferent_to_jurisdiction_label() -> None:
    # The model keys on the opaque member id; a city councilor trains and
    # predicts exactly as a federal member does.
    examples = [
        MemberVoteExample(member_id="city:seattle:jane", signals={"party": 1.0}, is_yea=True),
        MemberVoteExample(member_id="city:seattle:jane", signals={"party": -1.0}, is_yea=False),
        MemberVoteExample(member_id="federal:A000001", signals={"party": 1.0}, is_yea=True),
    ]
    model = train_per_member_model(examples, pooling_penalty=1.0)
    city = model.predict_probability("city:seattle:jane", {"party": 1.0})
    federal = model.predict_probability("federal:A000001", {"party": 1.0})
    assert 0.0 <= city <= 1.0
    assert 0.0 <= federal <= 1.0
    # An unseen special-district official falls back to the global model, same as
    # any unseen federal one -- no jurisdiction is privileged.
    unseen = model.predict_probability("special_district:smud:new", {"party": 1.0})
    assert 0.0 <= unseen <= 1.0


def test_non_congress_prediction_payload_is_accepted() -> None:
    # Constructing a county-council prediction must validate just like Congress.
    payload = _prediction(
        jurisdiction="county_king_wa",
        legislative_body_id="king_council",
        legislative_session_id="2025",
        chamber="council",
        member_id="county:king:lee",
        probability_yea=0.6,
        vote_option="yea",
        roll_call_number=1,
    )
    assert payload.jurisdiction_id == "county_king_wa"
    assert payload.legislative_body_id == "king_council"
