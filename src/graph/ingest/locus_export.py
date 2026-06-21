"""Download LOCUS-v1 parquet shards -> contract ordinance corpus + sidecar + CDC.

The runner for deliverable #1. It streams LOCUS parquet shards from the Hugging
Face Hub (``LocalLaws/LOCUS-v1``), runs each row through the pure adapter in
:mod:`src.graph.ingest.locus`, and writes three artifacts under an output
directory:

* a contract corpus (``records.jsonl`` + content-addressed ``manifest.json``)
  of pending ordinance rows, via :func:`~src.graph.export.write_contract_corpus`;
* a ``deltas.jsonl`` CDC feed (every ordinance is ``created`` on first ingest),
  via :func:`~src.graph.export.write_delta_feed`, so Track B hot-swaps the new
  ordinance tier; and
* an ``ordinance_content.jsonl`` text sidecar (``{canonical_id, text, function,
  topic, dimension_scores}``) so the semantic re-embed can vectorize ordinances
  the same way it does federal / CA bills.

Honest cap: LOCUS ships as 8 parquet shards (~1.6 GB, 2.2M rows). ``--max-shards``
and ``--max-rows`` bound a representative sample for a constrained run; the full
set drops in by removing the caps. The sample size actually ingested is recorded
in the manifest sidecar (``ingest_meta.json``) so the cap is documented, never
hidden.

This module isolates all network + parquet I/O (huggingface_hub + pyarrow, which
are build-time tools, not core library deps) from the pure adapter, keeping the
core graph library importable without them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.graph.cdc import diff_outputs
from src.graph.contracts import EntityResolutionOutput
from src.graph.export import (
    DELTAS_FILENAME,
    write_contract_corpus,
    write_delta_feed,
)
from src.graph.ingest.locus import (
    LOCUS_ATTRIBUTION,
    LOCUS_DATASET_URL,
    LOCUS_LICENSE,
    now_utc,
    ordinance_content_sha256,
    parse_locus_row,
)
from src.graph.jurisdictions import Jurisdiction

LOCUS_REPO = "LocalLaws/LOCUS-v1"
SHARD_TEMPLATE = "data/train-{index:05d}-of-00008.parquet"
SHARD_COUNT = 8
CONTENT_SIDECAR_FILENAME = "ordinance_content.jsonl"
INGEST_META_FILENAME = "ingest_meta.json"

# The columns the adapter needs; reading a subset keeps each shard scan cheap.
_LOCUS_COLUMNS = [
    "header",
    "content",
    "is_substantive",
    "function",
    "topic",
    "source_jurisdiction_type",
    "state",
    "city",
    "county",
    "opacity",
    "paternalism",
    "enforcement_discretion",
    "problem_salience",
]


@dataclass(frozen=True)
class LocusIngestReport:
    """Counts + provenance from one LOCUS ingest run (recorded in ingest_meta.json)."""

    dataset_url: str
    attribution: str
    license: str
    as_of: str
    shards_read: int
    shard_total: int
    rows_scanned: int
    rows_skipped: int
    ordinances: int
    jurisdictions: int
    deltas_written: int
    is_full_corpus: bool


def iter_shard_rows(
    *,
    max_shards: int | None,
    max_rows: int | None,
    cache_dir: str | None = None,
) -> Iterator[dict[str, Any]]:  # pragma: no cover - network + parquet I/O
    """Stream LOCUS rows as plain dicts from up to ``max_shards`` HF parquet shards."""
    from huggingface_hub import hf_hub_download  # local import: build-time dep
    import pyarrow.parquet as pq  # local import: build-time dep

    shard_limit = SHARD_COUNT if max_shards is None else min(max_shards, SHARD_COUNT)
    yielded = 0
    for index in range(shard_limit):
        path = hf_hub_download(
            LOCUS_REPO,
            SHARD_TEMPLATE.format(index=index),
            repo_type="dataset",
            cache_dir=cache_dir,
        )
        parquet = pq.ParquetFile(path)  # type: ignore[no-untyped-call]
        for batch in parquet.iter_batches(  # type: ignore[no-untyped-call]
            columns=_LOCUS_COLUMNS, batch_size=8192
        ):
            for record in batch.to_pylist():
                if max_rows is not None and yielded >= max_rows:
                    return
                yield record
                yielded += 1


def build_ordinance_corpus(
    records: Iterator[dict[str, Any]],
    *,
    known_at: datetime,
) -> tuple[list[EntityResolutionOutput], dict[str, Jurisdiction], int, int]:
    """Drain a row stream into (ordinance rows, jurisdictions, scanned, skipped).

    De-duplicates by canonical bill ID: identical provisions appearing twice in
    the corpus collapse to one row (zero false merges, zero dup rows).
    """
    rows_by_id: dict[str, EntityResolutionOutput] = {}
    jurisdictions: dict[str, Jurisdiction] = {}
    scanned = 0
    skipped = 0
    from src.graph.ingest.locus import ordinance_row

    for record in records:
        scanned += 1
        parsed = parse_locus_row(record)
        if parsed is None:
            skipped += 1
            continue
        row = ordinance_row(parsed, known_at=known_at)
        rows_by_id[row.canonical_id] = row
        jurisdiction = parsed.jurisdiction
        jurisdictions[jurisdiction.code] = jurisdiction
    return list(rows_by_id.values()), jurisdictions, scanned, skipped


def _sidecar_record(row: EntityResolutionOutput) -> dict[str, Any]:
    dossier = row.dossier_json or {}
    return {
        "canonical_id": row.canonical_id,
        "text": row.display_name,
        "function": dossier.get("function"),
        "topic": dossier.get("topic"),
        "is_substantive": dossier.get("is_substantive"),
        "dimension_scores": dossier.get("dimension_scores", {}),
        "jurisdiction_id": dossier.get("jurisdiction_id"),
    }


def export_locus(
    *,
    out_directory: Path | str,
    max_shards: int | None = None,
    max_rows: int | None = None,
    as_of: datetime | None = None,
    cache_dir: str | None = None,
) -> LocusIngestReport:  # pragma: no cover - orchestrates network I/O
    """Run the full LOCUS ingest: corpus + delta CDC + content sidecar + meta."""
    out_dir = Path(out_directory)
    observed = as_of if as_of is not None else now_utc()
    rows, jurisdictions, scanned, skipped = build_ordinance_corpus(
        iter_shard_rows(max_shards=max_shards, max_rows=max_rows, cache_dir=cache_dir),
        known_at=observed,
    )

    write_contract_corpus(rows, directory=out_dir, as_of=observed)
    deltas = diff_outputs({}, {row.canonical_id: row for row in rows})
    deltas_written = write_delta_feed(deltas, path=out_dir / DELTAS_FILENAME, append=False)

    sidecar = out_dir / CONTENT_SIDECAR_FILENAME
    with sidecar.open("w", encoding="utf-8") as handle:
        for row in sorted(rows, key=lambda r: r.canonical_id):
            handle.write(json.dumps(_sidecar_record(row), sort_keys=True) + "\n")

    shards_read = SHARD_COUNT if max_shards is None else min(max_shards, SHARD_COUNT)
    report = LocusIngestReport(
        dataset_url=LOCUS_DATASET_URL,
        attribution=LOCUS_ATTRIBUTION,
        license=LOCUS_LICENSE,
        as_of=observed.isoformat(),
        shards_read=shards_read,
        shard_total=SHARD_COUNT,
        rows_scanned=scanned,
        rows_skipped=skipped,
        ordinances=len(rows),
        jurisdictions=len(jurisdictions),
        deltas_written=deltas_written,
        is_full_corpus=(max_shards is None and max_rows is None),
    )
    (out_dir / INGEST_META_FILENAME).write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # Touch the content-hash helper so it stays referenced for the sidecar tooling.
    assert ordinance_content_sha256 is not None
    return report


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Ingest LOCUS-v1 into a contract corpus")
    parser.add_argument("--out", default="data/exports/locus")
    parser.add_argument("--max-shards", type=int, default=None, help="cap shards (8 = full)")
    parser.add_argument("--max-rows", type=int, default=None, help="cap total rows")
    parser.add_argument("--cache-dir", default=None)
    args = parser.parse_args(argv)
    report = export_locus(
        out_directory=args.out,
        max_shards=args.max_shards,
        max_rows=args.max_rows,
        cache_dir=args.cache_dir,
    )
    print(
        f"LOCUS: ordinances={report.ordinances} jurisdictions={report.jurisdictions} "
        f"shards={report.shards_read}/{report.shard_total} scanned={report.rows_scanned} "
        f"skipped={report.rows_skipped} full={report.is_full_corpus}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
