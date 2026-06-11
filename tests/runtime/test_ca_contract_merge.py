from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.graph.export import read_contract_corpus, write_contract_corpus
from src.graph.ingest.ca_leginfo import ca_bill_ref
from src.runtime.ca_contract_merge import (
    BILL_VERSION_FILE,
    fetch_ca_tables,
    merge_ca_into_corpus,
)
from src.runtime.ca_leginfo_votes import LEGISLATOR_FILE

_NOW = datetime(2026, 6, 11, tzinfo=UTC)

_VERSIONS = "\n".join(
    [
        "`20250AB1399INT`\t`202520260AB13`\t99\t2025-01-02 00:00:00\t`Introduced`\tNULL\t`Surplus land: planning.`\tx",
        "`20250SB199INT`\t`202520260SB1`\t99\t2025-01-06 00:00:00\t`Introduced`\tNULL\t`Wildfire insurance.`\tx",
    ]
)
_LEGISLATORS = "\n".join(
    [
        "`SD02`\t`20252026`\t`McGuire, Mike`\t`S`\t`McGuire`\t`Mike`\t`McGuire`\tNULL\tNULL\t`Senator`\t`Senator`\t`DEM`\t`Y`\tx\tts\t`Y`",
        "`AD78`\t`20252026`\t`Ward, Chris`\t`A`\t`Ward`\t`Chris`\t`Ward`\tN\tNULL\t`AM`\t`AM`\t`REP`\t`Y`\tx\tts\t`Y`",
    ]
)


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(BILL_VERSION_FILE, _VERSIONS)
        archive.writestr(LEGISLATOR_FILE, _LEGISLATORS)
    return buffer.getvalue()


def _range_client(blob: bytes) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(blob))})
        start_s, end_s = request.headers["Range"].removeprefix("bytes=").split("-")
        return httpx.Response(206, content=blob[int(start_s) : int(end_s) + 1])

    return httpx.Client(transport=httpx.MockTransport(handler))


def _empty_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "contract_records"
    write_contract_corpus([], directory=corpus, as_of=_NOW)
    return corpus


def test_fetch_ca_tables_over_ranges() -> None:
    versions, legislators = fetch_ca_tables(2025, client=_range_client(_zip_bytes()))
    assert "Surplus land" in versions and "McGuire" in legislators


def test_merge_adds_dense_bills_and_persons(tmp_path: Path) -> None:
    corpus = _empty_corpus(tmp_path)
    sidecar = tmp_path / "bill_content_ca.jsonl"
    report = merge_ca_into_corpus(
        year=2025,
        main_directory=corpus,
        content_sidecar=sidecar,
        client=_range_client(_zip_bytes()),
        as_of=_NOW,
    )
    assert report.bills_parsed == 2 and report.bills_added == 2
    assert report.persons_parsed == 2 and report.persons_added == 2
    assert report.corpus_total == 4 and report.deltas_written == 4
    rows = {r.canonical_id: r for r in read_contract_corpus(corpus)}
    ab13 = rows[ca_bill_ref("2025-2026", "AB13").canonical_id]
    assert ab13.entity_type == "bill"
    assert ab13.enrichment_status == "ready"  # densified by regenerate_corpus
    assert ab13.dossier_embedding is not None and ab13.structural_embedding is not None
    persons = [r for r in rows.values() if r.entity_type == "person"]
    assert {p.display_name for p in persons} == {"Mike McGuire", "Chris Ward"}
    side_rows = [json.loads(line) for line in sidecar.read_text().splitlines()]
    assert len(side_rows) == 2
    assert any("Wildfire insurance" in r["text"] for r in side_rows)


def test_merge_is_idempotent(tmp_path: Path) -> None:
    corpus = _empty_corpus(tmp_path)
    sidecar = tmp_path / "ca.jsonl"
    blob = _zip_bytes()
    merge_ca_into_corpus(
        year=2025,
        main_directory=corpus,
        content_sidecar=sidecar,
        client=_range_client(blob),
        as_of=_NOW,
    )
    again = merge_ca_into_corpus(
        year=2025,
        main_directory=corpus,
        content_sidecar=sidecar,
        client=_range_client(blob),
        as_of=_NOW,
    )
    assert again.bills_added == 0 and again.persons_added == 0
    assert again.deltas_written == 0 and again.sidecar_rows == 0
    assert len(sidecar.read_text().splitlines()) == 2  # not duplicated


def test_merge_respects_max_bills(tmp_path: Path) -> None:
    report = merge_ca_into_corpus(
        year=2025,
        main_directory=_empty_corpus(tmp_path),
        content_sidecar=tmp_path / "ca.jsonl",
        client=_range_client(_zip_bytes()),
        as_of=_NOW,
        max_bills=1,
    )
    assert report.bills_added == 1 and report.sidecar_rows == 1


def test_merge_default_client_and_as_of(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import src.runtime.ca_contract_merge as mod

    blob = _zip_bytes()
    real = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(blob))})
        start_s, end_s = request.headers["Range"].removeprefix("bytes=").split("-")
        return httpx.Response(206, content=blob[int(start_s) : int(end_s) + 1])

    monkeypatch.setattr(
        mod.httpx, "Client", lambda **_kw: real(transport=httpx.MockTransport(handler))
    )
    report = merge_ca_into_corpus(
        year=2025,
        main_directory=_empty_corpus(tmp_path),
        content_sidecar=tmp_path / "ca.jsonl",
    )
    assert report.bills_added == 2
