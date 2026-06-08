from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.entity_resolution.linker import (
    ResolutionResult,
    _UnionFind,
    blocking_keys_for,
    candidate_pairs,
    resolve,
)
from src.graph.entity_resolution.records import SourceRecord
from src.graph.provenance import ProvenanceEnvelope

_PROV = ProvenanceEnvelope(
    source_url="https://example.gov/x",
    content_sha256="d" * 64,
    first_observed_at=datetime(2024, 1, 10, 12, 0, tzinfo=UTC),
    valid_from=date(2024, 1, 1),
    known_at=datetime(2024, 1, 5, tzinfo=UTC),
)


def _rec(
    name: str,
    rid: str,
    *,
    system: str = "s",
    entity_type: str = "person",
    ext: list[dict[str, str]] | None = None,
    jurisdiction: str | None = None,
    region: str | None = None,
) -> SourceRecord:
    return SourceRecord(
        source_system=system,
        source_record_id=rid,
        entity_type=entity_type,  # type: ignore[arg-type]
        display_name=name,
        external_ids=ext or [],
        jurisdiction=jurisdiction,
        region=region,
        provenance=_PROV,
    )


def _cluster_of(result: ResolutionResult, record_id: str) -> tuple[str, ...]:
    for cluster in result.clusters:
        if record_id in cluster.record_ids:
            return cluster.record_ids
    raise AssertionError(f"{record_id} in no cluster")


# ── union-find internals ───────────────────────────────────────────


def test_union_find_resolves_and_compresses_deep_chains() -> None:
    # Build z -> y -> x by unioning the higher pair first, so find(z) must
    # traverse and compress a 2-level chain.
    uf = _UnionFind(["x", "y", "z"])
    uf.union("y", "z")  # parent[z] = y
    uf.union("x", "y")  # parent[y] = x  -> z -> y -> x
    assert uf.find("z") == "x"
    assert uf.find("y") == "x"
    # After compression, z points straight at the root.
    assert uf.find("z") == "x"


def test_union_find_chooses_smallest_representative() -> None:
    uf = _UnionFind(["a", "b", "c"])
    uf.union("c", "b")
    uf.union("b", "a")
    assert uf.find("c") == "a"


# ── blocking ───────────────────────────────────────────────────────


def test_blocking_keys_person_includes_name_block() -> None:
    keys = blocking_keys_for(_rec("Jane Doe", "1"))
    assert "person|doe|j" in keys


def test_blocking_keys_include_external_ids() -> None:
    keys = blocking_keys_for(_rec("Jane Doe", "1", ext=[{"system": "bioguide", "value": "D1"}]))
    assert "extid|bioguide:d1" in keys


def test_blocking_keys_org_uses_normalized_name() -> None:
    keys = blocking_keys_for(_rec("Acme PAC", "1", entity_type="org"))
    assert "org|acme pac" in keys


# ── candidate pair generation ──────────────────────────────────────


def test_candidate_pairs_only_within_blocks() -> None:
    a = _rec("Jane Doe", "1")
    b = _rec("Jane Doe", "2")  # same block as a
    c = _rec("Bob Zimmerman", "3")  # different block, no shared id
    pairs = candidate_pairs([a, b, c])
    flat = {frozenset(p) for p in pairs}
    assert frozenset({a.record_id, b.record_id}) in flat
    assert frozenset({a.record_id, c.record_id}) not in flat


def test_candidate_pairs_cross_block_on_shared_external_id() -> None:
    # Different name blocks, but the same FEC id pulls them together.
    a = _rec("Jane Doe", "1", ext=[{"system": "fec", "value": "X1"}])
    b = _rec("J. Q. Public", "2", ext=[{"system": "fec", "value": "X1"}])
    pairs = candidate_pairs([a, b])
    assert {frozenset(p) for p in pairs} == {frozenset({a.record_id, b.record_id})}


def test_candidate_pairs_are_unordered_and_deduped() -> None:
    a = _rec("Jane Doe", "1", ext=[{"system": "fec", "value": "X1"}])
    b = _rec("Jane Doe", "2", ext=[{"system": "fec", "value": "X1"}])
    # Shares both a name block and an external-id block -> still one pair.
    assert len(candidate_pairs([a, b])) == 1


# ── resolve: clustering ────────────────────────────────────────────


def test_resolve_merges_strong_match() -> None:
    a = _rec("Jane M. Doe", "1", jurisdiction="us")
    b = _rec("Doe, Jane M.", "2", jurisdiction="us")
    result = resolve([a, b])
    assert len(result.clusters) == 1
    assert result.clusters[0].record_ids == tuple(sorted((a.record_id, b.record_id)))
    assert result.review_queue == ()


def test_resolve_records_merge_edge_as_audit_trail() -> None:
    a = _rec("Jane M. Doe", "1", jurisdiction="us")
    b = _rec("Doe, Jane M.", "2", jurisdiction="us")
    cluster = resolve([a, b]).clusters[0]
    assert len(cluster.match_edges) == 1
    edge = cluster.match_edges[0]
    assert edge.score.decision == "match"
    assert "family_exact" in edge.score.reasons


def test_resolve_cross_block_merge_via_external_id() -> None:
    a = _rec("Jane Doe", "1", ext=[{"system": "fec", "value": "X1"}])
    b = _rec("J. Q. Public", "2", ext=[{"system": "fec", "value": "X1"}])
    result = resolve([a, b])
    assert len(result.clusters) == 1
    assert {"shared_external_id"} <= set(result.clusters[0].match_edges[0].score.reasons)


def test_resolve_transitive_merge() -> None:
    a = _rec("Jane M. Doe", "1", jurisdiction="us")
    b = _rec("Doe, Jane M.", "2", jurisdiction="us")
    c = _rec("Jane Marie Doe", "3", jurisdiction="us")
    result = resolve([a, b, c])
    assert len(result.clusters) == 1
    assert _cluster_of(result, a.record_id) == tuple(
        sorted((a.record_id, b.record_id, c.record_id))
    )


# ── resolve: review queue (possible band) ──────────────────────────


def test_resolve_possible_pair_goes_to_review_not_merged() -> None:
    # Same name block (doe|j), but a middle-name conflict makes it "possible":
    # family +2.5, given +1.5, middle -2.0, prior -1.5 = 0.5 -> p ~= 0.62.
    a = _rec("Jane Marie Doe", "1")
    b = _rec("Jane Anne Doe", "2")
    result = resolve([a, b])
    assert len(result.clusters) == 2  # NOT merged
    assert len(result.review_queue) == 1
    assert result.review_queue[0].score.decision == "possible"
    assert "middle_conflict" in result.review_queue[0].score.reasons


def test_resolve_conflict_not_merged_and_not_reviewed() -> None:
    a = _rec("Jane Doe", "1", ext=[{"system": "bioguide", "value": "D1"}])
    b = _rec("Jane Doe", "2", ext=[{"system": "bioguide", "value": "D2"}])
    result = resolve([a, b])
    assert len(result.clusters) == 2
    assert result.review_queue == ()


# ── resolve: singletons + completeness ─────────────────────────────


def test_resolve_singleton_gets_its_own_cluster() -> None:
    a = _rec("Solo Person", "1")
    result = resolve([a])
    assert len(result.clusters) == 1
    assert result.clusters[0].record_ids == (a.record_id,)
    assert result.clusters[0].match_edges == ()


def test_resolve_every_record_lands_in_exactly_one_cluster() -> None:
    recs = [
        _rec("Jane M. Doe", "1", jurisdiction="us"),
        _rec("Doe, Jane M.", "2", jurisdiction="us"),
        _rec("Bob Zimmerman", "3"),
        _rec("Acme PAC", "4", entity_type="org"),
    ]
    result = resolve(recs)
    placed = [rid for cluster in result.clusters for rid in cluster.record_ids]
    assert sorted(placed) == sorted(r.record_id for r in recs)
    assert len(placed) == len(set(placed))  # no record in two clusters


def test_resolve_dedupes_identical_source_records() -> None:
    a = _rec("Jane Doe", "1", system="s")
    dup = _rec("Jane Doe", "1", system="s")  # same (type, system, source_id)
    assert a.record_id == dup.record_id
    result = resolve([a, dup])
    assert len(result.clusters) == 1
    assert result.clusters[0].record_ids == (a.record_id,)


# ── determinism ────────────────────────────────────────────────────


def test_resolve_is_deterministic_regardless_of_input_order() -> None:
    a = _rec("Jane M. Doe", "1", jurisdiction="us")
    b = _rec("Doe, Jane M.", "2", jurisdiction="us")
    c = _rec("Bob Zimmerman", "3")
    forward = resolve([a, b, c])
    backward = resolve([c, b, a])
    assert [cl.canonical_id for cl in forward.clusters] == [
        cl.canonical_id for cl in backward.clusters
    ]
    assert forward.clusters == backward.clusters


def test_canonical_id_is_prefixed_and_membership_addressed() -> None:
    a = _rec("Jane M. Doe", "1", jurisdiction="us")
    b = _rec("Doe, Jane M.", "2", jurisdiction="us")
    cluster = resolve([a, b]).clusters[0]
    assert cluster.canonical_id.startswith("ce-")
