from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.graph.export import read_contract_corpus
from src.graph.ingest.govinfo_billstatus import canonical_bill_id, parse_billstatus_xml
from src.runtime.govinfo_bills_materialize import (
    billstatus_listing_url,
    fetch_billstatus_rows,
    list_billstatus_file_urls,
    materialize_govinfo_bill_corpus,
)


def _billstatus_xml(congress: int, bill_type: str, number: int, title: str) -> str:
    return (
        f"<billStatus><bill><congress>{congress}</congress><type>{bill_type.upper()}</type>"
        f"<number>{number}</number><title>{title}</title>"
        f"<introducedDate>2023-03-14</introducedDate>"
        f"<policyArea><name>Energy</name></policyArea></bill></billStatus>"
    )


def _listing(names: list[str]) -> str:
    base = "https://www.govinfo.gov/bulkdata/BILLSTATUS/118/hr"
    return json.dumps({"files": [{"name": n, "link": f"{base}/{n}"} for n in names]})


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_listing_url() -> None:
    assert (
        billstatus_listing_url(118, "hr")
        == "https://www.govinfo.gov/bulkdata/json/BILLSTATUS/118/hr"
    )


def test_list_filters_to_billstatus_xml_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_listing(
                ["BILLSTATUS-118hr1.xml", "BILLSTATUS-118hr2.xml", "index.xml", "README.txt"]
            ),
        )

    urls = list_billstatus_file_urls(118, "hr", client=_client(handler))
    assert urls == [
        "https://www.govinfo.gov/bulkdata/BILLSTATUS/118/hr/BILLSTATUS-118hr1.xml",
        "https://www.govinfo.gov/bulkdata/BILLSTATUS/118/hr/BILLSTATUS-118hr2.xml",
    ]


def test_fetch_rows_links_to_vote_canonical_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/json/" in path:
            return httpx.Response(
                200, text=_listing(["BILLSTATUS-118hr1.xml", "BILLSTATUS-118hr2.xml"])
            )
        number = 1 if path.endswith("118hr1.xml") else 2
        return httpx.Response(200, text=_billstatus_xml(118, "hr", number, f"Act {number}"))

    rows, report = fetch_billstatus_rows(
        118,
        bill_types=["hr"],
        client=_client(handler),
        first_observed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert report.parsed == 2 and report.fetched == 2 and report.skipped == 0
    ids = {row.canonical_id for row in rows}
    expected = {
        canonical_bill_id(parse_billstatus_xml(_billstatus_xml(118, "hr", n, "x"))) for n in (1, 2)
    }
    assert ids == expected
    assert all(row.entity_type == "bill" for row in rows)


def test_fetch_rows_respects_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/json/" in request.url.path:
            return httpx.Response(
                200, text=_listing([f"BILLSTATUS-118hr{n}.xml" for n in range(1, 6)])
            )
        number = int(request.url.path.split("118hr")[1].split(".")[0])
        return httpx.Response(200, text=_billstatus_xml(118, "hr", number, f"Act {number}"))

    rows, report = fetch_billstatus_rows(118, bill_types=["hr"], client=_client(handler), limit=2)
    assert report.listed == 5
    assert report.parsed == 2 and len(rows) == 2


def test_fetch_rows_skips_bad_files_without_crashing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/json/" in request.url.path:
            return httpx.Response(
                200, text=_listing(["BILLSTATUS-118hr1.xml", "BILLSTATUS-118hr2.xml"])
            )
        if request.url.path.endswith("118hr1.xml"):
            return httpx.Response(404)  # fetch error
        return httpx.Response(200, text="<billStatus><bill></bill></billStatus>")  # parse error

    rows, report = fetch_billstatus_rows(118, bill_types=["hr"], client=_client(handler))
    assert rows == []
    assert report.skipped == 2
    assert len(report.errors) == 2


def test_fetch_rows_default_observed_time_is_used() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/json/" in request.url.path:
            return httpx.Response(200, text=_listing(["BILLSTATUS-118hr1.xml"]))
        return httpx.Response(200, text=_billstatus_xml(118, "hr", 1, "Act 1"))

    rows, report = fetch_billstatus_rows(118, bill_types=["hr"], client=_client(handler))
    assert report.parsed == 1
    # known_at defaults to the introduced day regardless of observed time
    assert rows[0].known_at == datetime(2023, 3, 14, tzinfo=UTC)


@pytest.mark.parametrize("status_code", [500, 503])
def test_listing_raises_on_server_error(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    with pytest.raises(httpx.HTTPStatusError):
        list_billstatus_file_urls(118, "hr", client=_client(handler))


def _patch_default_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    # Make the default (client=None) construction use a MockTransport-backed
    # client, exercising the own-and-close path without real network.
    import src.runtime.govinfo_bills_materialize as mod

    real_client = httpx.Client  # capture before patching to avoid recursion
    monkeypatch.setattr(
        mod.httpx, "Client", lambda **_kw: real_client(transport=httpx.MockTransport(handler))
    )


def test_default_client_is_constructed_and_closed_for_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_listing(["BILLSTATUS-118hr1.xml"]))

    _patch_default_client(monkeypatch, handler)
    urls = list_billstatus_file_urls(118, "hr")  # client=None -> owns + closes
    assert urls and urls[0].endswith("BILLSTATUS-118hr1.xml")


def test_default_client_is_constructed_and_closed_for_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/json/" in request.url.path:
            return httpx.Response(200, text=_listing(["BILLSTATUS-118hr1.xml"]))
        return httpx.Response(200, text=_billstatus_xml(118, "hr", 1, "Act 1"))

    _patch_default_client(monkeypatch, handler)
    rows, report = fetch_billstatus_rows(118, bill_types=["hr"])  # client=None
    assert report.parsed == 1 and len(rows) == 1


def test_materialize_with_default_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_default_client(monkeypatch, _corpus_handler([1]))
    result = materialize_govinfo_bill_corpus(  # client=None -> owns + closes
        congresses=[118],
        directory=tmp_path,
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
        bill_types=["hr"],
        first_observed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert result.bill_count == 1


def _corpus_handler(numbers: list[int]):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/json/" in request.url.path:
            return httpx.Response(200, text=_listing([f"BILLSTATUS-118hr{n}.xml" for n in numbers]))
        number = int(request.url.path.split("118hr")[1].split(".")[0])
        return httpx.Response(200, text=_billstatus_xml(118, "hr", number, f"Act {number}"))

    return handler


def test_materialize_exports_dense_corpus_and_creates_deltas(tmp_path: Path) -> None:
    as_of = datetime(2025, 1, 1, tzinfo=UTC)
    result = materialize_govinfo_bill_corpus(
        congresses=[118],
        directory=tmp_path,
        as_of=as_of,
        bill_types=["hr"],
        client=_client(_corpus_handler([1, 2])),
        first_observed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert result.bill_count == 2
    assert result.delta_count == 2  # both created
    assert result.manifest.record_count == 2
    rows = read_contract_corpus(tmp_path)
    assert all(r.entity_type == "bill" for r in rows)
    assert all(r.enrichment_status == "ready" for r in rows)
    assert all(r.dossier_embedding and len(r.dossier_embedding) == 256 for r in rows)
    # distinct embeddings (no sector collapse)
    assert len({tuple(r.dossier_embedding) for r in rows}) == 2  # type: ignore[arg-type]


def test_materialize_is_idempotent_then_incremental(tmp_path: Path) -> None:
    as_of = datetime(2025, 1, 1, tzinfo=UTC)
    first = materialize_govinfo_bill_corpus(
        congresses=[118],
        directory=tmp_path,
        as_of=as_of,
        bill_types=["hr"],
        client=_client(_corpus_handler([1, 2])),
        first_observed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert first.delta_count == 2
    # same bills again -> no new deltas (idempotent CDC)
    again = materialize_govinfo_bill_corpus(
        congresses=[118],
        directory=tmp_path,
        as_of=as_of,
        bill_types=["hr"],
        client=_client(_corpus_handler([1, 2])),
        first_observed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert again.delta_count == 0
    # a third bill appears -> exactly one created delta, corpus grows to 3
    grown = materialize_govinfo_bill_corpus(
        congresses=[118],
        directory=tmp_path,
        as_of=as_of,
        bill_types=["hr"],
        client=_client(_corpus_handler([1, 2, 3])),
        first_observed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert grown.delta_count == 1
    assert grown.manifest.record_count == 3
    # the appended delta feed holds 2 (first run) + 0 + 1 = 3 lines
    feed = (tmp_path / "deltas.jsonl").read_text().strip().splitlines()
    assert len(feed) == 3
