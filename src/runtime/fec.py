"""Runtime entry point for local FEC bulk-data loads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, TypeVar

from src.db.load_report import (
    LoadSummary,
    TableWriteResult,
    WarnErrorSummary,
    build_load_summary,
)
from src.db.repositories import rollback_if_available
from src.ingest.fec.bulk import (
    iter_candidate_committee_linkage,
    iter_committee_master,
    iter_individual_contributions,
)
from src.ingest.fec.models import (
    CandidateCommitteeLinkage,
    CommitteeRecord,
)
from src.pipeline.fec_load_run import FecLoadInputs, run_fec_load
from src.provenance.artifacts import create_source_artifact
from src.provenance.store import (
    ensure_data_source,
    fail_ingestion_run,
    finish_ingestion_run,
    start_ingestion_run,
)
from src.runtime.sources import FEC_BULK

_FEC_BULK_LANDING_URL = "https://www.fec.gov/data/browse-data/?tab=bulk-data"
_TXT_MIME = "text/plain"


@dataclass(frozen=True)
class FecBulkFilePaths:
    committee_master: Path
    candidate_committee_linkage: Path
    individual_contributions: Path
    committee_source_url: str = _FEC_BULK_LANDING_URL
    linkage_source_url: str = _FEC_BULK_LANDING_URL
    contribution_source_url: str = _FEC_BULK_LANDING_URL
    contribution_chunk_size: int = 50_000


@dataclass(frozen=True)
class FecLocalLoadResult:
    data_source: dict[str, Any]
    run_id: int
    artifact_rows: tuple[dict[str, Any], ...]
    parsed_counts: dict[str, int]
    load_summary: LoadSummary


def run_fec_local_load_runtime(
    conn: Any,
    files: FecBulkFilePaths,
) -> FecLocalLoadResult:
    """Stage local FEC bulk files as source artifacts and load canonical rows."""
    _validate_files(files)
    committees = list(iter_committee_master(files.committee_master))
    linkages = list(iter_candidate_committee_linkage(files.candidate_committee_linkage))

    data_source = ensure_data_source(
        conn,
        slug=FEC_BULK.slug,
        name=FEC_BULK.name,
        source_kind=FEC_BULK.source_kind,
        base_url=FEC_BULK.base_url,
    )
    run_id = start_ingestion_run(
        conn,
        data_source_id=data_source["id"],
        run_type="ingest",
        parameters={
            "stage": "fec_local_load",
            "committee_master": str(files.committee_master),
            "candidate_committee_linkage": str(files.candidate_committee_linkage),
            "individual_contributions": str(files.individual_contributions),
        },
    )

    try:
        committee_artifact = _create_text_artifact(
            conn,
            data_source_id=data_source["id"],
            ingestion_run_id=run_id,
            path=files.committee_master,
            source_url=files.committee_source_url,
            source_record_id="committee-master",
        )
        linkage_artifact = _create_text_artifact(
            conn,
            data_source_id=data_source["id"],
            ingestion_run_id=run_id,
            path=files.candidate_committee_linkage,
            source_url=files.linkage_source_url,
            source_record_id="candidate-committee-linkage",
        )
        contribution_artifact = _create_text_artifact(
            conn,
            data_source_id=data_source["id"],
            ingestion_run_id=run_id,
            path=files.individual_contributions,
            source_url=files.contribution_source_url,
            source_record_id="individual-contributions",
        )
        summaries: list[LoadSummary] = []
        parsed_contribution_count = 0
        inputs = FecLoadInputs(
            committees=committees,
            contributions=[],
            linkages=linkages,
            committee_source_artifact_id=_row_id(committee_artifact),
            linkage_source_artifact_id=_row_id(linkage_artifact),
        )
        summaries.append(run_fec_load(inputs, conn, run_id=run_id, commit=False))
        for contributions in _chunks(
            iter_individual_contributions(files.individual_contributions),
            size=files.contribution_chunk_size,
        ):
            parsed_contribution_count += len(contributions)
            summaries.append(
                run_fec_load(
                    FecLoadInputs(
                        contributions=contributions,
                        contribution_source_artifact_id=_row_id(contribution_artifact),
                    ),
                    conn,
                    run_id=run_id,
                    commit=False,
                )
            )
        load_summary = _merge_load_summaries(summaries, run_id=run_id)
    except Exception as exc:
        rollback_if_available(conn)
        fail_ingestion_run(conn, run_id, str(exc))
        raise

    try:
        finish_ingestion_run(conn, run_id, record_count=load_summary.total_written)
    except Exception:
        rollback_if_available(conn)
        raise

    return FecLocalLoadResult(
        data_source=data_source,
        run_id=run_id,
        artifact_rows=(committee_artifact, linkage_artifact, contribution_artifact),
        parsed_counts=_parsed_counts(
            committees,
            linkages,
            contribution_count=parsed_contribution_count,
        ),
        load_summary=load_summary,
    )


def _validate_files(files: FecBulkFilePaths) -> None:
    for path in (
        files.committee_master,
        files.candidate_committee_linkage,
        files.individual_contributions,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)


def _create_text_artifact(
    conn: Any,
    *,
    data_source_id: int,
    ingestion_run_id: int,
    path: Path,
    source_url: str,
    source_record_id: str,
) -> dict[str, Any]:
    return create_source_artifact(
        conn,
        data_source_id=data_source_id,
        artifact_kind="txt",
        storage_uri=str(path.resolve()),
        sha256=_sha256_file(path),
        ingestion_run_id=ingestion_run_id,
        source_url=source_url,
        mime_type=_TXT_MIME,
        source_record_id=source_record_id,
        commit=False,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_id(row: dict[str, Any]) -> int:
    row_id = row.get("id")
    if isinstance(row_id, bool) or not isinstance(row_id, int):
        raise TypeError(f"expected source_artifact id to be int, got {row_id!r}")
    return row_id


_T = TypeVar("_T")


def _chunks(records: Iterable[_T], *, size: int) -> Iterator[list[_T]]:
    if size < 1:
        raise ValueError("contribution_chunk_size must be positive")
    chunk: list[_T] = []
    for record in records:
        chunk.append(record)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _parsed_counts(
    committees: list[CommitteeRecord],
    linkages: list[CandidateCommitteeLinkage],
    *,
    contribution_count: int,
) -> dict[str, int]:
    return {
        "committees": len(committees),
        "candidate_committee_linkages": len(linkages),
        "individual_contributions": contribution_count,
    }


def _merge_load_summaries(
    summaries: list[LoadSummary],
    *,
    run_id: int,
) -> LoadSummary:
    warn_error = WarnErrorSummary()
    table_results: list[TableWriteResult] = []
    for summary in summaries:
        table_results.extend(summary.table_results)
        warn_error.warnings.extend(summary.warn_error.warnings)
        warn_error.errors.extend(summary.warn_error.errors)
    return build_load_summary(table_results, warn_error=warn_error, run_id=run_id)
