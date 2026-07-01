from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.graph.bills import BillRef
from src.graph.cdc import diff_outputs
from src.graph.export import (
    DELTAS_FILENAME,
    MANIFEST_FILENAME,
    RECORDS_FILENAME,
    read_contract_corpus,
    write_contract_corpus,
    write_delta_feed,
)
from src.graph.ingest.lda_export import (
    INGEST_META_FILENAME,
    LOBBYING_EDGES_FILENAME,
    YearStats,
    build_lobbying_graph,
    year_coverage_stats,
)

_OBSERVED = datetime(2026, 6, 20, tzinfo=UTC)


def _filing(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "filing_uuid": "11111111-1111-1111-1111-111111111111",
        "filing_year": 2024,
        "filing_type_display": "Q1",
        "dt_posted": "2024-04-20T10:00:00-04:00",
        "income": "50000.00",
        "expenses": None,
        "client": {"name": "Acme Corp", "id": 100, "state": "IL"},
        "registrant": {"name": "Big Lobby LLC", "id": 200, "state": "DC"},
        "lobbying_activities": [
            {"general_issue_area_code": "HCR", "description": "Re H.R. 2471 appropriations."}
        ],
    }
    base.update(overrides)
    return base


def test_build_lobbying_graph_resolves_and_links_bills() -> None:
    records = [
        _filing(),
        # Same client + registrant, second filing -> one extra retention edge, no new orgs.
        _filing(
            filing_uuid="22222222-2222-2222-2222-222222222222",
            lobbying_activities=[
                {"general_issue_area_code": "TAX", "description": "S. 4321 tax credit."}
            ],
        ),
    ]
    rows, edges, scanned, skipped = build_lobbying_graph(records, first_observed_at=_OBSERVED)
    assert scanned == 2
    assert skipped == 0
    # Two orgs: Acme (client) + Big Lobby (registrant), deduped across filings.
    assert len(rows) == 2
    assert all(r.entity_type == "org" for r in rows)

    retention = [e for e in edges if e.edge_type == "lobbying_retention"]
    contacts = [e for e in edges if e.edge_type == "lobbying_contact"]
    assert len(retention) == 2  # one per filing
    assert len(contacts) == 2  # H.R. 2471 and S. 4321
    bill_targets = {e.dst_id for e in contacts}
    assert BillRef.for_congress(118, "H.R. 2471").canonical_id in bill_targets
    assert BillRef.for_congress(118, "S. 4321").canonical_id in bill_targets


def test_build_lobbying_graph_skips_malformed() -> None:
    records = [_filing(), {"filing_uuid": "", "client": {}, "registrant": {}}]
    rows, _edges, scanned, skipped = build_lobbying_graph(records, first_observed_at=_OBSERVED)
    assert scanned == 2
    assert skipped == 1
    assert len(rows) == 2


def test_filing_without_bill_still_links_retention() -> None:
    records = [
        _filing(
            lobbying_activities=[{"general_issue_area_code": "TAX", "description": "no bill named"}]
        )
    ]
    _rows, edges, _scanned, _skipped = build_lobbying_graph(records, first_observed_at=_OBSERVED)
    assert any(e.edge_type == "lobbying_retention" for e in edges)
    assert not any(e.edge_type == "lobbying_contact" for e in edges)


def test_export_writes_corpus_edges_cdc(tmp_path: Path) -> None:
    records = [_filing()]
    rows, edges, _scanned, _skipped = build_lobbying_graph(records, first_observed_at=_OBSERVED)
    write_contract_corpus(rows, directory=tmp_path, as_of=_OBSERVED)
    deltas = diff_outputs({}, {r.canonical_id: r for r in rows})
    write_delta_feed(deltas, path=tmp_path / DELTAS_FILENAME, append=False)
    edge_path = tmp_path / LOBBYING_EDGES_FILENAME
    with edge_path.open("w", encoding="utf-8") as handle:
        for edge in sorted(edges, key=lambda e: e.edge_id):
            handle.write(edge.model_dump_json() + "\n")

    assert (tmp_path / RECORDS_FILENAME).exists()
    assert (tmp_path / MANIFEST_FILENAME).exists()
    back = read_contract_corpus(tmp_path)
    assert len(back) == 2
    delta_lines = (tmp_path / DELTAS_FILENAME).read_text().strip().splitlines()
    assert all(json.loads(line)["change_type"] == "created" for line in delta_lines)
    edge_types = {json.loads(line)["edge_type"] for line in edge_path.read_text().splitlines()}
    assert edge_types == {"lobbying_retention", "lobbying_contact"}


def test_artifact_filename_constants() -> None:
    assert LOBBYING_EDGES_FILENAME == "lobbying_edges.jsonl"
    assert INGEST_META_FILENAME == "ingest_meta.json"


def test_year_coverage_stats_counts_null_descriptions_and_bills() -> None:
    records = [
        _filing(),  # one activity, has description + H.R. 2471 bill
        _filing(
            filing_uuid="33333333-3333-3333-3333-333333333333",
            lobbying_activities=[
                {"general_issue_area_code": "TAX", "description": None},  # null desc, real
                {"general_issue_area_code": "ENV", "description": "no bill cited here"},
            ],
        ),
    ]
    stats = year_coverage_stats(records, year=2024)
    assert stats["year"] == 2024
    assert stats["filings"] == 2
    assert stats["activities"] == 3
    # Two of three activity lines carry a non-empty description; one is genuinely null.
    assert stats["activities_with_description"] == 2
    # Only the first filing names a congress bill.
    assert stats["filings_with_bill"] == 1


def test_year_stats_fill_rates_and_serialization() -> None:
    stats = YearStats(
        year=2020,
        filings=10,
        filings_skipped=0,
        activities=8,
        activities_with_description=6,
        filings_with_bill=2,
        registrants=0,
        clients=0,
        retention_edges=10,
        bill_lobbying_edges=3,
    )
    assert stats.description_fill_rate == 0.75
    assert stats.bill_fill_rate == 0.2
    payload = stats.as_dict()
    assert payload["description_fill_rate"] == 0.75
    assert payload["bill_fill_rate"] == 0.2
    assert payload["year"] == 2020


def test_year_stats_empty_fill_rates_do_not_divide_by_zero() -> None:
    stats = YearStats(
        year=2019,
        filings=0,
        filings_skipped=0,
        activities=0,
        activities_with_description=0,
        filings_with_bill=0,
        registrants=0,
        clients=0,
        retention_edges=0,
        bill_lobbying_edges=0,
    )
    assert stats.description_fill_rate == 0.0
    assert stats.bill_fill_rate == 0.0
