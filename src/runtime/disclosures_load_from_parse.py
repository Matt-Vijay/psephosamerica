"""Runtime parse-to-load path for disclosures.

Entry point: run_disclosures_parse_load_runtime.

Flow:
  1. run_disclosure_parse_runtime  — parse each unparsed disclosure artifact
  2. transform_parse_sessions      — build DisclosureTransformResult per succeeded session
  3. run_disclosures_load_runtime  — provenance-tracked FK-safe canonical load
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.query.disclosure_member_rows import fetch_member_rows_for_disclosures
from src.runtime.disclosures_index_provider import live_index_matches
from src.runtime.disclosures import (
    DisclosuresLoadRuntimeResult,
    run_disclosures_load_runtime,
)
from src.runtime.disclosures_parse import (
    DisclosureParseRuntimeResult,
    IndexMatchProvider,
    run_disclosure_parse_runtime,
)
from src.runtime.disclosures_transform import (
    BatchTransformResult,
    SkippedSession,
    transform_parse_sessions,
)


# ---------------------------------------------------------------------------
# Live index provider
# ---------------------------------------------------------------------------


class _LiveIndexProvider:
    """Live-backed index/membership provider for the non-bundle runtime path."""

    def load_matches(self, artifacts: list[dict[str, Any]]) -> list[Any]:
        return live_index_matches(artifacts)

    def load_member_rows(
        self,
        conn: Any,
        *,
        chamber: str,
    ) -> list[dict[str, Any]]:
        return fetch_member_rows_for_disclosures(conn, chamber=chamber)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisclosuresParseLoadResult:
    """Structured outcome of the full parse-to-load pipeline run.

    parse_result            — raw counts and session objects from the parse step.
    transform_count         — number of DisclosureTransformResult objects produced.
    skipped_transform_count — parse-succeeded sessions that yielded no transform
                              (no ParseResult, or missing bioguide_id after resolution).
    skipped_sessions        — explicit SkippedSession records with per-session
                              reason codes; length equals skipped_transform_count.
    load_result             — provenance-tracked load outcome including LoadSummary.
    """

    parse_result: DisclosureParseRuntimeResult
    transform_count: int
    skipped_transform_count: int
    skipped_sessions: tuple[SkippedSession, ...]
    load_result: DisclosuresLoadRuntimeResult


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_disclosures_parse_load_runtime(
    conn: Any,
    *,
    local_root: Path,
    chamber: str | None = None,
    limit: int | None = None,
    parser_name: str = "text_extract_v1",
    parser_version: str = "1",
) -> DisclosuresParseLoadResult:
    """Parse stored disclosure artifacts, transform, and load into canonical DB.

    Steps:
      1. Parse — run parse_run lifecycle over every unparsed artifact.
         Failed artifacts are absorbed; the batch continues.
      2. Transform — convert each succeeded ParseSessionResult into a
         DisclosureTransformResult ready for the load layer.
      3. Load — execute the provenance-tracked FK-phased disclosure load.

    Args:
        conn:           Open psycopg connection (or compatible test mock).
        local_root:     Filesystem root under which artifact bytes are stored.
        chamber:        ``'house'`` or ``'senate'``; None means both.
        limit:          Cap on artifacts to parse in this run; None means all.
        parser_name:    Identifier recorded in the parse_run row.
        parser_version: Version string recorded in the parse_run row.
    """
    index_provider: IndexMatchProvider = _LiveIndexProvider()

    parse_result = run_disclosure_parse_runtime(
        conn,
        local_root=local_root,
        chamber=chamber,
        limit=limit,
        parser_name=parser_name,
        parser_version=parser_version,
        index_provider=index_provider,
    )

    batch: BatchTransformResult = transform_parse_sessions(conn, parse_result.parse_sessions)

    load_result = run_disclosures_load_runtime(conn, batch.transformed)

    return DisclosuresParseLoadResult(
        parse_result=parse_result,
        transform_count=len(batch.transformed),
        skipped_transform_count=len(batch.skipped),
        skipped_sessions=tuple(batch.skipped),
        load_result=load_result,
    )
