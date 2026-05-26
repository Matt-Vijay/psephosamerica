"""Bridge parsed disclosures into canonical transform results."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional, Sequence

from src.parse.disclosures.models import Filing, Holding, OutsidePosition, Transaction
from src.parse.disclosures.parse_result import ParseResult
from src.parse.disclosures.transform import (
    DisclosureTransformResult,
    ParseContext,
    transform_filing,
)

# ---------------------------------------------------------------------------
# Stable skip-reason constants (used by SkippedSession.reason_code)
# ---------------------------------------------------------------------------

SKIP_NO_PARSE_RESULT = "no_parse_result"
SKIP_NO_PARSED_DOCUMENT = "no_parsed_document"
SKIP_UNRESOLVED_MEMBER_IDENTITY = "unresolved_member_identity"
SKIP_OCR_REQUIRED_NOT_IMPLEMENTED = "ocr_required_not_implemented"


def build_parse_context(
    *,
    parse_run_id: Optional[int] = None,
    source_artifact_id: Optional[int] = None,
    ingestion_run_id: Optional[int] = None,
) -> ParseContext:
    """Construct a ParseContext from runtime provenance IDs.

    All IDs are optional; callers pass None in dry-run or test scenarios
    where rows have not yet been persisted.
    """
    return ParseContext(
        parse_run_id=parse_run_id,
        source_artifact_id=source_artifact_id,
        ingestion_run_id=ingestion_run_id,
    )


def transform_parsed_disclosure(
    filing: Filing,
    holdings: list[Holding],
    transactions: list[Transaction],
    outside_positions: list[OutsidePosition],
    *,
    parse_run_id: Optional[int] = None,
    source_artifact_id: Optional[int] = None,
    ingestion_run_id: Optional[int] = None,
) -> DisclosureTransformResult:
    ctx = build_parse_context(
        parse_run_id=parse_run_id,
        source_artifact_id=source_artifact_id,
        ingestion_run_id=ingestion_run_id,
    )
    return transform_filing(filing, holdings, transactions, outside_positions, ctx)


def transform_single_session(
    session: Any,
) -> DisclosureTransformResult | SkippedSession:
    """Transform a single parse session into a canonical result or a skip record.

    This is the single-document counterpart to ``transform_parse_sessions``.
    Returns a ``DisclosureTransformResult`` on success or a ``SkippedSession``
    when the session cannot be transformed (missing parse result, unresolved
    member identity, etc.).

    Callers that need to process a batch should prefer ``transform_parse_sessions``
    to avoid re-implementing the skip-reason logic.
    """
    run_id = getattr(session, "run_id", None)
    parse_result = getattr(session, "parse_result", None)

    if not isinstance(parse_result, dict):
        return SkippedSession(run_id=run_id, reason_code=SKIP_NO_PARSE_RESULT)

    skip_reason_code = parse_result.get("skip_reason_code")
    if isinstance(skip_reason_code, str) and skip_reason_code:
        return SkippedSession(run_id=run_id, reason_code=skip_reason_code)

    raw_document = parse_result.get("parsed_document")
    if not isinstance(raw_document, ParseResult):
        return SkippedSession(run_id=run_id, reason_code=SKIP_NO_PARSED_DOCUMENT)

    resolved = _resolve_parsed_document(parse_result)
    if resolved is None:
        return SkippedSession(run_id=run_id, reason_code=SKIP_UNRESOLVED_MEMBER_IDENTITY)

    return transform_parsed_disclosure(
        resolved.filing,
        list(resolved.holdings),
        list(resolved.transactions),
        list(resolved.outside_positions),
        parse_run_id=run_id,
        source_artifact_id=parse_result.get("source_artifact_id"),
        ingestion_run_id=parse_result.get("ingestion_run_id"),
    )


# ---------------------------------------------------------------------------
# Batch transform types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkippedSession:
    """A parse session that could not be transformed.

    reason_code is one of the module-level SKIP_* constants:
    - SKIP_NO_PARSE_RESULT            — session.parse_result is not a dict
    - SKIP_OCR_REQUIRED_NOT_IMPLEMENTED
                                      — parse step explicitly blocked on OCR
    - SKIP_NO_PARSED_DOCUMENT         — parse_result['parsed_document'] is not a ParseResult
    - SKIP_UNRESOLVED_MEMBER_IDENTITY — member bioguide_id absent and unresolvable
    """

    run_id: Optional[int]
    reason_code: str


@dataclass(frozen=True)
class BatchTransformResult:
    """Outcome of a batch transform over multiple parse sessions.

    transformed: sessions that produced a DisclosureTransformResult.
    skipped:     sessions that could not be transformed, each with a reason_code.
    """

    transformed: list[DisclosureTransformResult]
    skipped: list[SkippedSession]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolved_bioguide_id(resolution: Any) -> str | None:
    bioguide_id = getattr(resolution, "bioguide_id", None)
    if isinstance(bioguide_id, str) and bioguide_id:
        return bioguide_id
    if isinstance(resolution, dict):
        dict_id = resolution.get("bioguide_id")
        if isinstance(dict_id, str) and dict_id:
            return dict_id
    return None


def _resolve_parsed_document(parse_result: dict[str, Any]) -> ParseResult | None:
    """Return a ParseResult with a guaranteed member_bioguide_id, or None.

    Applies resolved_member identity when the parser left the field blank.
    Returns None if member identity cannot be established.
    """
    parsed_document = parse_result.get("parsed_document")
    if not isinstance(parsed_document, ParseResult):
        return None
    if parsed_document.filing.member_bioguide_id:
        return parsed_document
    bioguide_id = _resolved_bioguide_id(parse_result.get("resolved_member"))
    if bioguide_id is None:
        return None
    return replace(
        parsed_document,
        filing=replace(parsed_document.filing, member_bioguide_id=bioguide_id),
    )


# ---------------------------------------------------------------------------
# Batch transform
# ---------------------------------------------------------------------------


def transform_parse_sessions(
    _conn: Any,
    parse_sessions: Sequence[Any],
) -> BatchTransformResult:
    """Transform a batch of parse sessions into canonical payloads.

    Returns a BatchTransformResult separating successfully transformed sessions
    from skipped ones.  Callers can inspect skipped entries to drive review-queue
    routing or logging without re-scanning the raw session list.

    Delegates to ``transform_single_session`` per session so that skip-reason
    logic has a single source of truth.
    """
    transformed: list[DisclosureTransformResult] = []
    skipped: list[SkippedSession] = []

    for session in parse_sessions:
        result = transform_single_session(session)
        if isinstance(result, SkippedSession):
            skipped.append(result)
        else:
            transformed.append(result)

    return BatchTransformResult(transformed=transformed, skipped=skipped)
