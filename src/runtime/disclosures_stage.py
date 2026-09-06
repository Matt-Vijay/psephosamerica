"""Runtime staging for local disclosure artifact bundles.

Owns one public entry point:

    stage_disclosures_bundle(conn, bundle, *, local_root=None)

The function is also exported as run_disclosures_bundle_stage for callers
that prefer the run_* naming convention used elsewhere in this package.

Flow
----
1. Short-circuit with a zero-count result if the bundle has no artifacts.
2. Derive source_slug from bundle.artifacts; all entries must share one slug.
3. Ensure the data_source row for that source_slug.
4. Open an ingestion_run (run_type='ingest', parameters.stage='bundle_stage').
5. Create one source_artifact row per entry using the pre-computed sha256
   and typed metadata from each DisclosureArtifactEntry.
6. Finish or fail the ingestion_run.

No network calls.  The caller supplies a pre-loaded canonical bundle via the
DisclosuresBundle contract from src.runtime.disclosures_bundle.  Artifact
bytes are expected to already reside under local_root (when supplied); this
function does not write bytes to disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.db.repositories import rollback_if_available
from src.provenance.artifacts import create_source_artifact
from src.provenance.store import (
    ensure_data_source,
    fail_ingestion_run,
    finish_ingestion_run,
    start_ingestion_run,
)
from src.runtime.disclosures_bundle import DisclosuresBundle
from src.runtime.sources import source_by_slug

_MIME_BY_KIND: dict[str, str] = {
    "pdf": "application/pdf",
    "html": "text/html",
}


# Result type


@dataclass(frozen=True)
class DisclosureStagingResult:
    """Structured outcome of a single bundle staging run."""

    data_source: dict[str, Any]
    run_id: int
    artifact_rows: tuple[dict[str, Any], ...]
    staged_count: int  # rows written to source_artifact
    mirrored_count: int  # always 0; bytes are pre-placed, not written here


# Public entry point


def stage_disclosures_bundle(
    conn: Any,
    bundle: DisclosuresBundle,
    *,
    local_root: Path | None = None,
) -> DisclosureStagingResult:
    """Stage a canonical disclosure bundle into the DB.

    Args:
        conn:       Open psycopg connection (or compatible test stub).
        bundle:     Validated DisclosuresBundle from load_disclosures_bundle.
        local_root: Accepted for API compatibility; unused here.  Artifact
                    bytes are already on disk when a canonical bundle is staged.

    Returns:
        DisclosureStagingResult with artifact rows and staging counts.
        An empty bundle short-circuits: no DB writes, all counts zero.

    Raises:
        KeyError:   source_slug derived from entries is not a recognised slug.
        ValueError: Entries carry mixed source_slug values.
        Exception:  Any failure during artifact creation; the ingestion_run is
                    marked failed and the exception is re-raised.
    """
    if not bundle.artifacts:
        return DisclosureStagingResult(
            data_source={},
            run_id=0,
            artifact_rows=(),
            staged_count=0,
            mirrored_count=0,
        )

    slugs = {e.source_slug for e in bundle.artifacts}
    if len(slugs) > 1:
        raise ValueError(
            f"stage_disclosures_bundle requires a homogeneous bundle; "
            f"found multiple source_slugs: {sorted(slugs)}"
        )
    source_slug = next(iter(slugs))

    spec = source_by_slug(source_slug)
    data_source = ensure_data_source(
        conn,
        spec.slug,
        spec.name,
        spec.source_kind,
        spec.base_url,
    )
    run_id = start_ingestion_run(
        conn,
        data_source["id"],
        "ingest",
        parameters={
            "stage": "bundle_stage",
            "source_slug": source_slug,
            "entry_count": len(bundle.artifacts),
        },
    )

    try:
        artifact_rows: list[dict[str, Any]] = []
        for entry in bundle.artifacts:
            mime_type = _MIME_BY_KIND.get(entry.artifact_kind)
            row = create_source_artifact(
                conn,
                data_source_id=data_source["id"],
                artifact_kind=entry.artifact_kind,
                storage_uri=entry.storage_uri,
                sha256=entry.sha256,
                source_url=entry.source_url,
                mime_type=mime_type,
                fetched_at=None,
                source_record_id=entry.source_record_id,
                ingestion_run_id=run_id,
                commit=False,
            )
            artifact_rows.append(row)
    except Exception as exc:
        rollback_if_available(conn)
        fail_ingestion_run(conn, run_id, str(exc))
        raise

    finish_ingestion_run(conn, run_id, len(artifact_rows))

    return DisclosureStagingResult(
        data_source=data_source,
        run_id=run_id,
        artifact_rows=tuple(artifact_rows),
        staged_count=len(artifact_rows),
        mirrored_count=0,
    )


# Alias for callers that prefer the run_* naming convention.
run_disclosures_bundle_stage = stage_disclosures_bundle
