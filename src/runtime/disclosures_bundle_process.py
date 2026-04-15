"""Bundle-process path for local disclosure pipeline runs.

Entry point: run_disclosures_bundle_process.

Flow:
  1. validate bundle       — check bundle carries an 'artifacts' iterable
  2. stage bundle          — write artifact rows into DB + local_root
  3. resolve bundle files  — SHA-256 integrity check for each staged artifact
  4. build parse inputs    — construct bundle-backed IndexMatchProvider from
                             staged bundle index rows (no live fetches)
  5. parse                 — run_disclosure_parse_runtime with bundle provider
  6. transform             — transform_parse_sessions
  7. load                  — run_disclosures_load_runtime
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.query.disclosure_member_rows import fetch_member_rows_for_disclosures
from src.runtime.disclosures import (
    DisclosuresLoadRuntimeResult,
    run_disclosures_load_runtime,
)
from src.runtime.disclosures_bundle_files import verify_entry_sha256
from src.runtime.disclosures_index_rows import ArtifactIndexMatch
from src.runtime.disclosures_parse import (
    DisclosureParseRuntimeResult,
    run_disclosure_parse_runtime,
)
from src.runtime.disclosures_parse_inputs import DisclosureParseInput
from src.runtime.disclosures_stage import stage_disclosures_bundle
from src.runtime.disclosures_transform import (
    BatchTransformResult,
    transform_parse_sessions,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisclosuresBundleProcessResult:
    """Structured outcome of the full bundle-process pipeline run.

    stage_result    — artifact staging outcome (rows written to DB + local_root).
    parse_result    — raw counts and session objects from the parse step.
    transform_count — number of DisclosureTransformResult objects produced.
    load_result     — provenance-tracked load outcome including LoadSummary.
    """

    stage_result: Any  # DisclosureStagingResult
    parse_result: DisclosureParseRuntimeResult
    transform_count: int
    load_result: DisclosuresLoadRuntimeResult


# ---------------------------------------------------------------------------
# Bundle validation
# ---------------------------------------------------------------------------


def _validate_bundle(bundle: Any) -> None:
    """Raise TypeError if bundle does not expose an 'artifacts' attribute."""
    if not hasattr(bundle, "artifacts"):
        raise TypeError(
            f"bundle must have an 'artifacts' attribute, got {type(bundle).__name__}"
        )


# ---------------------------------------------------------------------------
# Bundle-backed IndexMatchProvider
# ---------------------------------------------------------------------------


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
            key = (chamber, int(year), doc_id)
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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_disclosures_bundle_process(
    conn: Any,
    bundle: Any,
    *,
    local_root: Path | None = None,
    parse_inputs: Optional[list[DisclosureParseInput]] = None,
) -> DisclosuresBundleProcessResult:
    """Stage a local disclosure bundle, parse, transform, and load.

    Steps:
      1. Validate — bundle must expose an 'artifacts' attribute.
      2. Stage    — write each artifact into the DB source_artifact table and
                    (when local_root is supplied) to the local filesystem.
      3. Resolve  — verify SHA-256 integrity for each bundle artifact on disk.
                    Skipped when local_root is None.
      4. Provide  — build a bundle-backed IndexMatchProvider so the parse step
                    resolves index rows exclusively from this bundle.
      5. Parse    — run a parse_run lifecycle over every staged artifact.
                    Per-artifact failures are absorbed; the batch continues.
      6. Transform — convert each succeeded ParseSessionResult into a
                     DisclosureTransformResult ready for the load layer.
      7. Load     — execute the provenance-tracked FK-phased disclosure load.

    Args:
        conn:         Open psycopg connection (or compatible test mock).
        bundle:       Pre-fetched artifact collection.  Must expose an
                      'artifacts' iterable whose entries carry chamber,
                      filing_year, source_record_id, index_row, sha256,
                      and storage_uri fields.
        local_root:   Filesystem root for artifact storage.  When supplied,
                      artifacts are written here during staging and their
                      SHA-256 digests are verified before parsing begins.
        parse_inputs: Explicit parse inputs passed directly to
                      run_disclosure_parse_runtime, bypassing the DB artifact
                      query.  When None the runtime queries the DB for
                      unparsed artifacts.  Useful for deterministic E2E tests
                      and local oracle runs where artifact rows are known in
                      advance.
    """
    # 1. validate
    _validate_bundle(bundle)

    # 2. stage
    stage_result = stage_disclosures_bundle(conn, bundle, local_root=local_root)

    # 3. resolve bundle files — SHA-256 integrity before parse begins
    if local_root is not None:
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
        load_result=load_result,
    )
