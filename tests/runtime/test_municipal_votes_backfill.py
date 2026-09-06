from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.graph.ingest.legistar_registry import LegistarClient
from src.runtime.municipal_votes_backfill import backfill_municipal_votes

_OBS = datetime(2026, 6, 11, tzinfo=UTC)
_REGISTRY = (
    LegistarClient("alpha", "city", "ca", "Alphaville"),
    LegistarClient("beta", "county", "tx", "Beta County"),
)

_PERSONS = [
    {"PersonId": 11, "PersonFullName": "Ada Alpha", "PersonActiveFlag": 1},
    {"PersonId": 12, "PersonFullName": "Bob Alpha", "PersonActiveFlag": 1},
    {"PersonId": 13, "PersonFullName": "Gone Member", "PersonActiveFlag": 0},  # inactive
    {"PersonId": 14, "PersonActiveFlag": 1},  # nameless -> skipped
]
_EVENTS = [{"EventId": 7, "EventDate": "2026-05-01T00:00:00"}]
_ITEMS = [
    {
        "EventItemId": 70,
        "EventItemMatterId": 900,
        "EventItemRollCallFlag": 1,
    },
    {"EventItemId": 71, "EventItemMatterId": None},  # no matter -> skipped
    {"EventItemId": 72, "EventItemMatterId": 901, "EventItemRollCallFlag": 0},  # no roll call
]
_MATTER = {
    "MatterId": 900,
    "MatterFile": "26-0042",
    "MatterName": "An ordinance",
    "MatterTypeName": "Ordinance",
    "MatterStatusName": "Passed",
    "MatterIntroDate": "2026-04-01T00:00:00",
}
_VOTES = [
    {"VoteId": 1, "VotePersonId": 11, "VoteValueName": "Aye"},
    {"VoteId": 2, "VotePersonId": 12, "VoteValueName": "No"},
    {"VoteId": 3, "VotePersonId": 99, "VoteValueName": "Aye"},  # unknown person
    {"VoteId": 4, "VotePersonId": 11, "VoteValueName": "Guest"},  # non-member value
]


def _noop(_seconds: float) -> None:
    return None


def _router(*, fail_clients: set[str] = frozenset()) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")  # v1/<client>/...
        code = parts[1]
        tail = parts[2:]
        if code in fail_clients:
            return httpx.Response(500)
        if code == "beta":
            # beta has members but no events with votes
            if tail == ["persons"]:
                return httpx.Response(200, json=[_PERSONS[0]])
            if tail == ["events"]:
                return httpx.Response(200, json=[])
            return httpx.Response(404)
        if tail == ["persons"]:
            return httpx.Response(200, json=_PERSONS)
        if tail == ["events"]:
            return httpx.Response(200, json=_EVENTS)
        if tail == ["events", "7", "eventitems"]:
            return httpx.Response(200, json=_ITEMS)
        if tail == ["matters", "900"]:
            return httpx.Response(200, json=_MATTER)
        if tail == ["eventitems", "70", "votes"]:
            return httpx.Response(200, json=_VOTES)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_backfill_writes_persons_and_edges(tmp_path: Path) -> None:
    edges = tmp_path / "edges.jsonl"
    persons = tmp_path / "persons.jsonl"
    report = backfill_municipal_votes(
        out_edges=edges,
        out_persons=persons,
        registry=_REGISTRY,
        client=_router(),
        first_observed_at=_OBS,
        sleep=_noop,
    )
    assert report.clients_processed == 2
    assert report.persons_written == 3  # 2 active alpha + 1 beta; inactive + nameless skipped
    assert report.edges_written == 2  # Ada aye + Bob no; unknown person + Guest dropped
    edge_rows = [json.loads(line) for line in edges.read_text().splitlines()]
    assert {e["attributes"]["choice"] for e in edge_rows} == {"yea", "nay"}
    assert all(e["edge_type"] == "vote" for e in edge_rows)
    assert all(e["external_key"].startswith("alpha:70:") for e in edge_rows)
    assert edge_rows[0]["provenance"]["known_at"].startswith("2026-05-01")  # event date
    person_rows = [json.loads(line) for line in persons.read_text().splitlines()]
    assert {p["display_name"] for p in person_rows} == {
        "Ada Alpha",
        "Bob Alpha",
    } or len(person_rows) == 3


def test_backfill_resumable_by_client(tmp_path: Path) -> None:
    edges = tmp_path / "edges.jsonl"
    persons = tmp_path / "persons.jsonl"
    first = backfill_municipal_votes(
        out_edges=edges,
        out_persons=persons,
        registry=_REGISTRY,
        client=_router(),
        first_observed_at=_OBS,
        sleep=_noop,
    )
    assert first.edges_written == 2
    again = backfill_municipal_votes(
        out_edges=edges,
        out_persons=persons,
        registry=_REGISTRY,
        client=_router(),
        first_observed_at=_OBS,
        sleep=_noop,
    )
    # alpha (has edges) is skipped; beta re-runs (it produced no edges)
    assert again.clients_skipped_done == 1
    assert again.edges_written == 0


def test_backfill_max_clients(tmp_path: Path) -> None:
    report = backfill_municipal_votes(
        out_edges=tmp_path / "e.jsonl",
        out_persons=tmp_path / "p.jsonl",
        registry=_REGISTRY,
        client=_router(),
        first_observed_at=_OBS,
        sleep=_noop,
        max_clients=1,
    )
    assert report.clients_processed == 1


def test_backfill_skips_failing_client(tmp_path: Path) -> None:
    report = backfill_municipal_votes(
        out_edges=tmp_path / "e.jsonl",
        out_persons=tmp_path / "p.jsonl",
        registry=_REGISTRY,
        client=_router(fail_clients={"alpha"}),
        first_observed_at=_OBS,
        sleep=_noop,
    )
    assert report.clients_processed == 2
    assert report.edges_written == 0  # alpha 500s at /persons; beta has no votes
    assert report.rows_skipped >= 1


def test_backfill_respects_vote_item_cap(tmp_path: Path) -> None:
    report = backfill_municipal_votes(
        out_edges=tmp_path / "e.jsonl",
        out_persons=tmp_path / "p.jsonl",
        registry=_REGISTRY[:1],
        client=_router(),
        first_observed_at=_OBS,
        sleep=_noop,
        max_vote_items_per_client=0,
    )
    assert report.edges_written == 0  # cap reached before any vote fetch


def test_backfill_default_client_and_now(monkeypatch, tmp_path: Path) -> None:
    import src.runtime.municipal_votes_backfill as mod

    router = _router()
    monkeypatch.setattr(mod.httpx, "Client", lambda **_kw: router)
    report = backfill_municipal_votes(
        out_edges=tmp_path / "e.jsonl",
        out_persons=tmp_path / "p.jsonl",
        registry=_REGISTRY[:1],
        sleep=_noop,
    )
    assert report.edges_written == 2


def test_backfill_tolerates_messy_data(tmp_path: Path) -> None:
    # gamma: bad event date + good event; failing matters + failing votes endpoints
    registry = (LegistarClient("gamma", "city", "ny", "Gammatown"),)

    def handler(request: httpx.Request) -> httpx.Response:
        tail = request.url.path.strip("/").split("/")[2:]
        if tail == ["persons"]:
            return httpx.Response(200, json=_PERSONS[:1])
        if tail == ["events"]:
            return httpx.Response(
                200,
                json=[
                    {"EventId": 1, "EventDate": "not-a-date"},  # skipped
                    {"EventId": None, "EventDate": "2026-05-01T00:00:00"},  # skipped
                    {"EventId": 2, "EventDate": "2026-05-01T00:00:00"},
                    {"EventId": 3, "EventDate": "2026-05-02T00:00:00"},
                ],
            )
        if tail == ["events", "2", "eventitems"]:
            return httpx.Response(
                200,
                json=[
                    {"EventItemId": 20, "EventItemMatterId": 800, "EventItemRollCallFlag": 1},
                    {"EventItemId": 21, "EventItemMatterId": 801, "EventItemRollCallFlag": 1},
                ],
            )
        if tail == ["events", "3", "eventitems"]:
            return httpx.Response(500)  # items fetch fails
        if tail == ["matters", "800"]:
            return httpx.Response(500)  # matter fetch fails
        if tail == ["matters", "801"]:
            return httpx.Response(200, json={"MatterId": 801, "MatterFile": "26-1"})
        if tail == ["eventitems", "21", "votes"]:
            return httpx.Response(500)  # votes fetch fails
        return httpx.Response(404)

    report = backfill_municipal_votes(
        out_edges=tmp_path / "e.jsonl",
        out_persons=tmp_path / "p.jsonl",
        registry=registry,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        first_observed_at=_OBS,
        sleep=_noop,
    )
    assert report.edges_written == 0
    assert report.rows_skipped >= 5  # bad date, bad id, items 500, matter 500, votes 500


def test_done_clients_tolerates_blank_and_keyless_lines(tmp_path: Path) -> None:
    edges = tmp_path / "edges.jsonl"
    edges.write_text('\n{"external_key": null}\n{"external_key": "alpha:70:1"}\n')
    report = backfill_municipal_votes(
        out_edges=edges,
        out_persons=tmp_path / "p.jsonl",
        registry=_REGISTRY,
        client=_router(),
        first_observed_at=_OBS,
        sleep=_noop,
    )
    assert report.clients_skipped_done == 1  # alpha marked done by the real key


def test_resolver_entries_skips_non_legistar_ids() -> None:
    from dataclasses import dataclass, field

    from src.runtime.municipal_votes_backfill import _resolver_entries

    @dataclass
    class _Node:
        canonical_id: str
        external_ids: list = field(default_factory=list)

    nodes = [_Node("ce-1", ["legistar:alpha:11", "bioguide:x000001"])]
    assert _resolver_entries(nodes) == [("alpha:11", "ce-1")]


def test_backfill_skips_client_when_events_fail(tmp_path: Path) -> None:
    registry = (LegistarClient("delta", "city", "oh", "Deltaville"),)

    def handler(request: httpx.Request) -> httpx.Response:
        tail = request.url.path.strip("/").split("/")[2:]
        if tail == ["persons"]:
            return httpx.Response(200, json=_PERSONS[:1])
        return httpx.Response(500)  # events fetch fails

    report = backfill_municipal_votes(
        out_edges=tmp_path / "e.jsonl",
        out_persons=tmp_path / "p.jsonl",
        registry=registry,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        first_observed_at=_OBS,
        sleep=_noop,
    )
    assert report.persons_written == 1 and report.edges_written == 0
    assert report.rows_skipped == 1


def test_backfill_cap_breaks_mid_items(tmp_path: Path) -> None:
    registry = (LegistarClient("epsilon", "city", "wa", "Epsilon"),)

    def handler(request: httpx.Request) -> httpx.Response:
        tail = request.url.path.strip("/").split("/")[2:]
        if tail == ["persons"]:
            return httpx.Response(200, json=_PERSONS[:2])
        if tail == ["events"]:
            return httpx.Response(200, json=_EVENTS)
        if tail == ["events", "7", "eventitems"]:
            return httpx.Response(
                200,
                json=[
                    {"EventItemId": 70, "EventItemMatterId": 900, "EventItemRollCallFlag": 1},
                    {"EventItemId": 73, "EventItemMatterId": 900, "EventItemRollCallFlag": 1},
                ],
            )
        if tail == ["matters", "900"]:
            return httpx.Response(200, json=_MATTER)
        if tail == ["eventitems", "70", "votes"]:
            return httpx.Response(200, json=_VOTES[:1])
        return httpx.Response(404)

    report = backfill_municipal_votes(
        out_edges=tmp_path / "e.jsonl",
        out_persons=tmp_path / "p.jsonl",
        registry=registry,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        first_observed_at=_OBS,
        sleep=_noop,
        max_vote_items_per_client=1,
    )
    assert report.edges_written == 1  # second roll-call item hit the cap mid-loop


def test_matter_lookup_is_cached_per_client(tmp_path: Path) -> None:
    registry = (LegistarClient("zeta", "city", "or", "Zetaville"),)
    matter_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        tail = request.url.path.strip("/").split("/")[2:]
        if tail == ["persons"]:
            return httpx.Response(200, json=_PERSONS[:2])
        if tail == ["events"]:
            return httpx.Response(200, json=_EVENTS)
        if tail == ["events", "7", "eventitems"]:
            return httpx.Response(
                200,
                json=[
                    {"EventItemId": 70, "EventItemMatterId": 900, "EventItemRollCallFlag": 1},
                    {"EventItemId": 73, "EventItemMatterId": 900, "EventItemRollCallFlag": 1},
                ],
            )
        if tail == ["matters", "900"]:
            matter_calls["n"] += 1
            return httpx.Response(200, json=_MATTER)
        if tail == ["eventitems", "70", "votes"]:
            return httpx.Response(200, json=_VOTES[:1])
        if tail == ["eventitems", "73", "votes"]:
            return httpx.Response(200, json=_VOTES[1:2])
        return httpx.Response(404)

    report = backfill_municipal_votes(
        out_edges=tmp_path / "e.jsonl",
        out_persons=tmp_path / "p.jsonl",
        registry=registry,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        first_observed_at=_OBS,
        sleep=_noop,
    )
    assert report.edges_written == 2
    assert matter_calls["n"] == 1  # second item reused the cached bill id
