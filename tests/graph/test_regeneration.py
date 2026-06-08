from __future__ import annotations

from datetime import UTC, date, datetime

from src.graph.bills import BillRef
from src.graph.enrichment.dossier import DossierClaimDraft
from src.graph.ingest.congress_members import member_provenance, source_record_from_member_entry
from src.graph.ingest.votes import vote_edge, vote_provenance
from src.graph.regeneration import RegenerationResult, regenerate_entities
from src.identity.current_member_lookup import CurrentMemberLookupEntry

_BILL = BillRef.for_congress(118, "H.R. 1").canonical_id


def _entry(bioguide: str, name: str) -> CurrentMemberLookupEntry:
    return CurrentMemberLookupEntry(
        bioguide_id=bioguide,
        slug=name.lower().replace(" ", "-"),
        name=name,
        search_name=name.lower(),
        state="CA",
        chamber="house",
    )


def _record(bioguide: str, name: str, *, known: datetime):
    entry = _entry(bioguide, name)
    return source_record_from_member_entry(
        entry,
        member_provenance(
            entry,
            snapshot_date=known.date(),
            content_sha256="a" * 64,
            first_observed_at=known,
        ),
    )


def _vote(member_id: str, *, vote_date: date):
    return vote_edge(
        member_canonical_id=member_id,
        bill_canonical_id=_BILL,
        choice="yea",
        provenance=vote_provenance(
            source_url="https://clerk.house.gov/Votes/1",
            content_sha256="b" * 64,
            vote_date=vote_date,
            first_observed_at=datetime(
                vote_date.year, vote_date.month, vote_date.day, 23, tzinfo=UTC
            ),
        ),
    )


def _model(prompt: str) -> tuple[str, list[DossierClaimDraft]]:
    # Anchor every claim to a source present in the prompt (the vote).
    claims = []
    if "clerk.house.gov" in prompt:
        claims.append(
            DossierClaimDraft(
                text="Voted yea.",
                source_url="https://clerk.house.gov/Votes/1",
                content_sha256="b" * 64,
            )
        )
    return "A California representative.", claims


def _embedder(text: str) -> list[float]:
    return [float(len(text))]


# ── full cycle ─────────────────────────────────────────────────────


def test_regenerate_emits_ready_enriched_rows() -> None:
    member = _record("P000001", "Jane Doe", known=datetime(2023, 1, 1, tzinfo=UTC))
    result = regenerate_entities(
        person_records=[member],
        edges=[_vote(_member_id(member), vote_date=date(2024, 3, 1))],
        dossier_model=_model,
        embedder=_embedder,
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert isinstance(result, RegenerationResult)
    assert len(result.outputs) == 1
    out = result.outputs[0]
    assert out.enrichment_status == "ready"
    assert out.dossier_json is not None
    assert out.dossier_embedding is not None
    assert out.structural_embedding is not None
    # First run: everything is "created".
    assert len(result.deltas) == 1
    assert result.deltas[0].change_type == "created"


def test_regenerate_is_leakage_safe() -> None:
    member = _record("P000001", "Jane Doe", known=datetime(2024, 6, 1, tzinfo=UTC))
    # As of January 2024 the member is not yet known -> nothing emitted.
    result = regenerate_entities(
        person_records=[member],
        edges=[],
        dossier_model=_model,
        embedder=_embedder,
        as_of=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert result.outputs == []
    assert result.deltas == []


def test_regenerate_second_run_reports_update_on_stable_id() -> None:
    member1 = _record("P000001", "Jane Doe", known=datetime(2023, 1, 1, tzinfo=UTC))
    run1 = regenerate_entities(
        person_records=[member1],
        edges=[],
        dossier_model=_model,
        embedder=_embedder,
        as_of=datetime(2024, 1, 1, tzinfo=UTC),
    )
    prior_outputs = {o.canonical_id: o for o in run1.outputs}

    # Second run: a vote is now known -> structural features change.
    run2 = regenerate_entities(
        person_records=[member1],
        edges=[_vote(_member_id(member1), vote_date=date(2024, 3, 1))],
        dossier_model=_model,
        embedder=_embedder,
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
        prior_assignment=run1.assignment,
        prior_outputs=prior_outputs,
    )
    assert run2.outputs[0].canonical_id == run1.outputs[0].canonical_id  # stable id
    assert len(run2.deltas) == 1
    assert run2.deltas[0].change_type == "updated"


def test_regenerate_empty() -> None:
    result = regenerate_entities(
        person_records=[],
        edges=[],
        dossier_model=_model,
        embedder=_embedder,
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert result.outputs == []
    assert result.deltas == []


def _member_id(record) -> str:  # type: ignore[no-untyped-def]
    # The canonical id a single-record member resolves to == its content-addressed
    # cluster id == persistent id on first sight; recompute the same way the
    # pipeline does by running a one-record regeneration.
    from src.graph.materialize import materialize_person_nodes

    nodes, _ = materialize_person_nodes([record])
    return nodes[0].canonical_id
