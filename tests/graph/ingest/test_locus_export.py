from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.graph.export import (
    DELTAS_FILENAME,
    MANIFEST_FILENAME,
    RECORDS_FILENAME,
    read_contract_corpus,
)
from src.graph.ingest.locus_export import (
    CONTENT_SIDECAR_FILENAME,
    INGEST_META_FILENAME,
    build_ordinance_corpus,
)

_KNOWN = datetime(2026, 6, 20, tzinfo=UTC)


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "header": "### 5.10 Sign permits.",
        "content": "A permit is required for any sign over 12 square feet.",
        "is_substantive": True,
        "function": "Rules",
        "topic": "Zoning",
        "source_jurisdiction_type": "cities",
        "state": "ca",
        "city": "oakland",
        "county": None,
        "opacity": 0.5,
        "paternalism": 0.1,
        "enforcement_discretion": 0.9,
        "problem_salience": 0.3,
    }
    base.update(overrides)
    return base


def test_build_ordinance_corpus_dedupes_and_counts() -> None:
    records = [
        _row(),
        _row(),  # exact duplicate -> collapses
        _row(content="Different provision.", state="ny", city="buffalo"),
        _row(state="zz", city=""),  # skipped (no city)
    ]
    rows, jurisdictions, scanned, skipped = build_ordinance_corpus(iter(records), known_at=_KNOWN)
    assert scanned == 4
    assert skipped == 1
    assert len(rows) == 2  # the duplicate collapsed
    assert set(jurisdictions) == {"us-ca-city-oakland", "us-ny-city-buffalo"}


def test_export_locus_writes_corpus_cdc_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import huggingface_hub
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.graph.ingest.locus_export import LOCUS_REVISION, export_locus

    records = [_row(), _row(content="Noise after 10pm prohibited.", function="Enforcement")]
    shard = tmp_path / "shard.parquet"
    pq.write_table(pa.Table.from_pylist(records), shard)
    calls = []

    def download(*args, **kwargs):
        calls.append((args, kwargs))
        return str(shard)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    report = export_locus(out_directory=tmp_path, max_shards=1, max_rows=2, as_of=_KNOWN)
    assert report.dataset_revision == LOCUS_REVISION
    assert report.ordinances == report.deltas_written == 2
    assert report.is_full_corpus is False
    assert calls == [
        (
            ("LocalLaws/LOCUS-v1", "data/train-00000-of-00008.parquet"),
            {"repo_type": "dataset", "revision": LOCUS_REVISION, "cache_dir": None},
        )
    ]
    sidecar = tmp_path / CONTENT_SIDECAR_FILENAME

    assert (tmp_path / RECORDS_FILENAME).exists()
    assert (tmp_path / MANIFEST_FILENAME).exists()
    back = read_contract_corpus(tmp_path)
    assert len(back) == 2
    assert all(r.entity_type == "bill" for r in back)
    # Every ordinance produced a 'created' delta.
    delta_lines = (tmp_path / DELTAS_FILENAME).read_text().strip().splitlines()
    assert len(delta_lines) == 2
    assert all(json.loads(line)["change_type"] == "created" for line in delta_lines)
    # Sidecar carries scores + labels keyed by canonical id.
    side_lines = sidecar.read_text().strip().splitlines()
    assert len(side_lines) == 2
    first = json.loads(side_lines[0])
    assert set(first) == {
        "canonical_id",
        "text",
        "function",
        "topic",
        "is_substantive",
        "dimension_scores",
        "jurisdiction_id",
    }
    assert first["jurisdiction_id"].startswith("us-")


def test_ingest_meta_constant_names() -> None:
    # Guard the on-disk artifact filenames against accidental rename.
    assert INGEST_META_FILENAME == "ingest_meta.json"
    assert CONTENT_SIDECAR_FILENAME == "ordinance_content.jsonl"
