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
from src.graph.ingest.courtlistener_export import (
    COURT_EDGES_FILENAME,
    COURT_OPINIONS_FILENAME,
    DEFAULT_COURTS,
    FETCH_STATE_FILENAME,
    INGEST_META_FILENAME,
    RAW_OPINIONS_FILENAME,
    build_court_graph,
    fetch_opinions,
    opinion_node_row,
)

_OBSERVED = datetime(2026, 6, 21, tzinfo=UTC)

_COURTS = [
    {
        "id": "scotus",
        "full_name": "Supreme Court of the United States",
        "jurisdiction": "F",
        "start_date": "1789-09-24",
    }
]
_JUDGES = [
    {"id": 1, "name_first": "John", "name_last": "Roberts", "fjc_id": 1392},
    {"id": 2, "name_first": "Sonia", "name_last": "Sotomayor", "fjc_id": 2243},
]


def _opinion(cluster_id: int, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "cluster_id": cluster_id,
        "court_id": "scotus",
        "dateFiled": "2020-06-15",
        "caseName": f"Case {cluster_id}",
        "docketNumber": f"{cluster_id}-1",
        "citation": [f"{cluster_id} U.S. 1"],
        "judge": "X",
        "panel_ids": [1, 2],
        "opinions": [
            {
                "id": cluster_id * 10,
                "author_id": 1,
                "type": "lead",
                "joined_by_ids": [2],
                "cites": [],
            }
        ],
    }
    base.update(overrides)
    return base


def test_build_court_graph_resolves_and_emits_all_edge_types() -> None:
    opinions = [
        _opinion(
            100,
            caseName="A v. B 42 U.S.C. § 1983",
            opinions=[
                {"id": 5001, "author_id": 1, "type": "lead", "joined_by_ids": [2], "cites": [5002]}
            ],
        ),
        _opinion(
            101,
            dateFiled="2019-01-01",
            opinions=[
                {"id": 5002, "author_id": 2, "type": "lead", "joined_by_ids": [], "cites": []}
            ],
        ),
    ]
    rows, edges, ops, scanned, skipped = build_court_graph(
        opinion_records=opinions,
        court_records=_COURTS,
        judge_records=_JUDGES,
        first_observed_at=_OBSERVED,
    )
    assert scanned == 2 and skipped == 0
    assert len(ops) == 2
    # 1 court org + 2 judge persons, deduped across both opinions.
    assert len(rows) == 3
    types = {r.entity_type for r in rows}
    assert types == {"org", "person"}
    edge_types = {e.edge_type for e in edges}
    assert {
        "decided_by",
        "authored_opinion",
        "joined_opinion",
        "cites_opinion",
        "cites_statute",
    } <= edge_types
    # The cites_opinion edge connects cluster 100 -> cluster 101 (via opinion id 5002).
    cite = next(e for e in edges if e.edge_type == "cites_opinion")
    assert cite.src_id == "co-100" and cite.dst_id == "co-101"
    statute = next(e for e in edges if e.edge_type == "cites_statute")
    assert statute.attributes["identifier"] == "usc-42-1983"


def test_build_court_graph_skips_future_dated_opinion() -> None:
    # Filed after the observation instant -> dropped (leakage guard), not crashed.
    opinions = [_opinion(200, dateFiled="2099-01-01"), _opinion(201)]
    _rows, _edges, ops, scanned, skipped = build_court_graph(
        opinion_records=opinions,
        court_records=_COURTS,
        judge_records=_JUDGES,
        first_observed_at=_OBSERVED,
    )
    assert scanned == 2 and skipped == 1
    assert [o.cluster_id for o in ops] == [201]


def test_judge_dedupes_across_opinions() -> None:
    # Same judge (author_id 1) in three opinions -> one person row, three authored edges.
    opinions = [_opinion(c) for c in (300, 301, 302)]
    rows, edges, _ops, _s, _k = build_court_graph(
        opinion_records=opinions,
        court_records=_COURTS,
        judge_records=_JUDGES,
        first_observed_at=_OBSERVED,
    )
    persons = [r for r in rows if r.entity_type == "person"]
    assert len(persons) == 2  # Roberts + Sotomayor, each once
    authored = [e for e in edges if e.edge_type == "authored_opinion"]
    assert len(authored) == 3


def test_opinion_node_row_shape() -> None:
    opinions = [_opinion(400)]
    _rows, _edges, ops, _s, _k = build_court_graph(
        opinion_records=opinions,
        court_records=_COURTS,
        judge_records=_JUDGES,
        first_observed_at=_OBSERVED,
    )
    node = opinion_node_row(ops[0])
    assert node["canonical_id"] == "co-400"
    assert node["entity_type"] == "court_opinion"
    assert node["court_id"] == "scotus"
    assert node["date_filed"] == "2020-06-15"


def test_export_writes_corpus_edges_opinions_cdc(tmp_path: Path) -> None:
    opinions = [_opinion(500), _opinion(501)]
    rows, edges, ops, _s, _k = build_court_graph(
        opinion_records=opinions,
        court_records=_COURTS,
        judge_records=_JUDGES,
        first_observed_at=_OBSERVED,
    )
    write_contract_corpus(rows, directory=tmp_path, as_of=_OBSERVED)
    deltas = diff_outputs({}, {r.canonical_id: r for r in rows})
    write_delta_feed(deltas, path=tmp_path / DELTAS_FILENAME, append=False)
    with (tmp_path / COURT_EDGES_FILENAME).open("w", encoding="utf-8") as handle:
        for edge in sorted(edges, key=lambda e: e.edge_id):
            handle.write(edge.model_dump_json() + "\n")
    with (tmp_path / COURT_OPINIONS_FILENAME).open("w", encoding="utf-8") as handle:
        for opinion in ops:
            handle.write(json.dumps(opinion_node_row(opinion)) + "\n")

    assert (tmp_path / RECORDS_FILENAME).exists()
    assert (tmp_path / MANIFEST_FILENAME).exists()
    back = read_contract_corpus(tmp_path)
    assert len(back) == 3  # 1 court + 2 judges
    delta_lines = (tmp_path / DELTAS_FILENAME).read_text().strip().splitlines()
    assert len(delta_lines) == 3
    assert all(json.loads(line)["change_type"] == "created" for line in delta_lines)
    op_lines = (tmp_path / COURT_OPINIONS_FILENAME).read_text().strip().splitlines()
    assert len(op_lines) == 2
    assert all(json.loads(line)["entity_type"] == "court_opinion" for line in op_lines)


def test_default_courts_scope_covers_scotus_and_appeals() -> None:
    assert "scotus" in DEFAULT_COURTS
    assert "ca9" in DEFAULT_COURTS and "cadc" in DEFAULT_COURTS
    # A slice of district + state high courts is included.
    assert "nysd" in DEFAULT_COURTS
    assert "cal" in DEFAULT_COURTS


def test_filename_constants() -> None:
    assert COURT_EDGES_FILENAME == "court_edges.jsonl"
    assert COURT_OPINIONS_FILENAME == "court_opinions.jsonl"
    assert INGEST_META_FILENAME == "ingest_meta.json"


class _FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self._body = body

    def json(self) -> dict[str, object]:
        return self._body


class _FakeClient:
    """Replays scripted search pages by call order (one court)."""

    def __init__(self, pages: list[dict[str, object]]) -> None:
        self._pages = pages
        self.calls = 0

    def get(self, _url: str) -> _FakeResponse:
        idx = self.calls
        self.calls += 1
        if idx < len(self._pages):
            return _FakeResponse(self._pages[idx])
        return _FakeResponse({"results": [], "next": None})


def test_fetch_opinions_paginates_caps_and_checkpoints(tmp_path: Path) -> None:
    pages: list[dict[str, object]] = [
        {"results": [_opinion(1), _opinion(2)], "next": "PAGE2"},
        {"results": [_opinion(3)], "next": None},
    ]
    written, completed, notes = fetch_opinions(
        out_dir=tmp_path,
        courts=["scotus"],
        since="2015-01-01",
        max_opinions_per_court=100,
        client=_FakeClient(pages),
    )
    assert written == 3
    assert completed == ["scotus"]
    assert notes == []
    raw = (tmp_path / RAW_OPINIONS_FILENAME).read_text().strip().splitlines()
    assert len(raw) == 3
    state = json.loads((tmp_path / FETCH_STATE_FILENAME).read_text())
    assert state["opinions_written"] == 3
    assert "scotus" in state["opinions_completed"]


def test_fetch_opinions_respects_per_court_cap(tmp_path: Path) -> None:
    pages: list[dict[str, object]] = [
        {"results": [_opinion(1), _opinion(2), _opinion(3)], "next": "PAGE2"},
    ]
    written, _completed, _notes = fetch_opinions(
        out_dir=tmp_path,
        courts=["scotus"],
        since="2015-01-01",
        max_opinions_per_court=2,
        client=_FakeClient(pages),
    )
    assert written == 2  # capped mid-page
    raw = (tmp_path / RAW_OPINIONS_FILENAME).read_text().strip().splitlines()
    assert len(raw) == 2


def test_fetch_opinions_resumes_completed_court(tmp_path: Path) -> None:
    fetch_opinions(
        out_dir=tmp_path,
        courts=["scotus"],
        since="2015-01-01",
        max_opinions_per_court=100,
        client=_FakeClient([{"results": [_opinion(1)], "next": None}]),
    )
    second = _FakeClient([{"results": [_opinion(99)], "next": None}])
    written, completed, _notes = fetch_opinions(
        out_dir=tmp_path,
        courts=["scotus"],
        since="2015-01-01",
        max_opinions_per_court=100,
        client=second,
    )
    assert written == 0
    assert second.calls == 0
    assert "scotus" in completed
    assert len((tmp_path / RAW_OPINIONS_FILENAME).read_text().strip().splitlines()) == 1
