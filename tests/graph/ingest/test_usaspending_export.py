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
    FETCH_STATE_FILENAME,
    INGEST_META_FILENAME,
    RAW_RECORDS_FILENAME,
    _sum_award_dollars,
    build_award_graph,
    fetch_raw_records,
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


class _FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self._body = body

    def json(self) -> dict[str, object]:
        return self._body


class _FakeClient:
    """Replays scripted pages keyed by (family-first-code, cursor) for one family."""

    def __init__(self, pages: list[dict[str, object]]) -> None:
        self._pages = pages
        self.calls = 0

    def post(self, _endpoint: str, json: dict[str, object]) -> _FakeResponse:  # noqa: A002
        idx = self.calls
        self.calls += 1
        if idx < len(self._pages):
            return _FakeResponse(self._pages[idx])
        return _FakeResponse({"results": [], "page_metadata": {"hasNext": False}})


def _row(award_id: str, amount: float) -> dict[str, object]:
    return {
        "generated_internal_id": award_id,
        "Recipient Name": f"Recipient {award_id}",
        "Recipient UEI": "ABC123DEF456",
        "Awarding Agency": "Department of Defense",
        "Award Type": "C",
        "Award Amount": amount,
        "Action Date": "2024-01-01",
    }


def test_fetch_raw_records_cursor_paginates_and_checkpoints(tmp_path: Path) -> None:
    # Two pages then exhaustion: cursor carries last_record_unique_id forward.
    pages: list[dict[str, object]] = [
        {
            "results": [_row("A1", 9.0), _row("A2", 8.0)],
            "page_metadata": {
                "hasNext": True,
                "last_record_unique_id": 100,
                "last_record_sort_value": "8",
            },
        },
        {
            "results": [_row("A3", 7.0)],
            "page_metadata": {
                "hasNext": False,
                "last_record_unique_id": None,
                "last_record_sort_value": None,
            },
        },
    ]
    client = _FakeClient(pages)
    written, completed, notes = fetch_raw_records(
        out_dir=tmp_path,
        since="2022-10-01",
        until="2026-06-21",
        min_amount=250000.0,
        award_type_groups={"contracts": ("A", "B", "C", "D")},
        client=client,
    )
    assert written == 3
    assert "contracts" in completed
    assert notes == []
    raw = (tmp_path / RAW_RECORDS_FILENAME).read_text().strip().splitlines()
    assert len(raw) == 3
    # The second page must have been requested with the prior page's cursor.
    state = json.loads((tmp_path / FETCH_STATE_FILENAME).read_text())
    assert state["rows_written"] == 3
    assert state["min_amount"] == 250000.0


def test_fetch_raw_records_resumes_completed_family(tmp_path: Path) -> None:
    pages: list[dict[str, object]] = [
        {
            "results": [_row("A1", 9.0)],
            "page_metadata": {"hasNext": False, "last_record_unique_id": None},
        }
    ]
    fetch_raw_records(
        out_dir=tmp_path,
        since="2022-10-01",
        until="2026-06-21",
        min_amount=0.0,
        award_type_groups={"contracts": ("A",)},
        client=_FakeClient(pages),
    )
    # A second run with the family already complete fetches nothing more.
    second = _FakeClient([{"results": [_row("A2", 1.0)], "page_metadata": {"hasNext": False}}])
    written, completed, _notes = fetch_raw_records(
        out_dir=tmp_path,
        since="2022-10-01",
        until="2026-06-21",
        min_amount=0.0,
        award_type_groups={"contracts": ("A",)},
        client=second,
    )
    assert written == 0
    assert second.calls == 0
    assert "contracts" in completed
    assert len((tmp_path / RAW_RECORDS_FILENAME).read_text().strip().splitlines()) == 1


def test_sum_award_dollars_is_cents_safe() -> None:
    records = [
        _award(total_obligation=1000000.10),
        _award(generated_internal_id="X2", total_obligation=2000000.20),
    ]
    _rows, edges, _scanned, _skipped = build_award_graph(records, first_observed_at=_OBSERVED)
    # Two awards, same recipient/agency pair -> two federal_award edges.
    assert _sum_award_dollars(edges) == "3000000.30"
