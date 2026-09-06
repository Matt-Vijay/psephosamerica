from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from src.graph.contracts import ContractSourceAnchor, build_bill_output
from src.graph.cron import load_assignment, run_cycle, save_assignment
from src.graph.entity_resolution.assignment import CanonicalAssignment
from src.graph.entity_resolution.records import SourceRecord
from src.graph.ingest.votes import vote_edge, vote_provenance
from src.graph.materialize import materialize_person_nodes
from src.graph.provenance import ProvenanceEnvelope

_T1 = datetime(2024, 4, 1, tzinfo=UTC)
_T2 = datetime(2024, 7, 1, tzinfo=UTC)


def _person() -> SourceRecord:
    return SourceRecord(
        source_system="house_clerk",
        source_record_id="A000055",
        entity_type="person",
        display_name="Robert Aderholt",
        external_ids=[{"system": "bioguide", "value": "A000055"}],
        jurisdiction="us-congress",
        provenance=ProvenanceEnvelope(
            source_url="https://bioguide.congress.gov/search/bio/A000055",
            content_sha256="a" * 64,
            first_observed_at=datetime(2023, 1, 1, tzinfo=UTC),
            valid_from=date(2023, 1, 1),
            known_at=datetime(2023, 1, 1, tzinfo=UTC),
        ),
    )


def _bill():
    return build_bill_output(
        canonical_bill_id="cb-1",
        display_name="cb-1",
        source_anchors=[
            ContractSourceAnchor(
                source_system="congress",
                record_id="cb-1",
                source_url="https://congress.gov/cb-1",
                content_sha256="b" * 64,
                content_address="sha256/bb/bb/" + "b" * 64,
                known_at=datetime(2023, 1, 1, tzinfo=UTC),
                valid_from=date(2023, 1, 1),
            )
        ],
    )


def _vote(member_id: str, *, vote_date: date):
    return vote_edge(
        member_canonical_id=member_id,
        bill_canonical_id="cb-1",
        choice="yea",
        provenance=vote_provenance(
            source_url="https://clerk.house.gov/Votes/1",
            content_sha256="c" * 64,
            vote_date=vote_date,
            first_observed_at=datetime(
                vote_date.year, vote_date.month, vote_date.day, 23, tzinfo=UTC
            ),
        ),
    )


# ── assignment persistence ─────────────────────────────────────────


def test_save_load_assignment_roundtrip(tmp_path: Path) -> None:
    a = CanonicalAssignment(by_record={"r1": "ce-1"}, superseded_ids=frozenset({"ce-old"}))
    path = tmp_path / "assignment.json"
    save_assignment(a, path)
    loaded = load_assignment(path)
    assert loaded is not None
    assert loaded.by_record == {"r1": "ce-1"}
    assert loaded.superseded_ids == frozenset({"ce-old"})


def test_load_missing_assignment_is_none(tmp_path: Path) -> None:
    assert load_assignment(tmp_path / "nope.json") is None


# ── run_cycle: idempotent + stateful ───────────────────────────────


def test_first_cycle_writes_corpus_and_state(tmp_path: Path) -> None:
    p = _person()
    result = run_cycle(
        person_records=[p], bill_outputs=[_bill()], edges=[], as_of=_T1, state_dir=tmp_path
    )
    assert len(result.rows) == 2  # person + bill, both ready
    assert all(r.enrichment_status == "ready" for r in result.rows)
    assert (tmp_path / "records.jsonl").exists()
    assert (tmp_path / "assignment.json").exists()
    assert {d.change_type for d in result.deltas} == {"created"}


def test_second_identical_cycle_is_idempotent(tmp_path: Path) -> None:
    p = _person()
    run_cycle(person_records=[p], bill_outputs=[_bill()], edges=[], as_of=_T1, state_dir=tmp_path)
    hash1 = (tmp_path / "manifest.json").read_text()
    second = run_cycle(
        person_records=[p], bill_outputs=[_bill()], edges=[], as_of=_T1, state_dir=tmp_path
    )
    hash2 = (tmp_path / "manifest.json").read_text()
    assert hash1 == hash2  # byte-identical corpus
    assert second.deltas == []  # nothing changed -> no spurious deltas


def test_incremental_cycle_advances_with_new_facts(tmp_path: Path) -> None:
    p = _person()
    member_id = materialize_person_nodes([p])[0][0].canonical_id
    run_cycle(person_records=[p], bill_outputs=[_bill()], edges=[], as_of=_T1, state_dir=tmp_path)
    # Next cycle: a vote is now known; as_of advances.
    second = run_cycle(
        person_records=[p],
        bill_outputs=[_bill()],
        edges=[_vote(member_id, vote_date=date(2024, 6, 1))],
        as_of=_T2,
        state_dir=tmp_path,
    )
    person_delta = next(d for d in second.deltas if d.canonical_id == member_id)
    assert person_delta.change_type == "updated"
    assert person_delta.enrichment_changed
    # The delta feed accumulates across cycles (appended, tailable).
    feed_lines = (tmp_path / "deltas.jsonl").read_text().splitlines()
    assert len(feed_lines) >= 3  # 2 created (cycle 1) + >=1 updated (cycle 2)


def test_persistent_id_stable_across_cycles(tmp_path: Path) -> None:
    p = _person()
    member_id = materialize_person_nodes([p])[0][0].canonical_id
    r1 = run_cycle(person_records=[p], bill_outputs=[], edges=[], as_of=_T1, state_dir=tmp_path)
    r2 = run_cycle(person_records=[p], bill_outputs=[], edges=[], as_of=_T2, state_dir=tmp_path)
    id1 = next(r.canonical_id for r in r1.rows if r.canonical_person_id)
    id2 = next(r.canonical_id for r in r2.rows if r.canonical_person_id)
    assert id1 == id2 == member_id
