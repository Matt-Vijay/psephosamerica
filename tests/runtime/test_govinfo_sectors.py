from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.graph.ingest.govinfo_billstatus import canonical_bill_id, parse_billstatus_xml
from src.runtime.govinfo_sectors import (
    bill_sector_record,
    build_sector_sidecar_batch,
    existing_sector_ids,
)


def _xml(number: int, policy: str = "Energy", subjects: tuple[str, ...] = ("Oil and gas",)) -> str:
    subj = "".join(f"<item><name>{s}</name></item>" for s in subjects)
    return (
        f"<billStatus><bill><congress>118</congress><type>HR</type><number>{number}</number>"
        f"<title>Act {number}</title><policyArea><name>{policy}</name></policyArea>"
        f"<subjects><legislativeSubjects>{subj}</legislativeSubjects></subjects></bill></billStatus>"
    )


def _listing(numbers: list[int]) -> str:
    base = "https://www.govinfo.gov/bulkdata/BILLSTATUS/118/hr"
    return json.dumps(
        {
            "files": [
                {"name": f"BILLSTATUS-118hr{n}.xml", "link": f"{base}/BILLSTATUS-118hr{n}.xml"}
                for n in numbers
            ]
        }
    )


def _handler(numbers: list[int]):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/json/" in request.url.path:
            return httpx.Response(200, text=_listing(numbers))
        n = int(request.url.path.split("118hr")[1].split(".")[0])
        return httpx.Response(200, text=_xml(n))

    return handler


def _client(numbers: list[int]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler(numbers)))


def test_bill_sector_record_shape() -> None:
    rec = bill_sector_record(_xml(1, policy="Health", subjects=("Medicare", "Hospitals")))
    assert rec["policy_area"] == "Health"
    assert rec["subjects"] == ["Medicare", "Hospitals"]
    assert rec["canonical_id"] == canonical_bill_id(parse_billstatus_xml(_xml(1)))
    # the dense embed text carries title + policy area + subjects
    text = rec["text"]
    assert isinstance(text, str)
    assert "Act 1" in text and "Health" in text and "Medicare" in text


def test_sidecar_keyed_by_vote_linkage_id(tmp_path: Path) -> None:
    path = tmp_path / "bill_sectors.jsonl"
    build_sector_sidecar_batch([118], path=path, bill_types=["hr"], client=_client([1, 2]))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    ids = {r["canonical_id"] for r in rows}
    assert ids == {canonical_bill_id(parse_billstatus_xml(_xml(n))) for n in (1, 2)}


def test_sidecar_is_resumable(tmp_path: Path) -> None:
    path = tmp_path / "bill_sectors.jsonl"
    first = build_sector_sidecar_batch(
        [118], path=path, bill_types=["hr"], client=_client([1, 2, 3]), max_fetch=2
    )
    assert first.written_new == 2 and first.remaining_after == 1
    second = build_sector_sidecar_batch(
        [118], path=path, bill_types=["hr"], client=_client([1, 2, 3]), max_fetch=2
    )
    assert second.existing_before == 2
    assert second.written_new == 1 and second.remaining_after == 0
    assert second.sidecar_rows == 3
    assert existing_sector_ids(path) == {
        canonical_bill_id(parse_billstatus_xml(_xml(n))) for n in (1, 2, 3)
    }
    # third pass: nothing new
    third = build_sector_sidecar_batch(
        [118], path=path, bill_types=["hr"], client=_client([1, 2, 3])
    )
    assert third.written_new == 0 and third.skipped == 0


def test_sidecar_skips_bad_files(tmp_path: Path) -> None:
    path = tmp_path / "bill_sectors.jsonl"

    def handler(request: httpx.Request) -> httpx.Response:
        if "/json/" in request.url.path:
            return httpx.Response(200, text=_listing([1, 2]))
        if request.url.path.endswith("118hr1.xml"):
            return httpx.Response(503)
        return httpx.Response(200, text=_xml(2))

    progress = build_sector_sidecar_batch(
        [118],
        path=path,
        bill_types=["hr"],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert progress.written_new == 1 and progress.skipped == 1


def test_existing_sector_ids_empty_when_absent(tmp_path: Path) -> None:
    assert existing_sector_ids(tmp_path / "nope.jsonl") == set()


def test_existing_sector_ids_ignores_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text('{"canonical_id": "cb-1"}\n\n{"canonical_id": "cb-2"}\n', encoding="utf-8")
    assert existing_sector_ids(path) == {"cb-1", "cb-2"}


def _patch_default_client(monkeypatch: pytest.MonkeyPatch, numbers: list[int]) -> None:
    import src.runtime.govinfo_sectors as mod

    real = httpx.Client
    monkeypatch.setattr(
        mod.httpx, "Client", lambda **_kw: real(transport=httpx.MockTransport(_handler(numbers)))
    )


def test_default_client_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_default_client(monkeypatch, [1])
    progress = build_sector_sidecar_batch([118], path=tmp_path / "s.jsonl", bill_types=["hr"])
    assert progress.written_new == 1
