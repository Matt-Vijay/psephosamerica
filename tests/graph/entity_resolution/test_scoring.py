from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.graph.entity_resolution.records import SourceRecord
from src.graph.entity_resolution.scoring import (
    MatchThresholds,
    MatchWeights,
    logistic,
    score_pair,
)
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://example.gov/x",
    content_sha256="c" * 64,
    first_observed_at=datetime(2024, 1, 10, 12, 0, tzinfo=UTC),
    valid_from=date(2024, 1, 1),
    known_at=datetime(2024, 1, 5, tzinfo=UTC),
)


def _rec(
    name: str,
    *,
    rid: str = "r",
    entity_type: str = "person",
    ext: list[dict[str, str]] | None = None,
    jurisdiction: str | None = None,
    region: str | None = None,
) -> SourceRecord:
    return SourceRecord(
        source_system="s",
        source_record_id=rid,
        entity_type=entity_type,  # type: ignore[arg-type]
        display_name=name,
        external_ids=ext or [],
        jurisdiction=jurisdiction,
        region=region,
        provenance=_PROV,
    )


# ── logistic helper ────────────────────────────────────────────────


def test_logistic_midpoint_and_monotonic() -> None:
    assert logistic(0.0) == pytest.approx(0.5)
    # Clamped at +/-16, so extremes saturate near (but not exactly) 1 and 0.
    assert logistic(100.0) == pytest.approx(1.0, abs=1e-6)
    assert logistic(-100.0) == pytest.approx(0.0, abs=1e-6)
    assert logistic(100.0) == logistic(16.0)  # clamp is symmetric and stable
    assert logistic(2.0) > logistic(1.0)


# ── Hard rules ─────────────────────────────────────────────────────


def test_entity_type_mismatch_is_no_match() -> None:
    person = _rec("Jane Doe", entity_type="person")
    org = _rec("Jane Doe", rid="o", entity_type="org")
    score = score_pair(person, org)
    assert score.decision == "no_match"
    assert score.probability == pytest.approx(0.0, abs=1e-3)
    assert score.reasons == ("entity_type_mismatch",)


def test_shared_external_id_forces_match_despite_name_mismatch() -> None:
    a = _rec("Jane Doe", rid="a", ext=[{"system": "fec_candidate", "value": "H0CA1"}])
    b = _rec("Robert Smith", rid="b", ext=[{"system": "FEC_Candidate", "value": "h0ca1"}])
    score = score_pair(a, b)
    assert score.decision == "match"
    assert score.probability > 0.999
    assert "shared_external_id" in score.reasons


def test_conflicting_same_namespace_id_forces_no_match() -> None:
    a = _rec("Jane Doe", rid="a", ext=[{"system": "bioguide", "value": "D000001"}])
    b = _rec("Jane Doe", rid="b", ext=[{"system": "bioguide", "value": "D000002"}])
    score = score_pair(a, b)
    assert score.decision == "no_match"
    assert "external_id_conflict" in score.reasons


def test_conflict_takes_priority_over_partial_shared_id() -> None:
    # Agree on FEC, conflict on bioguide -> different people win.
    a = _rec(
        "Jane Doe",
        rid="a",
        ext=[
            {"system": "fec_candidate", "value": "H0CA1"},
            {"system": "bioguide", "value": "D000001"},
        ],
    )
    b = _rec(
        "Jane Doe",
        rid="b",
        ext=[
            {"system": "fec_candidate", "value": "H0CA1"},
            {"system": "bioguide", "value": "D000002"},
        ],
    )
    assert score_pair(a, b).decision == "no_match"


# ── Additive scoring on names + context ────────────────────────────


def test_strong_name_and_jurisdiction_match() -> None:
    a = _rec("Jane M. Doe", rid="a", jurisdiction="us")
    b = _rec("Doe, Jane M.", rid="b", jurisdiction="us")
    score = score_pair(a, b)
    # family +2.5, given +1.5, middle +0.5, jurisdiction +0.5, prior -1.5 = 3.5
    assert score.log_odds == pytest.approx(3.5)
    assert score.decision == "match"
    assert "family_exact" in score.reasons
    assert "jurisdiction_match" in score.reasons


def test_family_match_alone_is_possible() -> None:
    a = _rec("Doe", rid="a")  # single token -> empty given
    b = _rec("Jane Doe", rid="b")
    score = score_pair(a, b)
    # family +2.5, prior -1.5 = 1.0; given is neutral (one side missing)
    assert score.log_odds == pytest.approx(1.0)
    assert score.decision == "possible"


def test_given_name_mismatch_is_no_match() -> None:
    score = score_pair(_rec("Jane Doe", rid="a"), _rec("John Doe", rid="b"))
    # family +2.5, given -2.5, prior -1.5 = -1.5
    assert score.log_odds == pytest.approx(-1.5)
    assert score.decision == "no_match"
    assert "given_mismatch" in score.reasons


def test_suffix_conflict_sinks_an_otherwise_strong_match() -> None:
    score = score_pair(_rec("John Smith Jr.", rid="a"), _rec("John Smith Sr.", rid="b"))
    # family +2.5, given +1.5, suffix -3.0, prior -1.5 = -0.5
    assert score.log_odds == pytest.approx(-0.5)
    assert score.decision == "no_match"
    assert "suffix_conflict" in score.reasons


def test_middle_name_conflict_penalizes() -> None:
    score = score_pair(_rec("Jane Marie Doe", rid="a"), _rec("Jane Anne Doe", rid="b"))
    assert "middle_conflict" in score.reasons
    # family +2.5, given +1.5, middle -2.0, prior -1.5 = 0.5
    assert score.log_odds == pytest.approx(0.5)


def test_empty_family_names_contribute_no_name_evidence() -> None:
    # Degenerate names parse to empty parts -> only the prior remains.
    score = score_pair(_rec(".", rid="a"), _rec(".", rid="b"))
    assert score.reasons == ()
    assert score.log_odds == pytest.approx(-1.5)


def test_org_with_unnormalizable_name_contributes_nothing() -> None:
    score = score_pair(
        _rec("...", rid="a", entity_type="org"),
        _rec("Acme PAC", rid="b", entity_type="org"),
    )
    assert "org_name_match" not in score.reasons
    assert "org_name_mismatch" not in score.reasons
    assert score.log_odds == pytest.approx(-1.5)


def test_region_agreement_adds_evidence() -> None:
    with_region = score_pair(
        _rec("Doe", rid="a", region="CA"), _rec("Jane Doe", rid="b", region="CA")
    )
    without = score_pair(_rec("Doe", rid="a"), _rec("Jane Doe", rid="b"))
    assert with_region.log_odds > without.log_odds
    assert "region_match" in with_region.reasons


def test_region_and_jurisdiction_mismatch_penalize() -> None:
    score = score_pair(
        _rec("Jane Doe", rid="a", jurisdiction="us", region="CA"),
        _rec("Jane Doe", rid="b", jurisdiction="tx-state", region="TX"),
    )
    assert "jurisdiction_mismatch" in score.reasons
    assert "region_mismatch" in score.reasons


# ── Org records compare on whole normalized name ───────────────────


def test_org_name_match_and_mismatch() -> None:
    same = score_pair(
        _rec("Acme PAC", rid="a", entity_type="org"),
        _rec("acme  pac", rid="b", entity_type="org"),
    )
    diff = score_pair(
        _rec("Acme PAC", rid="a", entity_type="org"),
        _rec("Globex Fund", rid="b", entity_type="org"),
    )
    assert "org_name_match" in same.reasons
    assert "org_name_mismatch" in diff.reasons
    assert same.log_odds > diff.log_odds


# ── Injectable weights / thresholds ────────────────────────────────


def test_custom_thresholds_change_decision_band() -> None:
    a = _rec("Doe", rid="a")
    b = _rec("Jane Doe", rid="b")
    strict = score_pair(a, b, thresholds=MatchThresholds(match_prob=0.99, possible_prob=0.95))
    assert strict.decision == "no_match"
    lax = score_pair(a, b, thresholds=MatchThresholds(match_prob=0.6, possible_prob=0.3))
    assert lax.decision == "match"


def test_custom_weights_are_used() -> None:
    weights = MatchWeights(prior=0.0, family_agree=10.0)
    score = score_pair(_rec("Doe", rid="a"), _rec("Jane Doe", rid="b"), weights=weights)
    assert score.log_odds == pytest.approx(10.0)


def test_probability_matches_logistic_of_log_odds() -> None:
    score = score_pair(_rec("Jane M. Doe", rid="a"), _rec("Doe, Jane M.", rid="b"))
    assert score.probability == pytest.approx(logistic(score.log_odds))
