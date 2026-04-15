from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.parse.disclosures.acquire import HOUSE_DISCLOSURE_SOURCE, SENATE_DISCLOSURE_SOURCE
from src.parse.disclosures.artifact_store import write_artifact
from src.parse.disclosures.discovery import fetch_disclosure_artifacts
from src.parse.disclosures.download import download_artifact_bytes, store_downloaded_artifact
from src.provenance.store import (
    ensure_data_source,
    fail_ingestion_run,
    finish_ingestion_run,
    start_ingestion_run,
)
from src.runtime.sources import HOUSE_DISCLOSURES, SENATE_DISCLOSURES

_SOURCE_BY_CHAMBER = {
    "house": HOUSE_DISCLOSURES,
    "senate": SENATE_DISCLOSURES,
}

_SOURCE_SLUG_BY_CHAMBER = {
    "house": HOUSE_DISCLOSURE_SOURCE,
    "senate": SENATE_DISCLOSURE_SOURCE,
}


@dataclass(frozen=True)
class DisclosureArtifactIngestResult:
    data_source: dict[str, Any]
    run_id: int
    chamber: str
    year: int
    filing_kind: str | None
    local_root: Path | None
    discovered_count: int
    stored_count: int
    artifact_rows: tuple[dict[str, Any], ...]


def run_disclosure_artifact_ingest(
    conn: Any,
    *,
    chamber: str,
    year: int,
    local_root: Path | None = None,
    filing_kind: str | None = None,
    client: Any = None,
) -> DisclosureArtifactIngestResult:
    if chamber not in _SOURCE_BY_CHAMBER:
        raise ValueError(f"chamber must be 'house' or 'senate', got {chamber!r}")

    spec = _SOURCE_BY_CHAMBER[chamber]
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
        "artifact_ingest",
        parameters={"chamber": chamber, "year": year, "filing_kind": filing_kind},
    )

    try:
        metas = fetch_disclosure_artifacts(
            chamber, year, filing_kind=filing_kind, client=client
        )

        artifact_rows: list[dict[str, Any]] = []
        for meta in metas:
            if meta.source_slug != _SOURCE_SLUG_BY_CHAMBER[chamber]:
                raise ValueError(
                    f"unexpected artifact source slug for {chamber}: {meta.source_slug!r}"
                )
            data = download_artifact_bytes(meta.source_url)
            row = store_downloaded_artifact(conn, meta, data)
            artifact_rows.append(row)
            if local_root is not None:
                write_artifact(local_root, meta, data)

    except Exception as exc:
        fail_ingestion_run(conn, run_id, str(exc))
        raise

    finish_ingestion_run(conn, run_id, len(artifact_rows))

    return DisclosureArtifactIngestResult(
        data_source=data_source,
        run_id=run_id,
        chamber=chamber,
        year=year,
        filing_kind=filing_kind,
        local_root=local_root,
        discovered_count=len(metas),
        stored_count=len(artifact_rows),
        artifact_rows=tuple(artifact_rows),
    )
