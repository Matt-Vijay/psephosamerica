from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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


def test_export_locus_writes_corpus_cdc_sidecar(tmp_path: Path) -> None:
    # Drive the export through its private building blocks with an in-memory stream
    # (no network): replicate what export_locus does, then assert the artifacts.
    from src.graph.cdc import diff_outputs
    from src.graph.export import write_contract_corpus, write_delta_feed
    from src.graph.ingest.locus_export import _sidecar_record

    records = [_row(), _row(content="Noise after 10pm prohibited.", function="Enforcement")]
    rows, jurisdictions, _scanned, _skipped = build_ordinance_corpus(iter(records), known_at=_KNOWN)
    write_contract_corpus(rows, directory=tmp_path, as_of=_KNOWN)
    deltas = diff_outputs({}, {r.canonical_id: r for r in rows})
    write_delta_feed(deltas, path=tmp_path / DELTAS_FILENAME, append=False)
    sidecar = tmp_path / CONTENT_SIDECAR_FILENAME
    with sidecar.open("w", encoding="utf-8") as handle:
        for row in sorted(rows, key=lambda r: r.canonical_id):
            handle.write(json.dumps(_sidecar_record(row), sort_keys=True) + "\n")

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
