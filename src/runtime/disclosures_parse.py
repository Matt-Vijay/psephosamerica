"""Runtime that drives disclosure parse runs over stored artifacts."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from src.parse.disclosures.classify import classify_pdf
from src.parse.disclosures.classify_runtime import build_heuristic_input
from src.parse.disclosures.header_fields import extract_header_fields
from src.parse.disclosures.models import Chamber, Filing, FilingType
from src.parse.disclosures.parser_dispatch import parse_disclosure_pages
from src.parse.disclosures.text_extract import extract_text_metrics, extract_text_pages
from src.parse.disclosures.text_lines import drop_empty, flatten_lines, pages_to_line_lists
from src.runtime.disclosures_index_rows import ArtifactIndexMatch
from src.runtime.disclosures_matches import resolve_artifact_member
from src.runtime.disclosures_parse_inputs import (
    DisclosureParseInput,
    load_unparsed_disclosure_artifacts,
)
from src.runtime.parse_runs import ParseSessionResult, run_parse_session

# Pure-function module aliases — patchable for test isolation.
_extract_pages = extract_text_pages
_extract_header_fields = extract_header_fields
_dispatch_parser = parse_disclosure_pages


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class IndexMatchProvider(Protocol):
    """Supply index matches and member rows to the parse runtime.

    Both bundle-backed and live-backed implementations must satisfy this
    protocol.  The runtime calls load_matches once per run (passing all
    artifact rows) then calls load_member_rows once per distinct chamber
    that has at least one resolved index row.
    """

    def load_matches(
        self,
        artifacts: list[dict[str, Any]],
    ) -> list[ArtifactIndexMatch]:
        """Return one ArtifactIndexMatch per artifact row, in the same order."""
        ...

    def load_member_rows(
        self,
        conn: Any,
        *,
        chamber: str,
    ) -> list[dict[str, Any]]:
        """Return canonical member rows for the given chamber from the DB."""
        ...


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisclosureParseRuntimeResult:
    processed_count: int
    succeeded_count: int
    failed_count: int
    parse_sessions: tuple[ParseSessionResult, ...]
    parsed_documents: tuple[Any, ...]  # one entry per succeeded artifact
    failed_artifact_ids: tuple[int, ...]  # artifact row IDs that raised during parse


def _all_lines(page_texts: list[str]) -> tuple[str, ...]:
    return drop_empty(flatten_lines(pages_to_line_lists(page_texts)))


def _dispatchable_filing_type(header_filing_type: FilingType | None) -> FilingType:
    if header_filing_type in (FilingType.PTR, FilingType.ANNUAL):
        return header_filing_type
    raise ValueError("could not determine underlying filing type from disclosure text")


def _provisional_filing(
    parse_input: DisclosureParseInput,
    *,
    filing_type: FilingType,
    filing_year: int,
    filed_at,
    amendment_number: int,
    is_amended: bool,
) -> Filing:
    return Filing(
        member_bioguide_id="",
        chamber=Chamber(parse_input.chamber),
        filing_year=filing_year,
        filing_type=filing_type,
        filed_at=filed_at,
        amendment_number=amendment_number,
        is_amended=is_amended,
        source_record_id=parse_input.source_record_id,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_disclosure_parse_runtime(
    conn: Any,
    *,
    local_root: Path,
    chamber: str | None = None,
    limit: int | None = None,
    parser_name: str = "text_extract_v1",
    parser_version: str = "1",
    index_provider: Optional[IndexMatchProvider] = None,
    parse_inputs: Optional[list[DisclosureParseInput]] = None,
) -> DisclosureParseRuntimeResult:
    """Run a parse_run lifecycle for each unparsed disclosure artifact.

    Steps for each artifact:
    1. Extract text metrics from the stored PDF bytes.
    2. Classify the document using heuristic rules.
    3. Dispatch to the document-type parser if available.
    4. Resolve the canonical member identity.
       - If index_provider is given, load_matches is called once with all
         artifact rows; load_member_rows is called once per distinct chamber
         that has at least one resolved index row.  Both bundle-backed and
         live-backed providers follow this same explicit path.
       - When no provider is given, resolved_member is left as None.
    5. Wrap the full pipeline in a parse_run open/close lifecycle.

    Per-artifact failures are isolated; one bad PDF does not stop the batch.

    When parse_inputs is provided the inputs are used directly and the DB
    query path (load_unparsed_disclosure_artifacts) is skipped entirely.
    When parse_inputs is None the DB query path is used.  There is no
    implicit fallback between the two paths.
    """
    if parse_inputs is not None:
        inputs: list[DisclosureParseInput] = parse_inputs
    else:
        inputs = load_unparsed_disclosure_artifacts(
            conn,
            local_root=local_root,
            chamber=chamber,
            limit=limit,
        )

    # Pre-build index lookup and member roster when a provider is supplied.
    # Only chambers with at least one resolved index row are fetched.
    _match_by_artifact_id: dict[int, ArtifactIndexMatch] = {}
    _member_rows_cache: dict[str, list] = {}
    if index_provider is not None:
        artifact_rows = [inp.artifact_row for inp in inputs]
        for m in index_provider.load_matches(artifact_rows):
            _match_by_artifact_id[m.artifact["id"]] = m
        chambers_needed: set[str] = {
            m.artifact["chamber"]
            for m in _match_by_artifact_id.values()
            if m.index_row is not None and m.artifact.get("chamber")
        }
        for ch in chambers_needed:
            _member_rows_cache[ch] = index_provider.load_member_rows(conn, chamber=ch)

    sessions: list[ParseSessionResult] = []
    failed_count = 0
    failed_artifact_ids: list[int] = []

    for parse_input in inputs:

        def _parse_fn(inp: DisclosureParseInput = parse_input) -> dict[str, Any]:
            page_texts = _extract_pages(inp.local_bytes)
            metrics = extract_text_metrics(inp.local_bytes)
            classify_result = classify_pdf(
                build_heuristic_input(
                    metrics,
                    Chamber(inp.chamber),
                    is_ptr=False,
                    is_amendment=False,
                )
            )
            parsed_document = None
            if _dispatch_parser is not None:
                header = _extract_header_fields(_all_lines(page_texts))
                filing_year = header.filing_year or inp.artifact_row.get("filing_year") or 0
                filing = _provisional_filing(
                    inp,
                    filing_type=_dispatchable_filing_type(header.filing_type),
                    filing_year=filing_year,
                    filed_at=header.filed_at,
                    amendment_number=header.amendment_number,
                    is_amended=header.is_amended,
                )
                classify_result = classify_pdf(
                    build_heuristic_input(
                        metrics,
                        Chamber(inp.chamber),
                        is_ptr=filing.filing_type is FilingType.PTR,
                        is_amendment=filing.is_amended,
                    )
                )
                parsed_document = _dispatch_parser(page_texts, filing)

            resolved_member = None
            match = _match_by_artifact_id.get(inp.artifact_row["id"])
            if match is not None and match.index_row is not None:
                idx_dict = dataclasses.asdict(match.index_row)
                resolved_member = resolve_artifact_member(
                    inp.artifact_row,
                    idx_dict,
                    _member_rows_cache,
                )

            return {
                "page_count": metrics.total_pages,
                "ocr_page_count": metrics.total_pages - metrics.text_pages if classify_result.needs_ocr else 0,
                "confidence_summary": {
                    "text_pages": metrics.text_pages,
                    "avg_chars_per_text_page": metrics.avg_chars_per_text_page,
                    "detected_headers": sorted(metrics.detected_headers),
                    "pdf_kind": classify_result.pdf_kind.value,
                    "needs_ocr": classify_result.needs_ocr,
                    "needs_review": classify_result.needs_review,
                    "review_triggers": [t.value for t in classify_result.review_triggers],
                },
                "parsed_document": parsed_document,
                "resolved_member": resolved_member,
                "source_artifact_id": inp.artifact_row["id"],
                "source_record_id": inp.source_record_id,
                "chamber": inp.chamber,
            }

        try:
            session = run_parse_session(
                conn,
                source_artifact_id=parse_input.artifact_row["id"],
                parser_name=parser_name,
                parser_version=parser_version,
                parse_fn=_parse_fn,
            )
            sessions.append(session)
        except Exception:  # noqa: BLE001
            failed_count += 1
            failed_artifact_ids.append(parse_input.artifact_row["id"])

    return DisclosureParseRuntimeResult(
        processed_count=len(sessions) + failed_count,
        succeeded_count=len(sessions),
        failed_count=failed_count,
        parse_sessions=tuple(sessions),
        parsed_documents=tuple(s.parse_result for s in sessions),
        failed_artifact_ids=tuple(failed_artifact_ids),
    )
