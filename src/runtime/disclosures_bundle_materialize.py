"""Materialize a canonical local disclosures bundle from official indexes."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.parse.disclosures.acquire import ArtifactKind, ArtifactMeta
from src.parse.disclosures.artifact_store import write_artifact
from src.parse.disclosures.download import download_artifact_bytes, sha256_bytes
from src.parse.disclosures.house_index import HouseFilingKind, HouseIndexRow, fetch_house_index
from src.parse.disclosures.models import Chamber
from src.parse.disclosures.senate_index import SenateIndexRow, fetch_senate_index
from src.runtime.disclosures_bundle import (
    DisclosureArtifactEntry,
    DisclosuresBundle,
    HouseBundledIndexRow,
    SenateBundledIndexRow,
    write_disclosures_bundle,
)
from src.runtime.sources import HOUSE_DISCLOSURES, SENATE_DISCLOSURES


@dataclass(frozen=True)
class MaterializedDisclosuresBundleResult:
    bundle_path: Path
    artifact_root: Path
    chamber: Literal["house", "senate", "both"]
    years: tuple[int, ...]
    artifact_count: int
    house_count: int
    senate_count: int


def materialize_disclosures_bundle(
    *,
    years: Sequence[int],
    chamber: Literal["house", "senate", "both"],
    bundle_path: Path,
    artifact_root: Path,
) -> MaterializedDisclosuresBundleResult:
    normalized_years = tuple(sorted(dict.fromkeys(years)))
    if not normalized_years:
        raise ValueError("years must contain at least one year")
    if bundle_path.exists():
        raise ValueError("bundle_path must not already exist")
    if artifact_root.exists() and (not artifact_root.is_dir() or any(artifact_root.iterdir())):
        raise ValueError("artifact_root must not already contain files")

    entries: list[DisclosureArtifactEntry] = []
    house_count = 0
    senate_count = 0
    staging_root = _sibling_staging_dir(artifact_root)
    bundle_stage_dir: Path | None = None
    try:
        for year in normalized_years:
            if chamber in {"house", "both"}:
                annual_rows = fetch_house_index(year, HouseFilingKind.ANNUAL)
                ptr_rows = fetch_house_index(year, HouseFilingKind.PTR)
                house_rows: list[HouseIndexRow] = [*annual_rows, *ptr_rows]
                for row in house_rows:
                    entry = _materialize_house_entry(row=row, artifact_root=staging_root)
                    entries.append(entry)
                    house_count += 1
            if chamber in {"senate", "both"}:
                senate_rows = fetch_senate_index(year)
                for senate_row in senate_rows:
                    entry = _materialize_senate_entry(
                        row=senate_row,
                        artifact_root=staging_root,
                    )
                    entries.append(entry)
                    senate_count += 1

        bundle = DisclosuresBundle(artifacts=tuple(entries))
        bundle_stage_dir = _sibling_staging_dir(bundle_path)
        staged_bundle_path = bundle_stage_dir / bundle_path.name
        write_disclosures_bundle(staged_bundle_path, bundle)

        _promote_staged_directory(staging_root, artifact_root)
        staged_bundle_path.replace(bundle_path)
    except BaseException:
        _cleanup_staging_path(staging_root)
        _cleanup_staging_path(bundle_stage_dir)
        raise
    else:
        _cleanup_staging_path(bundle_stage_dir)
    return MaterializedDisclosuresBundleResult(
        bundle_path=bundle_path,
        artifact_root=artifact_root,
        chamber=chamber,
        years=normalized_years,
        artifact_count=len(entries),
        house_count=house_count,
        senate_count=senate_count,
    )


def _materialize_house_entry(
    row: HouseIndexRow,
    artifact_root: Path,
) -> DisclosureArtifactEntry:
    kind_path = (
        "public_disc/financial-pdfs"
        if row.filing_kind is HouseFilingKind.ANNUAL
        else "public_disc/ptr-pdfs"
    )
    source_url = f"https://disclosures.house.gov/{kind_path}/{row.year}/{row.doc_id}.pdf"
    storage_uri = f"house/{row.year}/{row.doc_id}.pdf"
    data = download_artifact_bytes(source_url)
    write_artifact(
        artifact_root,
        _artifact_meta(
            chamber=Chamber.HOUSE,
            storage_key=storage_uri,
            source_url=source_url,
            filing_year=row.year,
            source_record_id=row.doc_id,
            source_slug=HOUSE_DISCLOSURES.slug,
        ),
        data,
    )
    return DisclosureArtifactEntry(
        source_record_id=row.doc_id,
        chamber="house",
        filing_year=row.year,
        storage_uri=storage_uri,
        source_url=source_url,
        source_slug=HOUSE_DISCLOSURES.slug,
        artifact_kind="pdf",
        sha256=sha256_bytes(data),
        index_row=HouseBundledIndexRow(
            last_name=row.last_name,
            first_name=row.first_name,
            suffix=row.suffix,
            raw_filing_type=row.raw_filing_type,
            state_dst=row.state_dst,
            filing_date=row.filing_date.isoformat(),
            doc_id=row.doc_id,
            filing_kind=row.filing_kind.value,
        ),
    )


def _materialize_senate_entry(
    row: SenateIndexRow,
    artifact_root: Path,
) -> DisclosureArtifactEntry:
    source_url = f"https://efdsearch.senate.gov/search/view/paper/{row.doc_id}/"
    storage_uri = f"senate/{row.filing_year}/{row.doc_id}.pdf"
    data = download_artifact_bytes(source_url)
    write_artifact(
        artifact_root,
        _artifact_meta(
            chamber=Chamber.SENATE,
            storage_key=storage_uri,
            source_url=source_url,
            filing_year=row.filing_year,
            source_record_id=row.doc_id,
            source_slug=SENATE_DISCLOSURES.slug,
        ),
        data,
    )
    return DisclosureArtifactEntry(
        source_record_id=row.doc_id,
        chamber="senate",
        filing_year=row.filing_year,
        storage_uri=storage_uri,
        source_url=source_url,
        source_slug=SENATE_DISCLOSURES.slug,
        artifact_kind="pdf",
        sha256=sha256_bytes(data),
        index_row=SenateBundledIndexRow(
            first_name=row.first_name,
            last_name=row.last_name,
            office=row.office,
            report_type=row.report_type,
            date_filed=row.date_filed,
            doc_id=row.doc_id,
        ),
    )


def _artifact_meta(
    *,
    chamber: Chamber,
    storage_key: str,
    source_url: str,
    filing_year: int,
    source_record_id: str,
    source_slug: str,
) -> ArtifactMeta:
    return ArtifactMeta(
        source_slug=source_slug,
        chamber=chamber,
        artifact_kind=ArtifactKind.PDF,
        source_url=source_url,
        storage_key=storage_key,
        member_bioguide_id="",
        filing_year=filing_year,
        source_record_id=source_record_id,
    )


def _sibling_staging_dir(final_path: Path) -> Path:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    return Path(
        tempfile.mkdtemp(
            prefix=f".{final_path.name}.",
            dir=final_path.parent,
        )
    )


def _promote_staged_directory(staging_path: Path, final_path: Path) -> None:
    if final_path.exists():
        final_path.rmdir()
    staging_path.replace(final_path)


def _cleanup_staging_path(path: Path | None) -> None:
    if path is not None and path.exists():
        shutil.rmtree(path, ignore_errors=True)
