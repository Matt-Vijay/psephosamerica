from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.graph.export import read_contract_corpus
from src.graph.ingest.govinfo_billstatus import canonical_bill_id_from_filename
from src.runtime.govinfo_bills_run import Candidate, collect_candidates, ingest_batch

_AS_OF = datetime(2025, 1, 1, tzinfo=UTC)
_OBS = datetime(2024, 1, 1, tzinfo=UTC)


def _billstatus_xml(number: int) -> str:
    return (
        f"<billStatus><bill><congress>118</congress><type>HR</type><number>{number}</number>"
        f"<title>Act {number}</title><introducedDate>2023-03-14</introducedDate>"
        f"<policyArea><name>Energy</name></policyArea></bill></billStatus>"
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
        return httpx.Response(200, text=_billstatus_xml(n))

    return handler


def _client(numbers: list[int]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler(numbers)))


def test_collect_candidates_derives_ids_without_fetching() -> None:
    cands = collect_candidates([118], bill_types=["hr"], client=_client([1, 2, 3]))
    assert [c.canonical_id for c in cands] == [
        canonical_bill_id_from_filename(f"BILLSTATUS-118hr{n}.xml") for n in (1, 2, 3)
    ]
    assert all(c.url.endswith(".xml") for c in cands)


def test_ingest_batch_checkpoints_and_resumes(tmp_path: Path) -> None:
    # First batch: max_fetch=2 of 3 candidates -> corpus has 2, 1 remaining.
    first = ingest_batch(
        congresses=[118],
        directory=tmp_path,
        as_of=_AS_OF,
        bill_types=["hr"],
        client=_client([1, 2, 3]),
        first_observed_at=_OBS,
        max_fetch=2,
    )
    assert first.candidates_total == 3
    assert first.parsed_new == 2 and first.remaining_after == 1
    assert first.corpus_rows == 2 and first.deltas_written == 2
    rows = read_contract_corpus(tmp_path)
    assert len(rows) == 2 and all(r.enrichment_status == "ready" for r in rows)

    # Second batch: resumes, skips the 2 already-ingested, fetches the last 1.
    second = ingest_batch(
        congresses=[118],
        directory=tmp_path,
        as_of=_AS_OF,
        bill_types=["hr"],
        client=_client([1, 2, 3]),
        first_observed_at=_OBS,
        max_fetch=2,
    )
    assert second.existing_before == 2
    assert second.parsed_new == 1 and second.remaining_after == 0
    assert second.corpus_rows == 3
    assert len({r.canonical_id for r in read_contract_corpus(tmp_path)}) == 3

    # Third batch: nothing new -> no fetch, no deltas, corpus unchanged.
    third = ingest_batch(
        congresses=[118],
        directory=tmp_path,
        as_of=_AS_OF,
        bill_types=["hr"],
        client=_client([1, 2, 3]),
        first_observed_at=_OBS,
        max_fetch=2,
    )
    assert third.fetched_new == 0 and third.deltas_written == 0 and third.corpus_rows == 3


def test_ingest_batch_distinct_embeddings_and_delta_feed(tmp_path: Path) -> None:
    ingest_batch(
        congresses=[118],
        directory=tmp_path,
        as_of=_AS_OF,
        bill_types=["hr"],
        client=_client([1, 2, 3]),
        first_observed_at=_OBS,
        max_fetch=10,
    )
    rows = read_contract_corpus(tmp_path)
    embs = {tuple(r.dossier_embedding) for r in rows if r.dossier_embedding}
    assert len(embs) == 3  # distinct, no collapse
    feed = (tmp_path / "deltas.jsonl").read_text().strip().splitlines()
    assert len(feed) == 3 and all(json.loads(line)["change_type"] == "created" for line in feed)


def test_ingest_batch_reuses_supplied_candidates(tmp_path: Path) -> None:
    cands = collect_candidates([118], bill_types=["hr"], client=_client([1, 2]))
    assert isinstance(cands[0], Candidate)
    progress = ingest_batch(
        congresses=[118],
        directory=tmp_path,
        as_of=_AS_OF,
        bill_types=["hr"],
        client=_client([1, 2]),
        first_observed_at=_OBS,
        candidates=cands,
    )
    assert progress.parsed_new == 2


def test_collect_candidates_skips_non_bill_and_duplicate_names() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        base = "https://www.govinfo.gov/bulkdata/BILLSTATUS/118/hr"
        names = ["BILLSTATUS-118hr1.xml", "index.xml", "BILLSTATUS-118hr1.xml"]  # bad + dup
        return httpx.Response(
            200,
            text=json.dumps({"files": [{"name": n, "link": f"{base}/{n}"} for n in names]}),
        )

    cands = collect_candidates(
        [118], bill_types=["hr"], client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert len(cands) == 1  # index.xml -> None skipped, duplicate skipped


def _patch_default_client(monkeypatch: pytest.MonkeyPatch, numbers: list[int]) -> None:
    import src.runtime.govinfo_bills_run as mod

    real = httpx.Client
    monkeypatch.setattr(
        mod.httpx, "Client", lambda **_kw: real(transport=httpx.MockTransport(_handler(numbers)))
    )


def test_default_client_used_for_collect_and_ingest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_default_client(monkeypatch, [1])
    cands = collect_candidates([118], bill_types=["hr"])  # client=None -> owns+close
    assert len(cands) == 1
    progress = ingest_batch(  # client=None -> owns+close
        congresses=[118],
        directory=tmp_path,
        as_of=_AS_OF,
        bill_types=["hr"],
        first_observed_at=_OBS,
    )
    assert progress.parsed_new == 1


def test_ingest_batch_counts_fetch_errors_as_skipped(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/json/" in request.url.path:
            return httpx.Response(200, text=_listing([1, 2]))
        if request.url.path.endswith("118hr1.xml"):
            return httpx.Response(503)
        return httpx.Response(200, text=_billstatus_xml(2))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    progress = ingest_batch(
        congresses=[118],
        directory=tmp_path,
        as_of=_AS_OF,
        bill_types=["hr"],
        client=client,
        first_observed_at=_OBS,
    )
    assert progress.parsed_new == 1 and progress.skipped == 1
    assert progress.remaining_after == 0  # both attempted (1 parsed, 1 skipped)
