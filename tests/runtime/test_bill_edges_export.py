from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput
from src.graph.edges import GraphEdge
from src.graph.export import write_contract_corpus
from src.graph.ingest.govinfo_billstatus import billstatus_from_record, canonical_bill_id
from src.runtime.bill_edges_export import (
    build_bioguide_resolver,
    edges_for_record,
    export_bill_edges,
)

_OBS = datetime(2024, 6, 1, tzinfo=UTC)


def _anchor() -> ContractSourceAnchor:
    return ContractSourceAnchor(
        source_system="house_clerk",
        record_id="r",
        source_url="https://x",
        content_sha256="a" * 64,
        content_address="sha256/aa/aa/" + "a" * 64,
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        valid_from=datetime(2023, 1, 1, tzinfo=UTC).date(),
    )


def _person(cid: str, bioguide: str) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="person",
        display_name="Rep",
        external_ids=[f"bioguide:{bioguide}"],
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        source_anchors=[_anchor()],
    )


_PARTIAL = {
    "canonical_id": "cb-1",
    "policy_area": "Energy",
    "subjects": ["Oil and gas", "Energy prices"],
    "text": "Lower Energy Costs Act",
}
_FULL = {
    "canonical_id": canonical_bill_id(
        billstatus_from_record(
            {"congress": 118, "bill_type": "hr", "number": 2, "title": "Health Act"}
        )
    ),
    "congress": 118,
    "bill_type": "hr",
    "number": 2,
    "title": "Health Act",
    "introduced_date": "2023-03-14",
    "policy_area": "Health",
    "subjects": ["Medicare"],
    "sponsors": [{"bioguide_id": "S001176", "full_name": "Scalise", "sponsorship_date": None}],
    "cosponsors": [],
    "committees": [{"name": "Ways and Means", "system_code": "hswm00", "chamber": "House"}],
    "summary_text": "A health bill.",
    "text": "Health Act. Policy area: Health.",
}


def _bill_row(cid: str) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="bill",
        display_name="A bill",
        external_ids=["congress:118-hr-1"],
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        source_anchors=[_anchor()],
    )


def _person_no_bioguide(cid: str) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="person",
        display_name="Rep",
        external_ids=["fec:H0X"],  # not a bioguide id
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        source_anchors=[_anchor()],
    )


def test_build_bioguide_resolver(tmp_path: Path) -> None:
    corpus = tmp_path / "contract_records"
    # mix a person, a non-person (skipped), and a person without a bioguide id (skipped)
    write_contract_corpus(
        [_person("cp-1", "S001176"), _bill_row("cb-9"), _person_no_bioguide("cp-9")],
        directory=corpus,
        as_of=_OBS,
    )
    assert build_bioguide_resolver(corpus) == {"S001176": "cp-1"}
    assert build_bioguide_resolver(tmp_path / "absent") == {}


def test_edges_for_partial_record_are_classification_only() -> None:
    edges = edges_for_record(_PARTIAL, resolve_bioguide={}, first_observed_at=_OBS)
    types = sorted(e.edge_type for e in edges)
    assert types == ["legislative_subject", "legislative_subject", "policy_area"]
    assert all(e.src_id == "cb-1" for e in edges)
    # no introduced date -> leakage known_at falls back to the observation time
    assert all(e.provenance.known_at == _OBS for e in edges)


def test_edges_for_full_record_add_sponsorship_and_committee() -> None:
    edges = edges_for_record(_FULL, resolve_bioguide={"S001176": "cp-1"}, first_observed_at=_OBS)
    by_type = sorted(e.edge_type for e in edges)
    assert "policy_area" in by_type
    assert "legislative_subject" in by_type
    assert "sponsorship" in by_type
    assert "referred_to" in by_type
    sponsor = next(e for e in edges if e.edge_type == "sponsorship")
    assert sponsor.src_id == "cp-1" and sponsor.dst_id == _FULL["canonical_id"]
    # introduced date drives the leakage gate
    assert sponsor.provenance.valid_from.isoformat() == "2023-03-14"


def test_export_writes_feed_and_is_resumable(tmp_path: Path) -> None:
    corpus = tmp_path / "contract_records"
    sidecar = tmp_path / "bill_content.jsonl"
    out = tmp_path / "bill_edges.jsonl"
    write_contract_corpus([_person("cp-1", "S001176")], directory=corpus, as_of=_OBS)
    # blank line between records exercises the sidecar skip branch
    sidecar.write_text(json.dumps(_PARTIAL) + "\n\n" + json.dumps(_FULL) + "\n", encoding="utf-8")

    first = export_bill_edges(
        content_sidecar=sidecar, corpus_directory=corpus, out_path=out, first_observed_at=_OBS
    )
    assert first.bills_new == 2
    assert first.by_type["policy_area"] == 2  # one per bill
    assert first.by_type["sponsorship"] == 1
    assert first.by_type["referred_to"] == 1
    # every line is a valid GraphEdge
    lines = out.read_text().strip().splitlines()
    assert first.edges_written == len(lines)
    GraphEdge.model_validate_json(lines[0])

    # second pass: both bills already done -> nothing new (blank line in the feed
    # exercises the edge-file skip branch when reloading done ids)
    with out.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    second = export_bill_edges(
        content_sidecar=sidecar, corpus_directory=corpus, out_path=out, first_observed_at=_OBS
    )
    assert second.bills_new == 0 and second.edges_written == 0
    assert second.total_edges == first.total_edges


def test_export_respects_max_bills(tmp_path: Path) -> None:
    corpus = tmp_path / "contract_records"
    sidecar = tmp_path / "bill_content.jsonl"
    out = tmp_path / "bill_edges.jsonl"
    write_contract_corpus([_person("cp-1", "S001176")], directory=corpus, as_of=_OBS)
    sidecar.write_text(json.dumps(_PARTIAL) + "\n" + json.dumps(_FULL) + "\n", encoding="utf-8")
    progress = export_bill_edges(
        content_sidecar=sidecar,
        corpus_directory=corpus,
        out_path=out,
        first_observed_at=_OBS,
        max_bills=1,
    )
    assert progress.bills_new == 1


def test_export_handles_missing_sidecar(tmp_path: Path) -> None:
    corpus = tmp_path / "contract_records"
    write_contract_corpus([_person("cp-1", "S001176")], directory=corpus, as_of=_OBS)
    progress = export_bill_edges(
        content_sidecar=tmp_path / "absent.jsonl",
        corpus_directory=corpus,
        out_path=tmp_path / "edges.jsonl",
        first_observed_at=_OBS,
    )
    assert progress.bills_seen == 0 and progress.edges_written == 0
