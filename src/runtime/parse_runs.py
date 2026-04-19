"""Wrapper that drives the parse_run lifecycle for one artifact.

Entry point: run_parse_session.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from src.provenance.artifacts import create_parse_run, fail_parse_run, finish_parse_run


class ParseResult(Protocol):
    """Minimum contract parse_fn must satisfy."""

    page_count: int | None
    ocr_page_count: int | None
    confidence_summary: dict[str, Any]


@dataclass(frozen=True)
class ParseSessionResult:
    run_id: int
    parse_result: ParseResult | dict[str, Any]  # full object returned by parse_fn


def run_parse_session(
    conn: Any,
    *,
    source_artifact_id: int,
    parser_name: str,
    parser_version: str,
    parse_fn: Callable[[], ParseResult | dict[str, Any]],
    ingestion_run_id: int | None = None,
) -> ParseSessionResult:
    """Create a parse_run, invoke parse_fn, then close the run.

    parse_fn must return an object (or dict) with at least:
      page_count, ocr_page_count, confidence_summary.
    Any additional payload survives inside ParseSessionResult.parse_result.

    Raises whatever parse_fn raises, after marking the run failed.
    """
    run_id = create_parse_run(
        conn,
        source_artifact_id,
        parser_name,
        parser_version,
        ingestion_run_id=ingestion_run_id,
    )

    try:
        result = parse_fn()
    except Exception as exc:
        fail_parse_run(conn, run_id, str(exc))
        raise

    if isinstance(result, dict):
        page_count = result.get("page_count")
        ocr_page_count = result.get("ocr_page_count")
        confidence_summary = result.get("confidence_summary")
    else:
        page_count = getattr(result, "page_count", None)
        ocr_page_count = getattr(result, "ocr_page_count", None)
        confidence_summary = getattr(result, "confidence_summary", None)

    finish_parse_run(
        conn,
        run_id,
        page_count=page_count,
        ocr_page_count=ocr_page_count,
        confidence_summary=confidence_summary,
    )

    return ParseSessionResult(run_id=run_id, parse_result=result)
