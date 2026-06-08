from __future__ import annotations

from src.graph.entity_resolution.assignment import CanonicalAssignment, assign_canonical_ids
from src.graph.entity_resolution.linker import ResolvedCluster


def _cluster(*record_ids: str) -> ResolvedCluster:
    ids = tuple(sorted(record_ids))
    # canonical_id mirrors the linker: content-addressed over membership.
    from src.graph.entity_resolution.ids import stable_id

    return ResolvedCluster(canonical_id=stable_id(list(ids), "ce"), record_ids=ids, match_edges=())


# ── first assignment ───────────────────────────────────────────────


def test_new_clusters_get_their_content_addressed_id() -> None:
    c1, c2 = _cluster("a"), _cluster("b")
    assignment = assign_canonical_ids([c1, c2])
    assert assignment.by_record["a"] == c1.canonical_id
    assert assignment.by_record["b"] == c2.canonical_id
    assert assignment.canonical_ids == frozenset({c1.canonical_id, c2.canonical_id})


def test_assignment_is_deterministic_regardless_of_order() -> None:
    c1, c2 = _cluster("a"), _cluster("b")
    assert assign_canonical_ids([c1, c2]).by_record == assign_canonical_ids([c2, c1]).by_record


# ── growth preserves the id ────────────────────────────────────────


def test_growth_carries_the_prior_id_forward() -> None:
    prior = assign_canonical_ids([_cluster("a")])
    original_id = prior.by_record["a"]
    # Next run: b joined a's entity.
    updated = assign_canonical_ids([_cluster("a", "b")], prior)
    assert updated.by_record["a"] == original_id
    assert updated.by_record["b"] == original_id  # stable id, despite new content hash
    assert updated.superseded_ids == frozenset()


# ── merge: two prior entities become one ───────────────────────────


def test_merge_keeps_smaller_prior_id_and_supersedes_other() -> None:
    prior = assign_canonical_ids([_cluster("a"), _cluster("b")])
    id_a, id_b = prior.by_record["a"], prior.by_record["b"]
    kept, gone = sorted((id_a, id_b))
    merged = assign_canonical_ids([_cluster("a", "b")], prior)
    assert merged.by_record["a"] == kept
    assert merged.by_record["b"] == kept
    assert gone in merged.superseded_ids


# ── split: one entity becomes two ──────────────────────────────────


def test_split_one_keeps_id_other_gets_new() -> None:
    prior = assign_canonical_ids([_cluster("a", "b")])
    original_id = prior.by_record["a"]
    split = assign_canonical_ids([_cluster("a"), _cluster("b")], prior)
    kept = [r for r in ("a", "b") if split.by_record[r] == original_id]
    fresh = [r for r in ("a", "b") if split.by_record[r] != original_id]
    assert len(kept) == 1  # exactly one side keeps the persistent id
    assert len(fresh) == 1
    assert split.by_record[fresh[0]] != original_id
    # No id is shared across the two distinct entities.
    assert split.by_record["a"] != split.by_record["b"]


def test_split_is_deterministic() -> None:
    prior = assign_canonical_ids([_cluster("a", "b")])
    one = assign_canonical_ids([_cluster("a"), _cluster("b")], prior)
    two = assign_canonical_ids([_cluster("b"), _cluster("a")], prior)
    assert one.by_record == two.by_record


# ── helpers / edge cases ───────────────────────────────────────────


def test_empty_assignment() -> None:
    assignment = assign_canonical_ids([])
    assert assignment.by_record == {}
    assert assignment.canonical_ids == frozenset()
    assert assignment.superseded_ids == frozenset()


def test_prior_records_not_in_new_run_are_ignored() -> None:
    prior = assign_canonical_ids([_cluster("a"), _cluster("gone")])
    updated = assign_canonical_ids([_cluster("a", "b")], prior)
    assert "gone" not in updated.by_record
    assert updated.by_record["a"] == prior.by_record["a"]


def test_assignment_is_frozen() -> None:
    import pytest
    from pydantic import ValidationError

    assignment = CanonicalAssignment(by_record={"a": "ce-x"})
    with pytest.raises(ValidationError):
        assignment.by_record = {}  # type: ignore[misc]
