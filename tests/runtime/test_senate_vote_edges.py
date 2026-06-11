from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.graph.bills import BillRef
from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput
from src.graph.export import write_contract_corpus
from src.runtime.senate_vote_edges import (
    build_lis_resolver,
    edges_for_rollcall,
    export_senate_vote_edges,
)

_OBS = datetime(2024, 6, 1, tzinfo=UTC)
_AS_OF = datetime(2024, 6, 2, tzinfo=UTC)
_KNOWN = datetime(2013, 1, 1, tzinfo=UTC)

_SRES15 = BillRef(jurisdiction_id="us-congress", session_id="113", identifier="sres-15")


def _anchor() -> ContractSourceAnchor:
    return ContractSourceAnchor(
        source_system="house_clerk",
        record_id="r",
        source_url="https://x",
        content_sha256="a" * 64,
        content_address="sha256/aa/aa/" + "a" * 64,
        known_at=_KNOWN,
        valid_from=_KNOWN.date(),
    )


def _person(cid: str, name: str, external_ids: list[str]) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="person",
        display_name=name,
        external_ids=external_ids,
        known_at=_KNOWN,
        source_anchors=[_anchor()],
    )


def _bill(cid: str) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="bill",
        display_name="A Bill",
        external_ids=[],
        known_at=_KNOWN,
        source_anchors=[_anchor()],
    )


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "contract_records"
    write_contract_corpus(
        [
            _person("ce-1", "Alpha Senator", ["bioguide:a000001", "lis:s289"]),
            _person("ce-2", "Beta Senator", ["bioguide:b000002", "lis:s340"]),
            _person("ce-3", "No Lis", ["bioguide:c000003"]),
            _bill("cb-x"),  # non-person rows are skipped by the resolver scan
        ],
        directory=corpus,
        as_of=_AS_OF,
    )
    return corpus


def _rollcall(vote_id: str = "senate-113-1-1") -> dict:
    return {
        "bill_id": "us_congress:113:sres-15",
        "congress": 113,
        "date": "2013-01-24",
        "sectors": [],
        "vote_id": vote_id,
        "votes": [
            ["S289", "R", "TN", "yea"],
            ["S340", "R", "NH", "nay"],
            ["S999", "D", "CA", "yea"],  # not in corpus -> unresolved
            ["S289x", "D", "CA", "???"],  # bad member AND bad choice
        ],
    }


def _rich_file(tmp_path: Path, rollcalls: list[dict]) -> Path:
    path = tmp_path / "rich.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rollcalls) + "\n", encoding="utf-8")
    return path


def test_build_lis_resolver(tmp_path: Path) -> None:
    resolver = build_lis_resolver(_corpus(tmp_path))
    assert resolver == {"S289": "ce-1", "S340": "ce-2"}
    assert build_lis_resolver(tmp_path / "absent") == {}


def test_edges_for_rollcall_resolves_and_counts(tmp_path: Path) -> None:
    resolver = build_lis_resolver(_corpus(tmp_path))
    result = edges_for_rollcall(_rollcall(), resolve_lis=resolver, first_observed_at=_OBS)
    assert result is not None
    assert len(result.edges) == 2
    assert result.members_unresolved == 2  # S999 unknown + S289x unknown
    edge = result.edges[0]
    assert edge.edge_type == "vote"
    assert edge.src_id == "ce-1"
    assert edge.dst_id == _SRES15.canonical_id  # same BillRef scheme as the corpus
    assert edge.attributes["choice"] == "yea"
    assert edge.external_key == "senate-113-1-1"
    assert edge.provenance.source_url.endswith("/vote1131/vote_113_1_00001.xml")
    assert edge.provenance.known_at.date().isoformat() == "2013-01-24"


def test_edges_for_rollcall_bad_choice_counts_unresolved(tmp_path: Path) -> None:
    resolver = {"S289X": "ce-9"}  # resolves, but the choice '???' is invalid
    record = _rollcall()
    record["votes"] = [["S289x", "D", "CA", "???"]]
    result = edges_for_rollcall(record, resolve_lis=resolver, first_observed_at=_OBS)
    assert result is not None
    assert result.edges == () and result.members_unresolved == 1


def test_edges_for_rollcall_malformed_bill_returns_none() -> None:
    for bad in ["nomination:PN123", "us_congress:abc:hr-1", "us_congress:113", ""]:
        record = _rollcall()
        record["bill_id"] = bad
        assert edges_for_rollcall(record, resolve_lis={}, first_observed_at=_OBS) is None


def test_edges_for_rollcall_odd_vote_id_gets_fallback_url(tmp_path: Path) -> None:
    resolver = build_lis_resolver(_corpus(tmp_path))
    record = _rollcall(vote_id="weird-id")
    result = edges_for_rollcall(record, resolve_lis=resolver, first_observed_at=_OBS)
    assert result is not None
    assert (
        result.edges[0].provenance.source_url == "https://www.senate.gov/legislative/votes_new.htm"
    )


def test_export_writes_feed_and_reports_per_congress(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    rich = _rich_file(
        tmp_path,
        [
            _rollcall("senate-113-1-1"),
            {**_rollcall("senate-114-1-7"), "congress": 114, "bill_id": "us_congress:114:hr-2"},
            {**_rollcall("senate-113-1-9"), "bill_id": "nomination:PN1"},  # skipped
        ],
    )
    out = tmp_path / "senate_vote_edges.jsonl"
    report = export_senate_vote_edges(
        rich_path=rich, corpus_directory=corpus, out_path=out, first_observed_at=_OBS
    )
    assert report.rollcalls_seen == 3 and report.rollcalls_converted == 2
    assert report.edges_written == 4  # 2 resolved members x 2 convertible roll-calls
    assert report.edges_per_congress == {113: 2, 114: 2}
    assert report.members_unresolved == 4
    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert all(r["edge_type"] == "vote" for r in rows)


def test_export_is_resumable_by_vote_id(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    rich = _rich_file(tmp_path, [_rollcall("senate-113-1-1")])
    out = tmp_path / "edges.jsonl"
    first = export_senate_vote_edges(
        rich_path=rich, corpus_directory=corpus, out_path=out, first_observed_at=_OBS
    )
    assert first.edges_written == 2
    second = export_senate_vote_edges(
        rich_path=rich, corpus_directory=corpus, out_path=out, first_observed_at=_OBS
    )
    assert second.edges_written == 0 and second.edges_total == 2


def test_export_respects_max_rollcalls(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    rich = _rich_file(tmp_path, [_rollcall("senate-113-1-1"), _rollcall("senate-113-1-2")])
    report = export_senate_vote_edges(
        rich_path=rich,
        corpus_directory=corpus,
        out_path=tmp_path / "edges.jsonl",
        first_observed_at=_OBS,
        max_rollcalls=1,
    )
    assert report.rollcalls_converted == 1 and report.edges_written == 2


def test_done_vote_ids_ignores_blank_and_keyless_lines(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    out = tmp_path / "edges.jsonl"
    out.write_text('\n{"external_key": null}\n{"external_key": "senate-113-1-1"}\n')
    rich = _rich_file(tmp_path, [_rollcall("senate-113-1-1"), _rollcall("senate-113-1-2")])
    report = export_senate_vote_edges(
        rich_path=rich, corpus_directory=corpus, out_path=out, first_observed_at=_OBS
    )
    assert report.rollcalls_converted == 1  # only the unseen roll-call


def test_export_default_first_observed_at(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    rich = _rich_file(tmp_path, [_rollcall()])
    report = export_senate_vote_edges(
        rich_path=rich, corpus_directory=corpus, out_path=tmp_path / "e.jsonl"
    )
    assert report.edges_written == 2  # observed defaulted to now(UTC)
