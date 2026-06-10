from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.graph.ingest.senate import bill_canonical_id_for, parse_senate_rollcall_xml
from src.runtime.senate_votes_backfill import (
    backfill_senate_votes,
    load_bill_sectors,
    parse_menu_vote_numbers,
    senate_menu_url,
    senate_rich_record,
    senate_vote_url,
)


def _menu(numbers: list[int]) -> str:
    votes = "".join(f"<vote><vote_number>{n}</vote_number></vote>" for n in numbers)
    return f"<vote_summary><votes>{votes}</votes></vote_summary>"


def _vote_xml(number: int, *, document: str | None = "S. 47", choice: str = "Yea") -> str:
    doc = f"<document><document_name>{document}</document_name></document>" if document else ""
    return (
        f"<roll_call_vote><congress>118</congress><session>1</session>"
        f"<vote_number>{number}</vote_number><vote_date>March 1, 2023</vote_date>{doc}"
        f"<vote_result>Passed</vote_result><members>"
        f"<member><lis_member_id>S001</lis_member_id><first_name>A</first_name>"
        f"<last_name>B</last_name><state>CA</state><party>D</party>"
        f"<vote_cast>{choice}</vote_cast></member></members></roll_call_vote>"
    )


_CB_S47 = bill_canonical_id_for(parse_senate_rollcall_xml(_vote_xml(10)))


def _handler(numbers: list[int]):
    def handler(request: httpx.Request) -> httpx.Response:
        if "vote_menu" in request.url.path:
            return httpx.Response(200, text=_menu(numbers))
        n = int(request.url.path.split("_")[-1].split(".")[0])
        return httpx.Response(200, text=_vote_xml(n))

    return handler


def _client(numbers: list[int]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler(numbers)))


def test_url_builders() -> None:
    assert senate_menu_url(118, 1).endswith("/roll_call_lists/vote_menu_118_1.xml")
    assert senate_vote_url(118, 1, 10).endswith("/vote1181/vote_118_1_00010.xml")


def test_parse_menu_vote_numbers() -> None:
    assert parse_menu_vote_numbers(_menu([3, 1, 2, 1])) == [1, 2, 3]


def test_load_bill_sectors(tmp_path: Path) -> None:
    sidecar = tmp_path / "bill_content.jsonl"
    sidecar.write_text(
        json.dumps({"canonical_id": "cb-1", "policy_area": "Energy", "subjects": ["Oil"]})
        + "\n"
        # a bill with no policy area -> only its subjects
        + json.dumps({"canonical_id": "cb-2", "policy_area": None, "subjects": ["Defense"]})
        + "\n",
        encoding="utf-8",
    )
    assert load_bill_sectors(sidecar) == {"cb-1": ["Energy", "Oil"], "cb-2": ["Defense"]}
    assert load_bill_sectors(tmp_path / "absent") == {}


def test_rich_record_attaches_sectors_and_normalizes_choice() -> None:
    record = senate_rich_record(_vote_xml(10), sectors_by_bill={_CB_S47: ["Health", "Medicare"]})
    assert record["vote_id"] == "senate-118-1-10"
    assert record["bill_id"] == "us_congress:118:s-47"
    assert record["sectors"] == ["Health", "Medicare"]
    assert record["votes"] == [["S001", "D", "CA", "yea"]]  # lowercased
    assert record["date"] == "2023-03-01"


def test_rich_record_procedural_vote_has_unknown_bill() -> None:
    record = senate_rich_record(_vote_xml(11, document=None), sectors_by_bill={})
    assert record["bill_id"] == "us_congress:118:unknown"
    assert record["sectors"] == []


def test_rich_record_unparseable_document_is_unknown_bill() -> None:
    # A nomination ("PN 44") is not a congress bill type -> unknown bill.
    record = senate_rich_record(_vote_xml(12, document="PN 44"), sectors_by_bill={})
    assert record["bill_id"] == "us_congress:118:unknown"


def test_load_bill_sectors_ignores_blank_lines(tmp_path: Path) -> None:
    sidecar = tmp_path / "bill_content.jsonl"
    sidecar.write_text(
        json.dumps({"canonical_id": "cb-1", "policy_area": "Energy", "subjects": []}) + "\n\n",
        encoding="utf-8",
    )
    assert load_bill_sectors(sidecar) == {"cb-1": ["Energy"]}


def test_backfill_writes_and_is_resumable(tmp_path: Path) -> None:
    out = tmp_path / "senate.jsonl"
    sidecar = tmp_path / "bill_content.jsonl"
    sidecar.write_text(
        json.dumps({"canonical_id": _CB_S47, "policy_area": "Energy", "subjects": []}) + "\n",
        encoding="utf-8",
    )
    first = backfill_senate_votes(
        congresses=[118],
        out_path=out,
        content_sidecar=sidecar,
        sessions=[1],
        client=_client([1, 2, 3]),
    )
    assert first.votes_listed == 3 and first.votes_new == 3
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 3
    assert rows[0]["sectors"] == ["Energy"]  # sector joined from the sidecar

    # resume: same session -> nothing new (blank line in the feed exercises the
    # existing-vote-ids skip branch)
    with out.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    second = backfill_senate_votes(
        congresses=[118],
        out_path=out,
        content_sidecar=sidecar,
        sessions=[1],
        client=_client([1, 2, 3]),
    )
    assert second.votes_new == 0 and second.rows_total == 3


def test_backfill_respects_max_votes(tmp_path: Path) -> None:
    out = tmp_path / "senate.jsonl"
    progress = backfill_senate_votes(
        congresses=[118],
        out_path=out,
        content_sidecar=tmp_path / "absent.jsonl",
        sessions=[1],
        client=_client([1, 2, 3, 4, 5]),
        max_votes=2,
    )
    assert progress.votes_listed == 5 and progress.votes_new == 2


def test_backfill_skips_session_with_no_menu(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "vote_menu" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, text=_vote_xml(1))

    progress = backfill_senate_votes(
        congresses=[118],
        out_path=tmp_path / "s.jsonl",
        content_sidecar=tmp_path / "absent.jsonl",
        sessions=[1],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert progress.sessions == 0 and progress.votes_new == 0


def test_backfill_skips_bad_vote(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "vote_menu" in request.url.path:
            return httpx.Response(200, text=_menu([1, 2]))
        if request.url.path.endswith("00001.xml"):
            return httpx.Response(500)
        return httpx.Response(200, text=_vote_xml(2))

    progress = backfill_senate_votes(
        congresses=[118],
        out_path=tmp_path / "s.jsonl",
        content_sidecar=tmp_path / "absent.jsonl",
        sessions=[1],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert progress.votes_new == 1 and progress.votes_skipped == 1


def test_default_client_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import src.runtime.senate_votes_backfill as mod

    real = httpx.Client
    monkeypatch.setattr(
        mod.httpx, "Client", lambda **_kw: real(transport=httpx.MockTransport(_handler([1])))
    )
    progress = backfill_senate_votes(
        congresses=[118],
        out_path=tmp_path / "s.jsonl",
        content_sidecar=tmp_path / "a.jsonl",
        sessions=[1],
    )
    assert progress.votes_new == 1
