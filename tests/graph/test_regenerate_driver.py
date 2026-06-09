from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.contracts import (
    ContractSourceAnchor,
    build_bill_output,
)
from src.graph.enrichment.local_embedder import default_text_embedder
from src.graph.entity_resolution.records import SourceRecord
from src.graph.ingest.votes import vote_edge, vote_provenance
from src.graph.provenance import ProvenanceEnvelope
from src.graph.regenerate import corpus_stats, regenerate_corpus

_T = datetime(2025, 1, 1, tzinfo=UTC)


def _person(rid: str, name: str, *, known: datetime) -> SourceRecord:
    return SourceRecord(
        source_system="house_clerk",
        source_record_id=rid,
        entity_type="person",
        display_name=name,
        external_ids=[{"system": "bioguide", "value": rid}],
        jurisdiction="us-congress",
        provenance=ProvenanceEnvelope(
            source_url=f"https://bioguide.congress.gov/search/bio/{rid}",
            content_sha256="a" * 64,
            first_observed_at=known,
            valid_from=known.date(),
            known_at=known,
        ),
    )


def _bill(cid: str, *, known: datetime):
    anchor = ContractSourceAnchor(
        source_system="congress",
        record_id=cid,
        source_url=f"https://www.congress.gov/{cid}",
        content_sha256="b" * 64,
        content_address="sha256/bb/bb/" + "b" * 64,
        known_at=known,
        valid_from=known.date(),
    )
    return build_bill_output(canonical_bill_id=cid, display_name=cid, source_anchors=[anchor])


def _person_canonical_id(record: SourceRecord) -> str:
    from src.graph.materialize import materialize_person_nodes

    return materialize_person_nodes([record])[0][0].canonical_id


# ── regenerate_corpus ──────────────────────────────────────────────


def test_all_known_rows_reach_ready_with_populated_fields() -> None:
    p = _person("A000055", "Robert Aderholt", known=datetime(2023, 1, 1, tzinfo=UTC))
    bill = _bill("cb-118-hr-1", known=datetime(2023, 1, 9, tzinfo=UTC))
    edge = vote_edge(
        member_canonical_id=_person_canonical_id(p),
        bill_canonical_id="cb-118-hr-1",
        choice="yea",
        provenance=vote_provenance(
            source_url="https://clerk.house.gov/Votes/1",
            content_sha256="c" * 64,
            vote_date=date(2024, 3, 1),
            first_observed_at=datetime(2024, 3, 2, tzinfo=UTC),
        ),
    )
    result = regenerate_corpus(person_records=[p], bill_outputs=[bill], edges=[edge], as_of=_T)
    assert len(result.rows) == 2
    for row in result.rows:
        assert row.enrichment_status == "ready"
        assert row.dossier_json is not None
        assert row.dossier_embedding is not None and len(row.dossier_embedding) == 256
        assert row.structural_embedding is not None
    # First run -> all created in the delta feed.
    assert {d.change_type for d in result.deltas} == {"created"}
    assert result.assignment.by_record  # persistent ids assigned


def test_future_dated_entities_excluded_by_as_of() -> None:
    p = _person("A000055", "Future Member", known=datetime(2026, 6, 1, tzinfo=UTC))
    result = regenerate_corpus(
        person_records=[p], bill_outputs=[], edges=[], as_of=datetime(2024, 1, 1, tzinfo=UTC)
    )
    assert result.rows == []  # not yet known


def test_persistent_id_stable_and_cdc_update_on_second_run() -> None:
    p = _person("A000055", "Robert Aderholt", known=datetime(2023, 1, 1, tzinfo=UTC))
    run1 = regenerate_corpus(
        person_records=[p], bill_outputs=[], edges=[], as_of=datetime(2024, 1, 1, tzinfo=UTC)
    )
    prior = {r.canonical_id: r for r in run1.rows}
    bill = _bill("cb-x", known=datetime(2024, 5, 1, tzinfo=UTC))
    edge = vote_edge(
        member_canonical_id=run1.rows[0].canonical_id,
        bill_canonical_id="cb-x",
        choice="yea",
        provenance=vote_provenance(
            source_url="https://clerk.house.gov/Votes/2",
            content_sha256="d" * 64,
            vote_date=date(2024, 6, 1),
            first_observed_at=datetime(2024, 6, 2, tzinfo=UTC),
        ),
    )
    run2 = regenerate_corpus(
        person_records=[p],
        bill_outputs=[bill],
        edges=[edge],
        as_of=_T,
        prior_assignment=run1.assignment,
        prior_outputs=prior,
    )
    person_row = next(r for r in run2.rows if r.canonical_person_id)
    assert person_row.canonical_id == run1.rows[0].canonical_id  # stable
    person_delta = next(d for d in run2.deltas if d.canonical_id == person_row.canonical_id)
    assert person_delta.change_type == "updated"
    assert person_delta.enrichment_changed  # embedding/dossier changed


# ── corpus_stats ───────────────────────────────────────────────────


def test_corpus_stats() -> None:
    p = _person("A000055", "Robert Aderholt", known=datetime(2023, 1, 1, tzinfo=UTC))
    bill = _bill("cb-118-hr-1", known=datetime(2023, 1, 9, tzinfo=UTC))
    result = regenerate_corpus(person_records=[p], bill_outputs=[bill], edges=[], as_of=_T)
    stats = corpus_stats(result.rows)
    assert stats["rows"] == 2
    assert stats["ready"] == 2
    assert stats["pct_dossier_embedding_non_null"] == 100.0
    assert stats["pct_structural_embedding_non_null"] == 100.0
    assert stats["pct_dossier_json_non_null"] == 100.0
    assert 0.0 <= stats["mean_dossier_embedding_l2"] <= 1.01


def test_corpus_stats_empty() -> None:
    stats = corpus_stats([])
    assert stats["rows"] == 0
    assert stats["mean_dossier_embedding_l2"] == 0.0


def test_corpus_stats_zero_norm_embedding() -> None:
    # A degenerate empty embedding vector contributes L2 0.0.
    row = build_bill_output(
        canonical_bill_id="cb-z",
        display_name="z",
        source_anchors=[
            ContractSourceAnchor(
                source_system="s",
                record_id="r",
                source_url="https://x/z",
                content_sha256="b" * 64,
                content_address="sha256/bb/bb/" + "b" * 64,
                known_at=_T,
                valid_from=date(2024, 1, 1),
            )
        ],
    ).model_copy(update={"dossier_embedding": []})
    stats = corpus_stats([row])
    assert stats["mean_dossier_embedding_l2"] == 0.0


def test_custom_embedder_used() -> None:
    p = _person("A000055", "X", known=datetime(2023, 1, 1, tzinfo=UTC))
    calls: list[str] = []

    def spy(text: str) -> list[float]:
        calls.append(text)
        return default_text_embedder()(text)

    regenerate_corpus(person_records=[p], bill_outputs=[], edges=[], as_of=_T, embedder=spy)
    assert calls  # the injected embedder was used
