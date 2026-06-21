from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.graph.bills import BillRef
from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput
from src.graph.export import write_contract_corpus
from src.runtime.house_vote_edges import (
    backfill_house_vote_edges,
    congress_for_year,
    edges_for_house_rollcall,
    session_for_year,
    years_for_congress,
)

_OBS = datetime(2024, 6, 1, tzinfo=UTC)

_ROLL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rollcall-vote>
<vote-metadata>
<congress>118</congress>
<session>2nd</session>
<rollcall-num>17</rollcall-num>
<legis-num>H R 1234</legis-num>
<vote-question>On Passage</vote-question>
<vote-result>Passed</vote-result>
<action-date date="2024-02-01">1-Feb-2024</action-date>
</vote-metadata>
<vote-data>
<recorded-vote><legislator name-id="P000197">Pelosi</legislator><vote>Yea</vote></recorded-vote>
<recorded-vote><legislator name-id="Z999999">Ghost</legislator><vote>Nay</vote></recorded-vote>
</vote-data>
</rollcall-vote>"""

_QUORUM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rollcall-vote>
<vote-metadata>
<congress>118</congress>
<session>2nd</session>
<rollcall-num>1</rollcall-num>
<legis-num>QUORUM</legis-num>
<vote-question>Call of the House</vote-question>
<action-date date="2024-01-09">9-Jan-2024</action-date>
</vote-metadata>
<vote-data>
<recorded-vote><legislator name-id="P000197">Pelosi</legislator><vote>Present</vote></recorded-vote>
</vote-data>
</rollcall-vote>"""


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


def _corpus(tmp_path: Path) -> Path:
    person = EntityResolutionOutput(
        canonical_id="cp-pelosi",
        entity_type="person",
        display_name="Pelosi",
        external_ids=["bioguide:P000197"],
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        source_anchors=[_anchor()],
    )
    corpus = tmp_path / "contract_records"
    write_contract_corpus([person], directory=corpus, as_of=_OBS)
    return corpus


def test_year_congress_session_helpers() -> None:
    assert years_for_congress(113) == (2013, 2014)
    assert years_for_congress(119) == (2025, 2026)
    assert congress_for_year(2013) == 113
    assert congress_for_year(2024) == 118
    assert congress_for_year(2026) == 119
    assert session_for_year(2013) == 1
    assert session_for_year(2024) == 2


def test_edges_for_rollcall_resolves_bill_and_members(tmp_path: Path) -> None:
    resolver = {"P000197": "cp-pelosi"}
    result = edges_for_house_rollcall(_ROLL_XML, resolve_bioguide=resolver, first_observed_at=_OBS)
    assert result is not None
    assert result.had_bill is True
    assert result.members_unresolved == 1  # Z999999 not in resolver
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.edge_type == "vote"
    assert edge.src_id == "cp-pelosi"
    assert edge.dst_id == BillRef.for_congress(118, "H R 1234").canonical_id
    assert edge.attributes["choice"] == "yea"
    assert edge.external_key == "house-118-2-17"


def test_procedural_vote_emits_no_edge() -> None:
    assert (
        edges_for_house_rollcall(_QUORUM_XML, resolve_bioguide={}, first_observed_at=_OBS) is None
    )


def _handler(rolls: dict[tuple[int, int], str]):
    def handler(request: httpx.Request) -> httpx.Response:
        # .../evs/<year>/roll<NNN>.xml
        parts = request.url.path.split("/")
        year = int(parts[-2])
        roll = int(parts[-1].removeprefix("roll").removesuffix(".xml"))
        body = rolls.get((year, roll))
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body)

    return handler


def test_backfill_writes_edges_and_is_resumable(tmp_path: Path) -> None:
    out = tmp_path / "house_vote_edges.jsonl"
    corpus = _corpus(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(_handler({(2024, 17): _ROLL_XML})))
    report = backfill_house_vote_edges(
        years=[2024],
        corpus_directory=corpus,
        out_path=out,
        client=client,
        first_observed_at=_OBS,
        stop_after_misses=20,
    )
    assert report.edges_written == 1
    assert report.edges_per_congress == {118: 1}
    assert report.edges_total == 1
    edge = json.loads(out.read_text().splitlines()[0])
    assert edge["external_key"] == "house-118-2-17"

    # resume: the roll is already in the feed -> nothing new
    client2 = httpx.Client(transport=httpx.MockTransport(_handler({(2024, 17): _ROLL_XML})))
    again = backfill_house_vote_edges(
        years=[2024],
        corpus_directory=corpus,
        out_path=out,
        client=client2,
        first_observed_at=_OBS,
        stop_after_misses=20,
    )
    assert again.edges_written == 0
    assert again.edges_total == 1


def test_backfill_respects_max_rollcalls(tmp_path: Path) -> None:
    out = tmp_path / "house_vote_edges.jsonl"
    rolls = {
        (2024, n): _ROLL_XML.replace(
            "<rollcall-num>17</rollcall-num>", f"<rollcall-num>{n}</rollcall-num>"
        )
        for n in range(1, 6)
    }
    client = httpx.Client(transport=httpx.MockTransport(_handler(rolls)))
    report = backfill_house_vote_edges(
        years=[2024],
        corpus_directory=_corpus(tmp_path),
        out_path=out,
        client=client,
        first_observed_at=_OBS,
        max_rollcalls=2,
    )
    assert report.rollcalls_converted == 2
