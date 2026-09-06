"""Versioned, HF-publishable Parquet release of the LOCUS ordinance graph (#4).

Track B (and the public) consume the unified graph as a downloadable, versioned
dataset structured exactly like LOCUS itself — bulk Parquet + a documented
schema + a content hash + a dataset card. This module flattens the contract
ordinance corpus (one :class:`~src.graph.contracts.EntityResolutionOutput` per
line) into a tabular Parquet release with a stable, documented column schema.

The flat row schema (one ordinance per row):

================  =========  ====================================================
column            type       meaning
================  =========  ====================================================
canonical_id      string     content-addressed ``cb-…`` bill ID (primary key)
jurisdiction_id   string     canonical jurisdiction code (join key to the graph)
jurisdiction_lvl  string     city | county
display_name      string     "<jurisdiction> ordinance: <header-or-text>"
function          string     LOCUS legal function (Context/Rules/Process/Enforcement)
topic             string     LOCUS topic (Buildings/Business/Nuisance/Zoning/Other)
is_substantive    bool       LOCUS substantive flag
opacity           float64    LOCUS dimension score (nullable)
paternalism       float64    LOCUS dimension score (nullable)
enforcement_disc  float64    LOCUS dimension score (nullable)
problem_salience  float64    LOCUS dimension score (nullable)
known_at          string     ISO-8601 leakage stamp
source_url        string     LOCUS dataset URL (provenance)
content_sha256    string     sha256 of the ordinance text (content address)
license           string     cc-by-nc-4.0 (carried per-row)
================  =========  ====================================================

Outputs under the release directory:

* ``locus_ordinances.parquet`` — the bulk table;
* ``RELEASE_MANIFEST.json`` — version, row count, parquet ``content_sha256``,
  column schema, and the LOCUS attribution/license;
* ``README.md`` — an HF-style dataset card (front-matter + provenance + the
  non-commercial license + schema), ready to push to the Hub.

pyarrow is a build-time tool (not a core dep); the import is local so the core
library stays importable without it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.core.files import sha256_file
from src.graph.contracts import EntityResolutionOutput
from src.graph.ingest.locus import LOCUS_ATTRIBUTION, LOCUS_DATASET_URL, LOCUS_LICENSE

PARQUET_FILENAME = "locus_ordinances.parquet"
RELEASE_MANIFEST_FILENAME = "RELEASE_MANIFEST.json"
README_FILENAME = "README.md"

# (column, parquet/arrow type) — the documented, stable release schema.
RELEASE_SCHEMA: tuple[tuple[str, str], ...] = (
    ("canonical_id", "string"),
    ("jurisdiction_id", "string"),
    ("jurisdiction_level", "string"),
    ("display_name", "string"),
    ("function", "string"),
    ("topic", "string"),
    ("is_substantive", "bool"),
    ("opacity", "float64"),
    ("paternalism", "float64"),
    ("enforcement_discretion", "float64"),
    ("problem_salience", "float64"),
    ("known_at", "string"),
    ("source_url", "string"),
    ("content_sha256", "string"),
    ("license", "string"),
)


@dataclass(frozen=True)
class ReleaseManifest:
    """The versioned release manifest (content-addressed)."""

    version: str
    parquet_filename: str
    row_count: int
    content_sha256: str
    schema: list[list[str]]
    dataset_url: str
    attribution: str
    license: str


def ordinance_to_release_row(row: EntityResolutionOutput) -> dict[str, Any]:
    """Flatten one ordinance contract row to a release table row."""
    dossier = row.dossier_json or {}
    scores = dossier.get("dimension_scores", {}) or {}
    anchor = row.source_anchors[0]
    return {
        "canonical_id": row.canonical_id,
        "jurisdiction_id": dossier.get("jurisdiction_id"),
        "jurisdiction_level": dossier.get("jurisdiction_level"),
        "display_name": row.display_name,
        "function": dossier.get("function"),
        "topic": dossier.get("topic"),
        "is_substantive": bool(dossier.get("is_substantive")),
        "opacity": scores.get("opacity"),
        "paternalism": scores.get("paternalism"),
        "enforcement_discretion": scores.get("enforcement_discretion"),
        "problem_salience": scores.get("problem_salience"),
        "known_at": row.known_at.isoformat(),
        "source_url": anchor.source_url,
        "content_sha256": anchor.content_sha256,
        "license": LOCUS_LICENSE,
    }


def _dataset_card(manifest: ReleaseManifest) -> str:
    """An HF-style dataset card (YAML front-matter + body) for the release."""
    schema_lines = "\n".join(
        f"  - name: {name}\n    dtype: {dtype}" for name, dtype in RELEASE_SCHEMA
    )
    return (
        "---\n"
        "license: cc-by-nc-4.0\n"
        "language:\n- en\n"
        "tags:\n- law\n- local-government\n- ordinances\n- knowledge-graph\n"
        "pretty_name: Psephos America LOCUS Ordinance Graph\n"
        "configs:\n- config_name: default\n  data_files:\n"
        f"  - split: train\n    path: {manifest.parquet_filename}\n"
        "dataset_info:\n  features:\n"
        f"{schema_lines}\n"
        f"  splits:\n  - name: train\n    num_examples: {manifest.row_count}\n"
        "---\n\n"
        "# Psephos America LOCUS Ordinance Graph\n\n"
        f"Version `{manifest.version}`. A versioned, content-addressed Parquet "
        "release of U.S. municipal + county ordinances, each joined to a canonical "
        "jurisdiction entity in the Psephos America knowledge graph and carrying the LOCUS "
        "legal `function`, `topic`, `is_substantive` flag, and four dimension "
        "scores (`opacity`, `paternalism`, `enforcement_discretion`, "
        "`problem_salience`).\n\n"
        "## Provenance & license\n\n"
        f"Derived from **{manifest.attribution}** Source: {manifest.dataset_url}\n\n"
        f"This release is **{manifest.license.upper()} — NON-COMMERCIAL**, "
        "inheriting LOCUS's license. Do not use commercially.\n\n"
        "## Content hash\n\n"
        f"`{manifest.parquet_filename}` sha256: `{manifest.content_sha256}`\n\n"
        "## Schema\n\n"
        "| column | type |\n|---|---|\n"
        + "".join(f"| {name} | {dtype} |\n" for name, dtype in RELEASE_SCHEMA)
    )


def write_release(
    rows: Iterable[EntityResolutionOutput],
    *,
    out_directory: Path | str,
    version: str,
) -> ReleaseManifest:  # pragma: no cover - parquet I/O exercised by an integration run
    """Write the Parquet release + manifest + dataset card; return the manifest."""
    import pyarrow as pa  # local import: build-time dep
    import pyarrow.parquet as pq

    out_dir = Path(out_directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build columnar arrays in the documented column order (sorted by id for
    # deterministic bytes), then write a single Parquet file.
    ordered = sorted(rows, key=lambda r: r.canonical_id)
    columns: dict[str, list[Any]] = {name: [] for name, _ in RELEASE_SCHEMA}
    for row in ordered:
        flat = ordinance_to_release_row(row)
        for name, _ in RELEASE_SCHEMA:
            columns[name].append(flat[name])

    arrow_types = {
        "string": pa.string(),
        "bool": pa.bool_(),
        "float64": pa.float64(),
    }
    table = pa.table(
        {name: pa.array(columns[name], type=arrow_types[dtype]) for name, dtype in RELEASE_SCHEMA}
    )
    parquet_path = out_dir / PARQUET_FILENAME
    pq.write_table(table, parquet_path)

    content_sha = sha256_file(parquet_path)
    manifest = ReleaseManifest(
        version=version,
        parquet_filename=PARQUET_FILENAME,
        row_count=len(ordered),
        content_sha256=content_sha,
        schema=[[name, dtype] for name, dtype in RELEASE_SCHEMA],
        dataset_url=LOCUS_DATASET_URL,
        attribution=LOCUS_ATTRIBUTION,
        license=LOCUS_LICENSE,
    )
    (out_dir / RELEASE_MANIFEST_FILENAME).write_text(
        json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / README_FILENAME).write_text(_dataset_card(manifest), encoding="utf-8")
    return manifest


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    from src.graph.export import iter_contract_corpus

    parser = argparse.ArgumentParser(description="Write the versioned LOCUS Parquet release")
    parser.add_argument("--corpus", default="data/exports/locus")
    parser.add_argument("--out", default="data/exports/locus/release")
    parser.add_argument("--version", default="v1.0")
    args = parser.parse_args(argv)
    manifest = write_release(
        iter_contract_corpus(args.corpus), out_directory=args.out, version=args.version
    )
    print(
        f"release {manifest.version}: rows={manifest.row_count} "
        f"sha256={manifest.content_sha256[:16]}… -> {args.out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
