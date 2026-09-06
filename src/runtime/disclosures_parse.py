"""Runtime that drives disclosure parse runs over stored artifacts."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from src.parse.disclosures.classify import classify_pdf
from src.parse.disclosures.classify_runtime import build_heuristic_input
from src.parse.disclosures.header_fields import HeaderFields, extract_header_fields
from src.parse.disclosures.models import Chamber, Filing, FilingType
from src.parse.disclosures.parse_result import ParseResult
from src.parse.disclosures.parser_dispatch import parse_disclosure_pages
from src.parse.disclosures.text_extract import extract_text_metrics, extract_text_pages
from src.parse.disclosures.text_lines import drop_empty, flatten_lines, pages_to_line_lists
from src.runtime.disclosures_index_rows import ArtifactIndexMatch
from src.runtime.disclosures_matches import MemberLookup, resolve_artifact_member
from src.runtime.disclosures_parse_inputs import (
    DisclosureParseInput,
    load_unparsed_disclosure_artifacts,
)
from src.runtime.parse_runs import ParseSessionResult, run_parse_session

# Pure-function module aliases — patchable for test isolation.
_extract_pages = extract_text_pages
_extract_header_fields = extract_header_fields
_dispatch_parser = parse_disclosure_pages

SKIP_OCR_REQUIRED_NOT_IMPLEMENTED = "ocr_required_not_implemented"


# Provider protocol


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


# Result type


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


def _index_field(index_row: Any, field: str) -> Any:
    if index_row is None:
        return None
    return getattr(index_row, field, None)


def _index_text(index_row: Any, field: str) -> str | None:
    value = _index_field(index_row, field)
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    text = str(value).strip()
    return text or None


def _index_filing_type(index_row: Any) -> FilingType | None:
    filing_kind = _index_text(index_row, "filing_kind")
    if filing_kind == FilingType.PTR.value:
        return FilingType.PTR
    if filing_kind == FilingType.ANNUAL.value:
        return FilingType.ANNUAL

    raw_filing_type = _index_text(index_row, "raw_filing_type")
    if raw_filing_type is not None:
        raw = raw_filing_type.upper()
        if raw == "P":
            return FilingType.PTR
        if raw in {"O", "T"}:
            return FilingType.ANNUAL

    report_type = _index_text(index_row, "report_type")
    if report_type is None:
        return None
    lower = report_type.lower()
    if "periodic transaction report" in lower or re.search(r"\bptr\b", lower):
        return FilingType.PTR
    if "annual" in lower:
        return FilingType.ANNUAL
    return None


def _index_is_amended(index_row: Any) -> bool | None:
    raw_filing_type = _index_text(index_row, "raw_filing_type")
    if raw_filing_type is not None:
        return raw_filing_type.upper() == "A"

    report_type = _index_text(index_row, "report_type")
    if report_type is None:
        return None
    lower = report_type.lower()
    if "amend" in lower:
        return True
    if "annual" in lower or "periodic transaction report" in lower or re.search(r"\bptr\b", lower):
        return False
    return None


def _index_filed_at(index_row: Any) -> date | None:
    raw = _index_field(index_row, "filing_date")
    if isinstance(raw, date):
        return raw
    text = _index_text(index_row, "filing_date") or _index_text(index_row, "date_filed")
    if text is None:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _index_filing_year(index_row: Any) -> int | None:
    for field in ("year", "filing_year"):
        value = _index_field(index_row, field)
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _filing_from_header_and_index(
    parse_input: DisclosureParseInput,
    *,
    header: HeaderFields,
    index_row: Any,
) -> Filing:
    official_filing_type = _index_filing_type(index_row)
    official_is_amended = _index_is_amended(index_row)
    official_filing_year = _index_filing_year(index_row)
    official_filed_at = _index_filed_at(index_row)
    filing_type = official_filing_type or _dispatchable_filing_type(header.filing_type)
    is_amended = header.is_amended if official_is_amended is None else official_is_amended
    return _provisional_filing(
        parse_input,
        filing_type=filing_type,
        filing_year=(
            official_filing_year
            or parse_input.artifact_row.get("filing_year")
            or header.filing_year
            or 0
        ),
        filed_at=official_filed_at or header.filed_at,
        amendment_number=header.amendment_number if is_amended else 0,
        is_amended=is_amended,
    )


def _canonicalize_parsed_document(parsed_document: Any, filing: Filing) -> Any:
    if not isinstance(parsed_document, ParseResult):
        return parsed_document
    return dataclasses.replace(parsed_document, filing=filing)


def _provisional_filing(
    parse_input: DisclosureParseInput,
    *,
    filing_type: FilingType,
    filing_year: int,
    filed_at: date | None,
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


# Public entry point


def run_disclosure_parse_runtime(
    conn: Any,
    *,
    local_root: Path | None,
    chamber: str | None = None,
    limit: int | None = None,
    parser_name: str = "text_extract_v1",
    parser_version: str = "1",
    index_provider: IndexMatchProvider | None = None,
    parse_inputs: list[DisclosureParseInput] | None = None,
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
        if local_root is None:
            raise ValueError("local_root is required when parse_inputs is not provided")
        inputs = load_unparsed_disclosure_artifacts(
            conn,
            local_root=local_root,
            chamber=chamber,
            limit=limit,
        )

    # Pre-build index lookup and member roster when a provider is supplied.
    # Only chambers with at least one resolved index row are fetched.
    _match_by_artifact_id: dict[int, ArtifactIndexMatch] = {}
    _member_rows_cache: MemberLookup = {}
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
            match = _match_by_artifact_id.get(inp.artifact_row["id"])
            index_row = None if match is None else match.index_row
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
            skip_reason_code = None
            if classify_result.needs_ocr:
                skip_reason_code = SKIP_OCR_REQUIRED_NOT_IMPLEMENTED
            elif _dispatch_parser is not None:
                header = _extract_header_fields(_all_lines(page_texts))
                filing = _filing_from_header_and_index(
                    inp,
                    header=header,
                    index_row=index_row,
                )
                classify_result = classify_pdf(
                    build_heuristic_input(
                        metrics,
                        Chamber(inp.chamber),
                        is_ptr=filing.filing_type is FilingType.PTR,
                        is_amendment=filing.is_amended,
                    )
                )
                parsed_document = _canonicalize_parsed_document(
                    _dispatch_parser(page_texts, filing),
                    filing,
                )

            resolved_member = None
            if match is not None and match.index_row is not None:
                idx_dict: dict[str, Any] = dataclasses.asdict(match.index_row)
                resolved_member = resolve_artifact_member(
                    inp.artifact_row,
                    idx_dict,
                    _member_rows_cache,
                )

            return {
                "page_count": metrics.total_pages,
                "ocr_page_count": metrics.total_pages - metrics.text_pages
                if classify_result.needs_ocr
                else 0,
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
                "skip_reason_code": skip_reason_code,
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
