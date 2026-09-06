"""Bundle-process path for local disclosure pipeline runs.

Entry point: run_disclosures_bundle_process.

Flow:
  1. validate bundle       — check bundle carries an 'artifacts' iterable
  2. stage bundle          — write artifact rows into DB + local_root
  3. parse inputs          — build DisclosureParseInput per staged artifact:
                             read bytes from disk + SHA-256 integrity check
                             (when local_root is supplied and parse_inputs is
                             None).  When explicit parse_inputs are provided
                             and local_root is set, SHA-256 is still verified
                             for every bundle entry before parse begins.
  4. parse                 — run_disclosure_parse_runtime with bundle-backed
                             IndexMatchProvider (resolves index rows from
                             bundle; no live fetches)
  5. transform             — transform_parse_sessions
  6. load                  — run_disclosures_load_runtime
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.query.disclosure_member_rows import fetch_member_rows_for_disclosures
from src.runtime.disclosures import (
    DisclosuresLoadRuntimeResult,
    run_disclosures_load_runtime,
)
from src.runtime.disclosures_bundle import DisclosuresBundle
from src.runtime.disclosures_bundle_files import (
    Sha256Mismatch,
    read_and_verify_entry,
    verify_entry_sha256,
)
from src.runtime.disclosures_bundle_validate import validate_disclosures_bundle
from src.runtime.disclosures_index_rows import ArtifactIndexMatch, filing_year_or_none
from src.runtime.disclosures_parse import (
    DisclosureParseRuntimeResult,
    run_disclosure_parse_runtime,
)
from src.runtime.disclosures_parse_inputs import DisclosureParseInput
from src.runtime.disclosures_stage import stage_disclosures_bundle
from src.runtime.disclosures_transform import (
    BatchTransformResult,
    SkippedSession,
    transform_parse_sessions,
)

# Result type


@dataclass(frozen=True)
class DisclosuresBundleProcessResult:
    """Structured outcome of the full bundle-process pipeline run.

    stage_result            — artifact staging outcome (rows written to DB + local_root).
    parse_result            — raw counts and session objects from the parse step.
    transform_count         — number of DisclosureTransformResult objects produced.
    skipped_transform_count — parse-succeeded sessions that yielded no transform
                              (no ParseResult, or missing bioguide_id after resolution).
    skipped_sessions        — explicit SkippedSession records with per-session
                              reason codes; length equals skipped_transform_count.
    load_result             — provenance-tracked load outcome including LoadSummary.
    """

    stage_result: Any  # DisclosureStagingResult
    parse_result: DisclosureParseRuntimeResult
    transform_count: int
    skipped_transform_count: int
    skipped_sessions: tuple[SkippedSession, ...]
    load_result: DisclosuresLoadRuntimeResult


# Bundle validation


def _validate_bundle(bundle: Any) -> None:
    """Raise if bundle shape or semantic invariants are invalid."""
    if not hasattr(bundle, "artifacts"):
        raise TypeError(f"bundle must have an 'artifacts' attribute, got {type(bundle).__name__}")
    if isinstance(bundle, DisclosuresBundle):
        validate_disclosures_bundle(bundle)


# Parse input construction from staged bundle


def _build_parse_inputs(
    bundle: Any,
    stage_result: Any,
    local_root: Path | None,
) -> list[DisclosureParseInput]:
    """Build one DisclosureParseInput per staged artifact.

    For each artifact row in stage_result.artifact_rows:
    - locates the matching bundle entry by source_record_id
    - reads artifact bytes from disk via read_and_verify_entry when local_root
      is supplied, or directly from entry.storage_uri when it is already an
      absolute path
    - validates SHA-256 against the bundle entry's declared digest
    - constructs a DisclosureParseInput carrying the staged DB row, verified
      bytes, chamber, and source_record_id

    Artifact rows with no matching bundle entry are silently skipped (this
    can only happen if staging wrote rows not present in the bundle, which
    is not expected under normal operation).

    Raises Sha256Mismatch  if any on-disk digest does not match the bundle.
    Raises FileNotFoundError if a staged artifact file is absent.
    Raises ValueError if local_root is None and a bundle storage_uri is relative.
    """
    entry_by_id: dict[str, Any] = {entry.source_record_id: entry for entry in bundle.artifacts}
    inputs: list[DisclosureParseInput] = []
    for row in stage_result.artifact_rows:
        src_id = row.get("source_record_id", "")
        entry = entry_by_id.get(src_id)
        if entry is None:
            continue
        local_bytes = _read_entry_bytes(entry, local_root)
        inputs.append(
            DisclosureParseInput(
                artifact_row=row,
                local_bytes=local_bytes,
                chamber=row["chamber"],
                source_record_id=src_id,
            )
        )
    return inputs


def _read_entry_bytes(
    entry: Any,
    local_root: Path | None,
) -> bytes:
    if local_root is not None:
        return read_and_verify_entry(entry, local_root)

    entry_path = Path(entry.storage_uri)
    if not entry_path.is_absolute():
        raise ValueError("local_root is required to resolve relative bundle storage_uri values")
    if not entry_path.exists():
        raise FileNotFoundError(f"Bundle artifact not found: {entry_path}")

    data = entry_path.read_bytes()
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != entry.sha256:
        raise Sha256Mismatch(
            f"SHA-256 mismatch for {entry.storage_uri!r}: "
            f"expected {entry.sha256!r}, got {actual_sha256!r}"
        )
    return data


# Bundle-backed IndexMatchProvider


class _BundleIndexProvider:
    """IndexMatchProvider backed by the bundle's own index rows.

    load_matches is deterministic and makes no network calls: it resolves
    each artifact row against the lookup built from bundle.artifacts at
    construction time.

    load_member_rows delegates to the canonical DB query so member identity
    is resolved against the live member roster.
    """

    def __init__(self, bundle: Any) -> None:
        # (chamber, filing_year, source_record_id) -> index_row
        self._lookup: dict[tuple[str, int, str], Any] = {
            (entry.chamber, int(entry.filing_year), entry.source_record_id): entry.index_row
            for entry in bundle.artifacts
        }

    def load_matches(
        self,
        artifacts: list[dict[str, Any]],
    ) -> list[ArtifactIndexMatch]:
        """Return one ArtifactIndexMatch per artifact row, in the same order."""
        matches: list[ArtifactIndexMatch] = []
        for row in artifacts:
            chamber = row.get("chamber")
            year = row.get("filing_year")
            doc_id = row.get("source_record_id") or ""
            if chamber is None or year is None:
                matches.append(ArtifactIndexMatch(artifact=row, index_row=None))
                continue
            filing_year = filing_year_or_none(year)
            if filing_year is None:
                matches.append(ArtifactIndexMatch(artifact=row, index_row=None))
                continue
            key = (chamber, filing_year, doc_id)
            matches.append(ArtifactIndexMatch(artifact=row, index_row=self._lookup.get(key)))
        return matches

    def load_member_rows(
        self,
        conn: Any,
        *,
        chamber: str,
    ) -> list[dict[str, Any]]:
        """Return canonical member rows for the given chamber from the DB."""
        return fetch_member_rows_for_disclosures(conn, chamber=chamber)


# Public entry point


def run_disclosures_bundle_process(
    conn: Any,
    bundle: Any,
    *,
    local_root: Path | None = None,
    parse_inputs: list[DisclosureParseInput] | None = None,
) -> DisclosuresBundleProcessResult:
    """Stage a local disclosure bundle, parse, transform, and load.

    Steps:
      1. Validate    — bundle must expose an 'artifacts' attribute.
      2. Stage       — write each artifact into the DB source_artifact table
                       and (when local_root is supplied) to the local
                       filesystem.
      3. Parse inputs — build DisclosureParseInput objects for the parse step.
                        When parse_inputs is None and local_root is provided,
                        inputs are auto-built from staged artifact rows by
                        reading each artifact from disk and verifying its
                        SHA-256 digest (via read_and_verify_entry).
                        When parse_inputs is explicitly provided and local_root
                        is also set, SHA-256 is verified for every bundle entry
                        before parsing begins.
      4. Parse       — run a parse_run lifecycle over every staged artifact
                       using a bundle-backed IndexMatchProvider (resolves
                       index rows from the bundle; no live index fetches).
                       Per-artifact failures are absorbed; the batch continues.
      5. Transform   — convert each succeeded ParseSessionResult into a
                       DisclosureTransformResult ready for the load layer.
      6. Load        — execute the provenance-tracked FK-phased disclosure
                       load.

    Args:
        conn:         Open psycopg connection (or compatible test mock).
        bundle:       Pre-fetched artifact collection.  Must expose an
                      'artifacts' iterable whose entries carry chamber,
                      filing_year, source_record_id, index_row, sha256,
                      and storage_uri fields.
        local_root:   Filesystem root for artifact storage.  When supplied,
                      artifacts are expected under local_root / storage_uri.
                      SHA-256 integrity is always checked when local_root is
                      provided.
        parse_inputs: Explicit parse inputs passed directly to
                      run_disclosure_parse_runtime, bypassing the auto-build
                      from staged artifacts.  When None, inputs are built
                      automatically from staged artifact rows using the bundle's
                      own artifact locations.  Relative storage_uri values still
                      require local_root; absolute storage_uri values are read
                      directly.
    """
    # 1. validate
    _validate_bundle(bundle)

    # 2. stage
    stage_result = stage_disclosures_bundle(conn, bundle, local_root=local_root)

    # 3. parse inputs — build inputs and/or verify SHA-256 integrity
    if parse_inputs is None:
        # bundle-backed runs always build explicit parse inputs from the staged
        # bundle entries; there is no DB fallback on this path.
        parse_inputs = _build_parse_inputs(bundle, stage_result, local_root)
    elif local_root is not None:
        # explicit inputs provided; still verify every bundle entry's SHA-256
        for entry in bundle.artifacts:
            verify_entry_sha256(entry, local_root)

    # 4. build bundle-backed index provider
    index_provider = _BundleIndexProvider(bundle)

    # 5. parse with bundle-backed provider
    parse_result = run_disclosure_parse_runtime(
        conn,
        local_root=local_root,
        index_provider=index_provider,
        parse_inputs=parse_inputs,
    )

    # 6. transform
    transform_result: BatchTransformResult = transform_parse_sessions(
        conn, parse_result.parse_sessions
    )

    # 7. load
    load_result = run_disclosures_load_runtime(conn, transform_result.transformed)

    return DisclosuresBundleProcessResult(
        stage_result=stage_result,
        parse_result=parse_result,
        transform_count=len(transform_result.transformed),
        skipped_transform_count=len(transform_result.skipped),
        skipped_sessions=tuple(transform_result.skipped),
        load_result=load_result,
    )
