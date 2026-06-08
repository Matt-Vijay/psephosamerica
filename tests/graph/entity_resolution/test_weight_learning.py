from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.entity_resolution.records import SourceRecord
from src.graph.entity_resolution.scoring import (
    DEFAULT_WEIGHTS,
    MatchWeights,
    person_comparison_features,
    score_pair,
)
from src.graph.entity_resolution.weight_learning import learn_match_weights
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://example.gov/x",
    content_sha256="a" * 64,
    first_observed_at=datetime(2024, 1, 10, tzinfo=UTC),
    valid_from=date(2024, 1, 1),
    known_at=datetime(2024, 1, 5, tzinfo=UTC),
)


def _rec(name: str, rid: str, *, jurisdiction: str | None = None) -> SourceRecord:
    return SourceRecord(
        source_system="s",
        source_record_id=rid,
        entity_type="person",
        display_name=name,
        jurisdiction=jurisdiction,
        provenance=_PROV,
    )


# ── feature extractor mirrors the scorer ───────────────────────────


def test_features_present_only_when_applicable() -> None:
    feats = person_comparison_features(_rec("Jane Doe", "1"), _rec("Jane Smith", "2"))
    assert feats["family"] is False
    assert feats["given"] is True
    assert "middle" not in feats  # neither has a middle name
    assert "jurisdiction" not in feats  # neither set


def test_features_cover_all_dimensions() -> None:
    a = _rec("Jane Marie Doe", "1", jurisdiction="us")
    b = _rec("Jane Anne Doe", "2", jurisdiction="tx")
    b = SourceRecord(
        source_system="s",
        source_record_id="2",
        entity_type="person",
        display_name="Jane Anne Doe",
        jurisdiction="tx",
        region="TX",
        provenance=_PROV,
    )
    a = SourceRecord(
        source_system="s",
        source_record_id="1",
        entity_type="person",
        display_name="Jane Marie Doe",
        jurisdiction="us",
        region="CA",
        provenance=_PROV,
    )
    feats = person_comparison_features(a, b)
    assert feats["family"] is True
    assert feats["given"] is True
    assert feats["middle"] is False  # Marie vs Anne
    assert feats["jurisdiction"] is False
    assert feats["region"] is False


def test_features_skip_dimensions_with_missing_name_parts() -> None:
    # A degenerate name parses to empty family/given -> those features are absent.
    feats = person_comparison_features(_rec(".", "1"), _rec("Jane Doe", "2"))
    assert "family" not in feats
    assert "given" not in feats


def test_features_match_score_pair_reasons() -> None:
    a = _rec("Jane M. Doe", "1", jurisdiction="us")
    b = _rec("Doe, Jane M.", "2", jurisdiction="us")
    feats = person_comparison_features(a, b)
    reasons = score_pair(a, b).reasons
    assert feats["family"] is True and "family_exact" in reasons
    assert feats["jurisdiction"] is True and "jurisdiction_match" in reasons


# ── learning ───────────────────────────────────────────────────────


def _labeled_set() -> list[tuple[SourceRecord, SourceRecord, bool]]:
    pairs: list[tuple[SourceRecord, SourceRecord, bool]] = []
    # Matches: same family, compatible given.
    for i in range(20):
        pairs.append((_rec("Jane Doe", f"m{i}a"), _rec("Jane Doe", f"m{i}b"), True))
    # Non-matches: different family, different given.
    for i in range(20):
        pairs.append((_rec("Jane Doe", f"n{i}a"), _rec("John Smith", f"n{i}b"), False))
    return pairs


def test_learned_agree_weights_are_positive_disagree_negative() -> None:
    weights = learn_match_weights(_labeled_set())
    assert weights.family_agree > 0
    assert weights.family_disagree < 0
    assert weights.given_agree > 0


def test_learned_weights_are_a_MatchWeights() -> None:
    weights = learn_match_weights(_labeled_set())
    assert isinstance(weights, MatchWeights)
    # Suffix weight isn't learned; it keeps the default.
    assert weights.suffix_conflict == DEFAULT_WEIGHTS.suffix_conflict


def test_learned_weights_score_a_clear_match_as_match() -> None:
    weights = learn_match_weights(_labeled_set())
    score = score_pair(_rec("Jane Doe", "x"), _rec("Jane Doe", "y"), weights=weights)
    assert score.decision == "match"


def test_dimension_with_no_data_falls_back_to_default() -> None:
    # No pair in the set sets a region, so region weights stay at the default.
    weights = learn_match_weights(_labeled_set())
    assert weights.region_agree == DEFAULT_WEIGHTS.region_agree
    assert weights.region_disagree == DEFAULT_WEIGHTS.region_disagree


def test_empty_labeled_set_returns_default() -> None:
    assert learn_match_weights([]) == DEFAULT_WEIGHTS


def test_learning_is_deterministic() -> None:
    pairs = _labeled_set()
    assert learn_match_weights(pairs) == learn_match_weights(pairs)


def test_custom_default_weights_used_for_unlearned_dims() -> None:
    custom = MatchWeights(region_agree=9.0, suffix_conflict=-7.0)
    weights = learn_match_weights(_labeled_set(), default=custom)
    assert weights.region_agree == 9.0  # no region data -> custom default kept
    assert weights.suffix_conflict == -7.0


def test_prior_reflects_match_rate() -> None:
    # A set that is mostly matches should learn a higher (less negative) prior
    # than one that is mostly non-matches.
    mostly_match = [(_rec("Jane Doe", f"a{i}"), _rec("Jane Doe", f"b{i}"), True) for i in range(18)]
    mostly_match += [(_rec("Jane Doe", "na"), _rec("John Smith", "nb"), False)]
    mostly_non = [
        (_rec("Jane Doe", f"a{i}"), _rec("John Smith", f"b{i}"), False) for i in range(18)
    ]
    mostly_non += [(_rec("Jane Doe", "ma"), _rec("Jane Doe", "mb"), True)]
    assert learn_match_weights(mostly_match).prior > learn_match_weights(mostly_non).prior
