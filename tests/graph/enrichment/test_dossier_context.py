from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.bills import BillRef
from src.graph.contracts import build_entity_resolution_output
from src.graph.enrichment.dossier_context import DossierContext, build_dossier_context
from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.entity_resolution.records import SourceRecord
from src.graph.ingest.votes import vote_edge, vote_provenance
from src.graph.knowledge_graph import KnowledgeGraph
from src.graph.provenance import ProvenanceEnvelope


def _person(known: datetime):
    record = SourceRecord(
        source_system="congress_bioguide",
        source_record_id="P1",
        entity_type="person",
        display_name="Jane Doe",
        external_ids=[{"system": "bioguide", "value": "P000001"}],
        jurisdiction="us-congress",
        provenance=ProvenanceEnvelope(
            source_url="https://bioguide.congress.gov/search/bio/P000001",
            content_sha256="a" * 64,
            first_observed_at=known,
            valid_from=known.date(),
            known_at=known,
        ),
    )
    entity = build_canonical_entity(resolve([record]).clusters[0], {record.record_id: record})
    assert entity is not None
    return build_entity_resolution_output(entity)


_BILL = BillRef.for_congress(118, "H.R. 1").canonical_id


def _vote(member_id: str, *, choice: str, vote_date: date):
    return vote_edge(
        member_canonical_id=member_id,
        bill_canonical_id=_BILL,
        choice=choice,
        provenance=vote_provenance(
            source_url="https://clerk.house.gov/Votes/1",
            content_sha256="b" * 64,
            vote_date=vote_date,
            first_observed_at=datetime(
                vote_date.year, vote_date.month, vote_date.day, 23, tzinfo=UTC
            ),
        ),
    )


def _graph():
    jane = _person(datetime(2023, 1, 1, tzinfo=UTC))
    graph = KnowledgeGraph(
        nodes=[jane],
        edges=[
            _vote(jane.canonical_id, choice="yea", vote_date=date(2024, 3, 1)),
            _vote(jane.canonical_id, choice="nay", vote_date=date(2024, 6, 1)),
        ],
    )
    return graph, jane.canonical_id


# ── basic assembly ─────────────────────────────────────────────────


def test_context_carries_identity_and_facts() -> None:
    graph, jane_id = _graph()
    ctx = build_dossier_context(graph, jane_id, as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert ctx is not None
    assert ctx.canonical_id == jane_id
    assert ctx.display_name == "Jane Doe"
    assert ctx.external_ids == ["bioguide:p000001"]
    assert len(ctx.facts) == 2
    assert {f.relation for f in ctx.facts} == {"vote"}


def test_facts_are_source_anchored() -> None:
    graph, jane_id = _graph()
    ctx = build_dossier_context(graph, jane_id, as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert ctx is not None
    fact = ctx.facts[0]
    assert fact.source_url == "https://clerk.house.gov/Votes/1"
    assert fact.content_sha256 == "b" * 64
    assert fact.target_id == _BILL
    assert "choice" in fact.attributes


# ── leakage safety ─────────────────────────────────────────────────


def test_context_is_leakage_safe_to_as_of() -> None:
    graph, jane_id = _graph()
    # As of April 2024 only the March vote is knowable.
    ctx = build_dossier_context(graph, jane_id, as_of=datetime(2024, 4, 1, tzinfo=UTC))
    assert ctx is not None
    assert len(ctx.facts) == 1
    assert ctx.facts[0].attributes["choice"] == "yea"
    assert ctx.as_of == datetime(2024, 4, 1, tzinfo=UTC)


def test_no_context_before_entity_is_known() -> None:
    graph, jane_id = _graph()
    assert build_dossier_context(graph, jane_id, as_of=datetime(2020, 1, 1, tzinfo=UTC)) is None


def test_unknown_id_returns_none() -> None:
    graph, _ = _graph()
    assert (
        build_dossier_context(graph, "ce-missing", as_of=datetime(2025, 1, 1, tzinfo=UTC)) is None
    )


# ── determinism + serialization ────────────────────────────────────


def test_facts_sorted_deterministically() -> None:
    graph, jane_id = _graph()
    ctx = build_dossier_context(graph, jane_id, as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert ctx is not None
    knowns = [f.known_at for f in ctx.facts]
    assert knowns == sorted(knowns)


def test_context_round_trips_json() -> None:
    graph, jane_id = _graph()
    ctx = build_dossier_context(graph, jane_id, as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert ctx is not None
    assert DossierContext.model_validate_json(ctx.model_dump_json()) == ctx


def test_entity_with_no_edges_has_empty_facts() -> None:
    jane = _person(datetime(2023, 1, 1, tzinfo=UTC))
    graph = KnowledgeGraph(nodes=[jane], edges=[])
    ctx = build_dossier_context(graph, jane.canonical_id, as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert ctx is not None
    assert ctx.facts == []
