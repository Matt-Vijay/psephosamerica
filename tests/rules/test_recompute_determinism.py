"""Determinism property test for the conflict recompute (pillar 5).

The product guarantee is "same inputs, same outputs": rule fires and score
deltas must be reproducible. The only non-deterministic field is the wall-clock
``fired_at`` audit timestamp, which is excluded from the comparison.
"""

from __future__ import annotations

from src.demo.conflict_demo import run_conflict_demo


# fire_id (random UUID) and fired_at (wall clock) are non-deterministic
# provenance identifiers; everything else (rule_id, severity, sourced_facts,
# derived_values, parameters_used) is the deterministic scoring payload.
_NON_DETERMINISTIC = {"fired_at", "fire_id"}


def _fires_scoring_payload(fires: object) -> list[dict[str, object]]:
    return [fire.model_dump(mode="json", exclude=_NON_DETERMINISTIC) for fire in fires]  # type: ignore[attr-defined]


def test_conflict_recompute_is_deterministic_modulo_fired_at() -> None:
    first = run_conflict_demo()
    second = run_conflict_demo()

    # Rule fires are identical aside from non-deterministic provenance ids
    # (this covers rule_id, severity, sourced_facts, derived_values, parameters).
    assert _fires_scoring_payload(first.fires) == _fires_scoring_payload(second.fires)
    assert [f.rule_id for f in first.fires] == [f.rule_id for f in second.fires]
    assert [f.severity for f in first.fires] == [f.severity for f in second.fires]

    # Content-derived evidence-card identity and delta are stable across runs.
    assert first.evidence_card.evidence_card_id == second.evidence_card.evidence_card_id
    assert first.evidence_card.score_delta == second.evidence_card.score_delta


def test_conflict_recompute_fires_are_nonempty_with_net_penalty() -> None:
    result = run_conflict_demo()
    assert len(result.fires) >= 1
    # The demo's seeded conflicts produce a net penalty on the public card.
    assert result.evidence_card.score_delta < 0
