from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

from src.graph.cdc import EntityDelta
from src.graph.contracts import ContractSourceAnchor, build_bill_output
from src.graph.export import (
    iter_contract_corpus,
    read_contract_corpus,
    write_contract_corpus,
    write_delta_feed,
)

_T = datetime(2025, 1, 1, tzinfo=UTC)


def _row(cid: str):
    return build_bill_output(
        canonical_bill_id=cid,
        display_name=cid,
        source_anchors=[
            ContractSourceAnchor(
                source_system="s",
                record_id=cid,
                source_url=f"https://x/{cid}",
                content_sha256="b" * 64,
                content_address="sha256/bb/bb/" + "b" * 64,
                known_at=datetime(2024, 1, 5, tzinfo=UTC),
                valid_from=date(2024, 1, 1),
            )
        ],
    )


def test_write_and_read_roundtrip(tmp_path: Path) -> None:
    rows = [_row("cb-b"), _row("cb-a")]
    manifest = write_contract_corpus(rows, directory=tmp_path, as_of=_T)
    assert manifest.record_count == 2
    assert (tmp_path / "records.jsonl").exists()
    assert (tmp_path / "manifest.json").exists()
    restored = read_contract_corpus(tmp_path)
    assert {r.canonical_id for r in restored} == {"cb-a", "cb-b"}


def test_iter_contract_corpus_streams_lazily(tmp_path: Path) -> None:
    write_contract_corpus([_row("cb-a"), _row("cb-b")], directory=tmp_path, as_of=_T)
    # tolerate a blank line mid-file (append seams)
    records = tmp_path / "records.jsonl"
    records.write_text(records.read_text().replace("\n", "\n\n", 1), encoding="utf-8")
    stream = iter_contract_corpus(tmp_path)
    assert next(iter(stream)).canonical_id == "cb-a"  # first row without reading the rest
    assert [r.canonical_id for r in iter_contract_corpus(tmp_path)] == ["cb-a", "cb-b"]


def test_records_sorted_for_determinism(tmp_path: Path) -> None:
    write_contract_corpus([_row("cb-b"), _row("cb-a")], directory=tmp_path, as_of=_T)
    lines = (tmp_path / "records.jsonl").read_text().splitlines()
    ids = [json.loads(line)["canonical_id"] for line in lines]
    assert ids == sorted(ids)


def test_manifest_content_hash_matches(tmp_path: Path) -> None:
    manifest = write_contract_corpus([_row("cb-a")], directory=tmp_path, as_of=_T)
    content = (tmp_path / "records.jsonl").read_bytes()
    assert manifest.content_sha256 == hashlib.sha256(content).hexdigest()
    on_disk = json.loads((tmp_path / "manifest.json").read_text())
    assert on_disk["content_sha256"] == manifest.content_sha256
    assert on_disk["as_of"] == _T.isoformat()


def test_identical_corpus_is_byte_identical(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    rows = [_row("cb-a"), _row("cb-b")]
    m1 = write_contract_corpus(rows, directory=a, as_of=_T)
    m2 = write_contract_corpus(list(reversed(rows)), directory=b, as_of=_T)
    # Same rows + same as_of -> identical content hash regardless of input order.
    assert m1.content_sha256 == m2.content_sha256
    assert (a / "records.jsonl").read_bytes() == (b / "records.jsonl").read_bytes()


def test_empty_corpus(tmp_path: Path) -> None:
    manifest = write_contract_corpus([], directory=tmp_path, as_of=_T)
    assert manifest.record_count == 0
    assert read_contract_corpus(tmp_path) == []


def test_delta_feed_write_and_append(tmp_path: Path) -> None:
    path = tmp_path / "feed" / "deltas.jsonl"
    d1 = EntityDelta(canonical_id="ce-1", change_type="created", added_external_ids=["bioguide:d1"])
    assert write_delta_feed([d1], path=path) == 1
    d2 = EntityDelta(canonical_id="ce-2", change_type="removed")
    assert write_delta_feed([d2], path=path, append=True) == 1
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["canonical_id"] == "ce-1"
    assert json.loads(lines[1])["canonical_id"] == "ce-2"


def test_delta_feed_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "deltas.jsonl"
    write_delta_feed([EntityDelta(canonical_id="ce-1", change_type="created")], path=path)
    write_delta_feed([EntityDelta(canonical_id="ce-2", change_type="created")], path=path)
    assert len(path.read_text().splitlines()) == 1  # overwritten, not appended
