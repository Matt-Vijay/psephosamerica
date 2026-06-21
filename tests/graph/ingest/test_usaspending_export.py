from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.graph.cdc import diff_outputs
from src.graph.export import (
    DELTAS_FILENAME,
    MANIFEST_FILENAME,
    RECORDS_FILENAME,
    read_contract_corpus,
    write_contract_corpus,
    write_delta_feed,
)
from src.graph.ingest.usaspending_export import (
    AWARD_EDGES_FILENAME,
    INGEST_META_FILENAME,
    build_award_graph,
)

_OBSERVED = datetime(2026, 6, 20, tzinfo=UTC)


def _award(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "generated_internal_id": "CONT_AWD_1",
        "recipient_name": "Acme Research Institute",
        "recipient_uei": "ABC123DEF456",
        "awarding_toptier_agency_name": "Department of Health and Human Services",
        "awarding_toptier_agency_code": "075",
        "type": "C",
        "total_obligation": 1000.0,
        "action_date": "2022-03-15",
    }
    base.update(overrides)
    return base


def test_build_award_graph_resolves_and_dedupes() -> None:
    records = [
        _award(),
        # Same recipient (same UEI) + same agency, a second award -> one extra edge,
        # NOT extra org rows.
        _award(generated_internal_id="CONT_AWD_2", total_obligation=2000.0),
        # A different recipient, different agency.
        _award(
            generated_internal_id="GRANT_9",
            recipient_name="Beta College",
            recipient_uei="ZZZ999YYY888",
            awarding_toptier_agency_name="Department of Education",
            awarding_toptier_agency_code="091",
            type="04",
        ),
    ]
    rows, edges, scanned, skipped = build_award_graph(records, first_observed_at=_OBSERVED)
    assert scanned == 3
    assert skipped == 0
    # Orgs: Acme + HHS + Beta + Education = 4 distinct entities.
    assert len(rows) == 4
    assert all(r.entity_type == "org" for r in rows)
    # Three awards -> three federal_award edges (the repeated recipient/agency pair
    # stays distinct because the award id is the external key).
    award_edges = [e for e in edges if e.edge_type == "federal_award"]
    assert len(award_edges) == 3
    external_keys = {e.external_key for e in award_edges}
    assert external_keys == {"CONT_AWD_1", "CONT_AWD_2", "GRANT_9"}


def test_build_award_graph_skips_malformed() -> None:
    records = [_award(), {"recipient_name": "no id, no agency"}]
    rows, edges, scanned, skipped = build_award_graph(records, first_observed_at=_OBSERVED)
    assert scanned == 2
    assert skipped == 1
    assert len(rows) == 2  # one recipient + one agency from the good row


def test_appropriation_edge_emitted_when_public_law_present() -> None:
    records = [_award(public_law="Public Law 117-103")]
    _rows, edges, _scanned, _skipped = build_award_graph(records, first_observed_at=_OBSERVED)
    funded = [e for e in edges if e.edge_type == "funded_by"]
    assert len(funded) == 1
    assert funded[0].attributes["public_law"] == "117-103"
    assert funded[0].dst_id.startswith("cb-")


def test_export_writes_corpus_edges_cdc(tmp_path: Path) -> None:
    # Drive the artifact-writing path without network, mirroring export_usaspending.
    records = [_award(), _award(generated_internal_id="CONT_AWD_2", total_obligation=5.0)]
    rows, edges, _scanned, _skipped = build_award_graph(records, first_observed_at=_OBSERVED)
    write_contract_corpus(rows, directory=tmp_path, as_of=_OBSERVED)
    deltas = diff_outputs({}, {r.canonical_id: r for r in rows})
    write_delta_feed(deltas, path=tmp_path / DELTAS_FILENAME, append=False)
    edge_path = tmp_path / AWARD_EDGES_FILENAME
    with edge_path.open("w", encoding="utf-8") as handle:
        for edge in sorted(edges, key=lambda e: e.edge_id):
            handle.write(edge.model_dump_json() + "\n")

    assert (tmp_path / RECORDS_FILENAME).exists()
    assert (tmp_path / MANIFEST_FILENAME).exists()
    back = read_contract_corpus(tmp_path)
    assert len(back) == 2  # Acme + HHS, recipient deduped across the two awards
    delta_lines = (tmp_path / DELTAS_FILENAME).read_text().strip().splitlines()
    assert len(delta_lines) == 2
    assert all(json.loads(line)["change_type"] == "created" for line in delta_lines)
    edge_lines = edge_path.read_text().strip().splitlines()
    assert len(edge_lines) == 2  # two federal_award edges (distinct award ids)
    assert all(json.loads(line)["edge_type"] == "federal_award" for line in edge_lines)


def test_artifact_filename_constants() -> None:
    assert AWARD_EDGES_FILENAME == "award_edges.jsonl"
    assert INGEST_META_FILENAME == "ingest_meta.json"
