"""Deterministic-replay pin: reconstruct the contract corpus at past time t.

Given the same inputs and the same ``as_of`` t, the whole pipeline
(resolve -> materialize -> graph -> RGCN-lite + local-embedder enrichment ->
JSONL export) must reproduce a **byte-identical** corpus — the
content-addressed manifest hash is the pin. And it must be leakage-safe: a fact
knowable only after t cannot appear in the t reconstruction.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from src.graph.contracts import ContractSourceAnchor, build_bill_output
from src.graph.entity_resolution.records import SourceRecord
from src.graph.export import write_contract_corpus
from src.graph.ingest.votes import vote_edge, vote_provenance
from src.graph.materialize import materialize_person_nodes
from src.graph.provenance import ProvenanceEnvelope
from src.graph.regenerate import regenerate_corpus

_T1 = datetime(2024, 4, 1, tzinfo=UTC)  # after the March vote, before the June one
_T2 = datetime(2024, 7, 1, tzinfo=UTC)  # after both votes


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


def _bill(cid: str) -> object:
    return build_bill_output(
        canonical_bill_id=cid,
        display_name=cid,
        source_anchors=[
            ContractSourceAnchor(
                source_system="congress",
                record_id=cid,
                source_url=f"https://congress.gov/{cid}",
                content_sha256="b" * 64,
                content_address="sha256/bb/bb/" + "b" * 64,
                known_at=datetime(2023, 1, 1, tzinfo=UTC),
                valid_from=date(2023, 1, 1),
            )
        ],
    )


def _vote(member_id: str, bill_id: str, *, vote_date: date, sha: str):
    return vote_edge(
        member_canonical_id=member_id,
        bill_canonical_id=bill_id,
        choice="yea",
        provenance=vote_provenance(
            source_url=f"https://clerk.house.gov/Votes/{bill_id}",
            content_sha256=sha * 64,
            vote_date=vote_date,
            first_observed_at=datetime(
                vote_date.year, vote_date.month, vote_date.day, 23, tzinfo=UTC
            ),
        ),
    )


def _scenario():
    person = _person()
    member_id = materialize_person_nodes([person])[0][0].canonical_id
    bills = [_bill("cb-1"), _bill("cb-2")]
    edges = [
        _vote(member_id, "cb-1", vote_date=date(2024, 3, 1), sha="c"),  # known by T1
        _vote(member_id, "cb-2", vote_date=date(2024, 6, 1), sha="d"),  # known only by T2
    ]
    return person, bills, edges, member_id


def _reconstruct(directory: Path, *, as_of: datetime) -> str:
    person, bills, edges, _ = _scenario()
    result = regenerate_corpus(
        person_records=[person], bill_outputs=bills, edges=edges, as_of=as_of
    )
    return write_contract_corpus(result.rows, directory=directory, as_of=as_of).content_sha256


def test_replay_is_byte_identical(tmp_path: Path) -> None:
    h1 = _reconstruct(tmp_path / "a", as_of=_T1)
    h2 = _reconstruct(tmp_path / "b", as_of=_T1)
    assert h1 == h2  # same t -> bit-identical corpus
    assert (tmp_path / "a" / "records.jsonl").read_bytes() == (
        tmp_path / "b" / "records.jsonl"
    ).read_bytes()


def test_different_t_yields_different_corpus(tmp_path: Path) -> None:
    assert _reconstruct(tmp_path / "t1", as_of=_T1) != _reconstruct(tmp_path / "t2", as_of=_T2)


def test_future_facts_excluded_from_past_replay(tmp_path: Path) -> None:
    person, bills, edges, member_id = _scenario()
    at_t1 = regenerate_corpus(person_records=[person], bill_outputs=bills, edges=edges, as_of=_T1)
    at_t2 = regenerate_corpus(person_records=[person], bill_outputs=bills, edges=edges, as_of=_T2)
    person_t1 = next(r for r in at_t1.rows if r.canonical_id == member_id)
    person_t2 = next(r for r in at_t2.rows if r.canonical_id == member_id)
    # The June vote is invisible at T1 -> fewer dossier claims, different embedding.
    assert len(person_t1.dossier_json["claims"]) == 1
    assert len(person_t2.dossier_json["claims"]) == 2
    assert person_t1.dossier_embedding != person_t2.dossier_embedding
    # cb-2's vote-in edge is not knowable at T1.
    bill2_t1 = next(r for r in at_t1.rows if r.canonical_id == "cb-2")
    assert bill2_t1.dossier_json["claims"] == []
