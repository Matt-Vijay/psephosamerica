from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.graph.contracts import ContractSourceAnchor, EntityResolutionOutput
from src.graph.export import write_contract_corpus
from src.runtime.gdelt_backfill import (
    backfill_gdelt_mentions,
    federal_members,
    fetch_member_articles,
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


def _person(cid: str, name: str, external_ids: list[str]) -> EntityResolutionOutput:
    return EntityResolutionOutput(
        canonical_id=cid,
        entity_type="person",
        display_name=name,
        external_ids=external_ids,
        known_at=datetime(2023, 1, 1, tzinfo=UTC),
        source_anchors=[_anchor()],
    )


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "contract_records"
    write_contract_corpus(
        [
            _person("cp-1", "Chuck Schumer", ["bioguide:S000148"]),
            _person("cp-2", "Nancy Pelosi", ["bioguide:P000197"]),
            _person("cp-state", "State Legislator", ["openstates:x"]),  # no bioguide -> excluded
        ],
        directory=corpus,
        as_of=_OBS,
    )
    return corpus


def _articles(n: int) -> str:
    arts = [
        {
            "url": f"https://news{i}.com/a",
            "title": f"T{i}",
            "domain": f"news{i}.com",
            "seendate": "20230301T120000Z",
        }
        for i in range(n)
    ]
    return json.dumps({"articles": arts})


def _client(per_member: int) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_articles(per_member))

    return httpx.Client(transport=httpx.MockTransport(handler))


def _noop_sleep(_seconds: float) -> None:
    return None


def test_federal_members_filters_to_bioguide(tmp_path: Path) -> None:
    members = federal_members(_corpus(tmp_path))
    assert {m.canonical_id for m in members} == {"cp-1", "cp-2"}  # state legislator excluded
    assert federal_members(tmp_path / "absent") == []


def test_fetch_member_articles_parses_list() -> None:
    arts = fetch_member_articles("Chuck Schumer", client=_client(3))
    assert len(arts) == 3 and arts[0]["domain"] == "news0.com"


def test_fetch_member_articles_tolerates_non_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"articles": "oops"}))

    assert (
        fetch_member_articles("X", client=httpx.Client(transport=httpx.MockTransport(handler)))
        == []
    )


def test_backfill_writes_news_mention_edges(tmp_path: Path) -> None:
    out = tmp_path / "gdelt_edges.jsonl"
    progress = backfill_gdelt_mentions(
        corpus_directory=_corpus(tmp_path),
        out_path=out,
        client=_client(2),
        first_observed_at=_OBS,
        sleep=_noop_sleep,
    )
    assert progress.members_queried == 2  # two federal members
    assert progress.edges_written == 4  # 2 articles each
    edge = json.loads(out.read_text().splitlines()[0])
    assert edge["edge_type"] == "news_mention"
    assert edge["src_id"] in {"cp-1", "cp-2"}
    assert edge["dst_id"].startswith("outlet:")


def test_backfill_is_resumable(tmp_path: Path) -> None:
    out = tmp_path / "gdelt_edges.jsonl"
    corpus = _corpus(tmp_path)
    first = backfill_gdelt_mentions(
        corpus_directory=corpus,
        out_path=out,
        client=_client(1),
        first_observed_at=_OBS,
        sleep=_noop_sleep,
    )
    assert first.edges_written == 2
    second = backfill_gdelt_mentions(
        corpus_directory=corpus,
        out_path=out,
        client=_client(1),
        first_observed_at=_OBS,
        sleep=_noop_sleep,
    )
    assert second.edges_written == 0 and second.edges_total == 2


def test_done_members_ignores_blank_lines(tmp_path: Path) -> None:
    out = tmp_path / "gdelt_edges.jsonl"
    # blank line precedes a real edge for cp-1 -> cp-1 is "done" and skipped
    out.write_text('\n{"src_id": "cp-1", "edge_type": "news_mention"}\n', encoding="utf-8")
    progress = backfill_gdelt_mentions(
        corpus_directory=_corpus(tmp_path),
        out_path=out,
        client=_client(1),
        first_observed_at=_OBS,
        sleep=_noop_sleep,
    )
    assert progress.members_queried == 1  # only cp-2 (cp-1 already done)


def test_backfill_respects_max_members(tmp_path: Path) -> None:
    out = tmp_path / "gdelt_edges.jsonl"
    progress = backfill_gdelt_mentions(
        corpus_directory=_corpus(tmp_path),
        out_path=out,
        client=_client(1),
        first_observed_at=_OBS,
        sleep=_noop_sleep,
        max_members=1,
    )
    assert progress.members_queried == 1 and progress.edges_written == 1


def test_backfill_skips_query_error_and_bad_article(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)  # first member's query fails
        # second member: one good + one url-less (bad) article
        return httpx.Response(
            200,
            text=json.dumps(
                {
                    "articles": [
                        {
                            "url": "https://ok.com/a",
                            "domain": "ok.com",
                            "seendate": "20230301T120000Z",
                        },
                        {"url": ""},
                    ]
                }
            ),
        )

    progress = backfill_gdelt_mentions(
        corpus_directory=_corpus(tmp_path),
        out_path=tmp_path / "e.jsonl",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        first_observed_at=_OBS,
        sleep=_noop_sleep,
    )
    assert progress.members_skipped == 1  # the 500
    assert progress.edges_written == 1  # the url-less article dropped


def test_backfill_sleeps_between_members(tmp_path: Path) -> None:
    delays: list[float] = []
    backfill_gdelt_mentions(
        corpus_directory=_corpus(tmp_path),
        out_path=tmp_path / "e.jsonl",
        client=_client(1),
        first_observed_at=_OBS,
        sleep=delays.append,
        delay_seconds=5.0,
    )
    assert delays == [5.0]  # slept once (between the 1st and 2nd member)


def test_backfill_default_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import src.runtime.gdelt_backfill as mod

    real = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_articles(1))

    monkeypatch.setattr(
        mod.httpx, "Client", lambda **_kw: real(transport=httpx.MockTransport(handler))
    )
    progress = backfill_gdelt_mentions(
        corpus_directory=_corpus(tmp_path),
        out_path=tmp_path / "e.jsonl",
        first_observed_at=_OBS,
        sleep=_noop_sleep,
    )
    assert progress.edges_written == 2
