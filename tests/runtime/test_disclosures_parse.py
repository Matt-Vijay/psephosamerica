"""Tests for src/runtime/disclosures_parse.py.

No live DB, no network, no filesystem reads.  All external boundaries are
mocked: load_unparsed_disclosure_artifacts, extract_text_metrics, and
run_parse_session.

classify_pdf and build_heuristic_input are exercised with the mocked metrics
returned by the _EXTRACT patch — both are pure functions and require no I/O.

_dispatch_parser is a module-level callable; tests patch it directly on the
module under test.

Index-driven resolution tests inject a _FakeIndexProvider that satisfies the
IndexMatchProvider protocol without touching _resolve_artifact_member or any
other module-level delegate.
"""
from __future__ import annotations

from datetime import date
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.parse.disclosures.header_fields import HeaderFields
from src.parse.disclosures.house_index import HouseFilingKind, HouseIndexRow
from src.parse.disclosures.models import Chamber, Filing, FilingType
from src.parse.disclosures.parse_result import ParseResult, ParserMeta
from src.parse.disclosures.senate_index import SenateIndexRow
from src.runtime.disclosures_index_rows import ArtifactIndexMatch
from src.runtime.disclosures_parse import (
    DisclosureParseRuntimeResult,
    IndexMatchProvider,
    run_disclosure_parse_runtime,
)

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_LOAD = "src.runtime.disclosures_parse.load_unparsed_disclosure_artifacts"
_PAGES = "src.runtime.disclosures_parse._extract_pages"
_EXTRACT = "src.runtime.disclosures_parse.extract_text_metrics"
_HEADER = "src.runtime.disclosures_parse._extract_header_fields"
_RUN_SESSION = "src.runtime.disclosures_parse.run_parse_session"
_DISPATCH = "src.runtime.disclosures_parse._dispatch_parser"
_RESOLVE_ARTIFACT = "src.runtime.disclosures_parse.resolve_artifact_member"

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_LOCAL_ROOT = Path("/tmp/test_artifacts")


@dataclass
class _FakeParseInput:
    chamber: str
    source_record_id: str
    artifact_row: dict[str, Any]
    local_bytes: bytes


def _make_input(artifact_id: int, chamber: str = "senate") -> _FakeParseInput:
    return _FakeParseInput(
        chamber=chamber,
        source_record_id=f"DOC{artifact_id}",
        artifact_row={
            "id": artifact_id,
            "chamber": chamber,
            "storage_uri": f"disclosures/{chamber}/DOC{artifact_id}.pdf",
        },
        local_bytes=b"%PDF fake",
    )


def _make_session(run_id: int) -> MagicMock:
    session = MagicMock()
    session.run_id = run_id
    return session


def _make_metrics(
    total_pages: int = 3,
    text_pages: int = 3,
    avg_chars: float = 500.0,
    headers: frozenset | None = None,
) -> MagicMock:
    m = MagicMock()
    m.total_pages = total_pages
    m.text_pages = text_pages
    m.avg_chars_per_text_page = avg_chars
    m.detected_headers = headers if headers is not None else frozenset({"schedule a"})
    return m


def _make_header(
    *,
    filing_type: FilingType = FilingType.ANNUAL,
    filing_year: int = 2024,
) -> HeaderFields:
    return HeaderFields(
        member_name="Jane Doe",
        chamber=None,
        filing_type=filing_type,
        filing_year=filing_year,
        filed_at=None,
        amendment_number=0,
        is_amended=False,
    )


def _make_senate_index_row(doc_id: str = "DOC1", year: int = 2024) -> SenateIndexRow:
    return SenateIndexRow(
        first_name="Jane",
        last_name="Smith",
        office="Senator, TX",
        report_type="Annual Report for CY2024",
        date_filed="01/15/2025",
        doc_id=doc_id,
        filing_year=year,
    )


def _make_house_index_row(
    doc_id: str = "DOC1",
    *,
    filing_kind: HouseFilingKind = HouseFilingKind.ANNUAL,
    raw_filing_type: str = "O",
    year: int = 2024,
) -> HouseIndexRow:
    return HouseIndexRow(
        last_name="Smith",
        first_name="Jane",
        suffix="",
        raw_filing_type=raw_filing_type,
        state_dst="CA08",
        year=year,
        filing_date=date(year, 3, 15),
        doc_id=doc_id,
        filing_kind=filing_kind,
    )


# ---------------------------------------------------------------------------
# Fake IndexMatchProvider
# ---------------------------------------------------------------------------


class _FakeIndexProvider:
    """Satisfies IndexMatchProvider without DB or network access.

    Tracks how many times load_member_rows is called and which chambers
    were requested, so tests can assert on provider interaction.
    """

    def __init__(
        self,
        matches: list[ArtifactIndexMatch],
        member_rows: dict[str, list] | None = None,
        member_rows_side_effect: list[Exception] | None = None,
    ) -> None:
        self._matches = matches
        self._member_rows = member_rows or {}
        self._member_rows_side_effect = list(member_rows_side_effect or [])
        self.load_matches_calls: list[list[dict]] = []
        self.load_member_rows_calls: list[str] = []

    def load_matches(self, artifacts: list[dict[str, Any]]) -> list[ArtifactIndexMatch]:
        self.load_matches_calls.append(list(artifacts))
        return self._matches

    def load_member_rows(self, conn: Any, *, chamber: str) -> list[dict[str, Any]]:
        self.load_member_rows_calls.append(chamber)
        if self._member_rows_side_effect:
            raise self._member_rows_side_effect.pop(0)
        return self._member_rows.get(chamber, [])


@pytest.fixture(autouse=True)
def _default_parse_stage_patches():
    with (
        patch(_PAGES, return_value=["Annual Financial Disclosure Report"]),
        patch(_HEADER, return_value=_make_header()),
        patch(_DISPATCH, None),
    ):
        yield


# ---------------------------------------------------------------------------
# Happy-path context manager
# ---------------------------------------------------------------------------


def _patch_all(
    inputs=None,
    session_result=None,
    metrics=None,
):
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        with (
            patch(_LOAD, return_value=inputs if inputs is not None else []) as mock_load,
            patch(_EXTRACT, return_value=metrics or _make_metrics()) as mock_extract,
            patch(_RUN_SESSION, return_value=session_result or _make_session(1)) as mock_run,
        ):
            yield {"load": mock_load, "extract": mock_extract, "run": mock_run}

    return _ctx()


def _capturing_run_session() -> tuple[Any, list]:
    """Return a (side_effect, results) pair that invokes parse_fn and collects output."""
    results: list[Any] = []

    def _side_effect(conn, *, source_artifact_id, parser_name, parser_version, parse_fn):
        results.append(parse_fn())
        return _make_session(source_artifact_id)

    return _side_effect, results


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_returns_typed_result(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert isinstance(result, DisclosureParseRuntimeResult)

    def test_empty_inputs_yields_zero_counts(self):
        conn = MagicMock()
        with _patch_all(inputs=[]):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.processed_count == 0
        assert result.succeeded_count == 0
        assert result.failed_count == 0
        assert result.parse_sessions == ()

    def test_parse_sessions_is_tuple(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert isinstance(result.parse_sessions, tuple)

    def test_parsed_documents_is_tuple(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert isinstance(result.parsed_documents, tuple)

    def test_empty_inputs_yields_empty_parsed_documents(self):
        conn = MagicMock()
        with _patch_all(inputs=[]):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.parsed_documents == ()


# ---------------------------------------------------------------------------
# Count tracking
# ---------------------------------------------------------------------------


class TestCountTracking:
    def test_one_success_increments_succeeded(self):
        conn = MagicMock()
        inp = _make_input(1)
        with _patch_all(inputs=[inp], session_result=_make_session(10)):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.succeeded_count == 1
        assert result.failed_count == 0
        assert result.processed_count == 1

    def test_two_successes(self):
        conn = MagicMock()
        inputs = [_make_input(1), _make_input(2)]
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=[_make_session(10), _make_session(11)]),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.succeeded_count == 2
        assert result.processed_count == 2

    def test_one_failure_increments_failed(self):
        conn = MagicMock()
        inp = _make_input(1)
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=RuntimeError("parse failed")),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.failed_count == 1
        assert result.succeeded_count == 0
        assert result.processed_count == 1

    def test_mixed_success_and_failure(self):
        conn = MagicMock()
        inputs = [_make_input(1), _make_input(2), _make_input(3)]
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _RUN_SESSION,
                side_effect=[_make_session(10), RuntimeError("bad pdf"), _make_session(12)],
            ),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.succeeded_count == 2
        assert result.failed_count == 1
        assert result.processed_count == 3

    def test_processed_equals_succeeded_plus_failed(self):
        conn = MagicMock()
        inputs = [_make_input(i) for i in range(4)]
        session_or_error = [
            _make_session(i) if i % 2 == 0 else RuntimeError("bad")
            for i in range(4)
        ]
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=session_or_error),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.processed_count == result.succeeded_count + result.failed_count

    def test_failed_artifact_does_not_stop_batch(self):
        conn = MagicMock()
        inputs = [_make_input(1), _make_input(2)]
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _RUN_SESSION,
                side_effect=[ValueError("corrupt pdf"), _make_session(20)],
            ),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.succeeded_count == 1
        assert result.failed_count == 1


# ---------------------------------------------------------------------------
# parse_sessions content
# ---------------------------------------------------------------------------


class TestParseSessionsContent:
    def test_parse_sessions_contains_succeeded_sessions(self):
        conn = MagicMock()
        inp = _make_input(1)
        session = _make_session(99)
        with _patch_all(inputs=[inp], session_result=session):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.parse_sessions == (session,)

    def test_failed_sessions_not_in_parse_sessions(self):
        conn = MagicMock()
        inputs = [_make_input(1), _make_input(2)]
        good_session = _make_session(10)
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=[RuntimeError("bad"), good_session]),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert len(result.parse_sessions) == 1
        assert result.parse_sessions[0] is good_session

    def test_session_order_matches_input_order(self):
        conn = MagicMock()
        inputs = [_make_input(1), _make_input(2), _make_input(3)]
        s1, s2, s3 = _make_session(1), _make_session(2), _make_session(3)
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=[s1, s2, s3]),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.parse_sessions == (s1, s2, s3)


# ---------------------------------------------------------------------------
# parsed_documents content
# ---------------------------------------------------------------------------


class TestParsedDocuments:
    def test_parsed_documents_length_equals_succeeded_count(self):
        conn = MagicMock()
        inputs = [_make_input(1), _make_input(2)]
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=[_make_session(1), _make_session(2)]),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert len(result.parsed_documents) == result.succeeded_count

    def test_parsed_documents_mirrors_session_parse_results(self):
        """parsed_documents[i] is the same object as parse_sessions[i].parse_result."""
        conn = MagicMock()
        inp = _make_input(1)

        session = _make_session(10)
        payload = {"page_count": 3, "ocr_page_count": 0, "confidence_summary": {}}
        session.parse_result = payload

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=session),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)

        assert result.parsed_documents[0] is payload

    def test_failed_artifacts_not_in_parsed_documents(self):
        conn = MagicMock()
        inputs = [_make_input(1), _make_input(2)]
        good_session = _make_session(20)
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=[RuntimeError("bad"), good_session]),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert len(result.parsed_documents) == 1
        assert result.parsed_documents[0] is good_session.parse_result

    def test_empty_inputs_yields_empty_parsed_documents(self):
        conn = MagicMock()
        with _patch_all(inputs=[]):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.parsed_documents == ()


# ---------------------------------------------------------------------------
# Argument passing to load_unparsed_disclosure_artifacts
# ---------------------------------------------------------------------------


class TestLoadWiring:
    def test_conn_forwarded_to_load(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert mocks["load"].call_args[0][0] is conn

    def test_local_root_forwarded_to_load(self):
        conn = MagicMock()
        root = Path("/data/artifacts")
        with _patch_all() as mocks:
            run_disclosure_parse_runtime(conn, local_root=root)
        _, kwargs = mocks["load"].call_args
        assert kwargs["local_root"] == root

    def test_chamber_forwarded_to_load(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT, chamber="house")
        _, kwargs = mocks["load"].call_args
        assert kwargs["chamber"] == "house"

    def test_chamber_defaults_to_none(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        _, kwargs = mocks["load"].call_args
        assert kwargs.get("chamber") is None

    def test_limit_forwarded_to_load(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT, limit=10)
        _, kwargs = mocks["load"].call_args
        assert kwargs["limit"] == 10

    def test_limit_defaults_to_none(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        _, kwargs = mocks["load"].call_args
        assert kwargs.get("limit") is None


# ---------------------------------------------------------------------------
# Argument passing to run_parse_session
# ---------------------------------------------------------------------------


class TestParseSessionWiring:
    def test_source_artifact_id_from_artifact_row(self):
        conn = MagicMock()
        inp = _make_input(artifact_id=42)
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)) as mock_run,
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        _, kwargs = mock_run.call_args
        assert kwargs["source_artifact_id"] == 42

    def test_parser_name_forwarded(self):
        conn = MagicMock()
        inp = _make_input(1)
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)) as mock_run,
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, parser_name="house_ocr_v2"
            )
        _, kwargs = mock_run.call_args
        assert kwargs["parser_name"] == "house_ocr_v2"

    def test_parser_version_forwarded(self):
        conn = MagicMock()
        inp = _make_input(1)
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)) as mock_run,
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, parser_version="2"
            )
        _, kwargs = mock_run.call_args
        assert kwargs["parser_version"] == "2"

    def test_parser_name_default(self):
        conn = MagicMock()
        inp = _make_input(1)
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)) as mock_run,
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        _, kwargs = mock_run.call_args
        assert kwargs["parser_name"] == "text_extract_v1"

    def test_parser_version_default(self):
        conn = MagicMock()
        inp = _make_input(1)
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)) as mock_run,
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        _, kwargs = mock_run.call_args
        assert kwargs["parser_version"] == "1"

    def test_run_session_called_once_per_input(self):
        conn = MagicMock()
        inputs = [_make_input(1), _make_input(2), _make_input(3)]
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _RUN_SESSION,
                side_effect=[_make_session(1), _make_session(2), _make_session(3)],
            ) as mock_run,
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert mock_run.call_count == 3

    def test_conn_forwarded_to_run_session(self):
        conn = MagicMock()
        inp = _make_input(1)
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)) as mock_run,
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert mock_run.call_args[0][0] is conn


# ---------------------------------------------------------------------------
# parse_fn behaviour — extract_text_metrics is called inside run_parse_session
# ---------------------------------------------------------------------------


class TestParseFnBehaviour:
    def test_extract_called_with_artifact_bytes(self):
        conn = MagicMock()
        inp = _make_input(7)
        side_effect, _ = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()) as mock_extract,
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        mock_extract.assert_called_once_with(inp.local_bytes)

    def test_parse_fn_maps_total_pages_to_page_count(self):
        conn = MagicMock()
        inp = _make_input(7)
        metrics = _make_metrics(total_pages=5)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=metrics),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert results[0]["page_count"] == 5

    def test_parse_fn_returns_ocr_page_count(self):
        conn = MagicMock()
        inp = _make_input(7)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert "ocr_page_count" in results[0]

    def test_parse_fn_returns_confidence_summary(self):
        conn = MagicMock()
        inp = _make_input(7)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert "confidence_summary" in results[0]
        assert results[0]["confidence_summary"] is not None

    def test_each_artifact_parse_fn_uses_its_own_bytes(self):
        """Each closure captures its own parse_input; bytes are not mixed."""
        conn = MagicMock()
        inp1 = _FakeParseInput(
            chamber="senate",
            source_record_id="DOC1",
            artifact_row={"id": 1, "chamber": "senate", "storage_uri": "a.pdf"},
            local_bytes=b"bytes_for_1",
        )
        inp2 = _FakeParseInput(
            chamber="house",
            source_record_id="DOC2",
            artifact_row={"id": 2, "chamber": "house", "storage_uri": "b.pdf"},
            local_bytes=b"bytes_for_2",
        )
        captured_bytes: list[bytes] = []

        def _side_effect(conn, *, source_artifact_id, parser_name, parser_version, parse_fn):
            parse_fn()
            return _make_session(source_artifact_id)

        with (
            patch(_LOAD, return_value=[inp1, inp2]),
            patch(
                _EXTRACT,
                side_effect=lambda data: captured_bytes.append(data) or _make_metrics(),
            ),
            patch(_RUN_SESSION, side_effect=_side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)

        assert captured_bytes[0] == b"bytes_for_1"
        assert captured_bytes[1] == b"bytes_for_2"

    def test_parse_fn_returns_parsed_document_key(self):
        conn = MagicMock()
        inp = _make_input(7)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert "parsed_document" in results[0]

    def test_parse_fn_returns_resolved_member_key(self):
        conn = MagicMock()
        inp = _make_input(7)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert "resolved_member" in results[0]


# ---------------------------------------------------------------------------
# Classify integration — classify_pdf runs on the mocked metrics
# ---------------------------------------------------------------------------


class TestClassifyIntegration:
    def test_confidence_summary_includes_pdf_kind(self):
        conn = MagicMock()
        inp = _make_input(1)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert "pdf_kind" in results[0]["confidence_summary"]

    def test_confidence_summary_includes_needs_ocr(self):
        conn = MagicMock()
        inp = _make_input(1)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert "needs_ocr" in results[0]["confidence_summary"]

    def test_confidence_summary_includes_review_triggers(self):
        conn = MagicMock()
        inp = _make_input(1)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert "review_triggers" in results[0]["confidence_summary"]
        assert isinstance(results[0]["confidence_summary"]["review_triggers"], list)

    def test_text_pdf_yields_zero_ocr_page_count(self):
        conn = MagicMock()
        inp = _make_input(1, chamber="senate")
        metrics = _make_metrics(total_pages=3, text_pages=3, avg_chars=500.0)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=metrics),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert results[0]["ocr_page_count"] == 0

    def test_image_pdf_yields_nonzero_ocr_page_count(self):
        conn = MagicMock()
        inp = _make_input(1, chamber="senate")
        metrics = _make_metrics(total_pages=4, text_pages=0, avg_chars=0.0)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=metrics),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert results[0]["ocr_page_count"] == 4


# ---------------------------------------------------------------------------
# Optional pipeline stages — dispatch_parser
# ---------------------------------------------------------------------------


class TestOptionalPipelineStages:
    def test_dispatch_parser_called_when_present(self):
        conn = MagicMock()
        inp = _make_input(1)
        mock_dispatch = MagicMock(return_value={"fields": []})
        side_effect, _ = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_DISPATCH, mock_dispatch),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert mock_dispatch.called

    def test_dispatch_parser_result_stored_in_parsed_document(self):
        conn = MagicMock()
        inp = _make_input(1)
        doc = {"fields": [{"name": "asset", "value": "AAPL"}]}
        mock_dispatch = MagicMock(return_value=doc)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_DISPATCH, mock_dispatch),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert results[0]["parsed_document"] is doc

    def test_absent_dispatch_parser_yields_none_parsed_document(self):
        """When _dispatch_parser is None, parsed_document is None."""
        conn = MagicMock()
        inp = _make_input(1)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_DISPATCH, None),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert results[0]["parsed_document"] is None

    def test_no_provider_yields_none_resolved_member(self):
        """When index_provider is None, resolved_member is always None."""
        conn = MagicMock()
        inp = _make_input(1)
        side_effect, results = _capturing_run_session()
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert results[0]["resolved_member"] is None

    def test_house_index_metadata_overrides_header_for_dispatch(self):
        conn = MagicMock()
        inp = _make_input(1, chamber="house")
        provider = _FakeIndexProvider(
            matches=[
                ArtifactIndexMatch(
                    artifact=inp.artifact_row,
                    index_row=_make_house_index_row(
                        filing_kind=HouseFilingKind.PTR,
                        raw_filing_type="A",
                    ),
                )
            ]
        )
        mock_dispatch = MagicMock(return_value={"fields": []})
        side_effect, _ = _capturing_run_session()

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _HEADER,
                return_value=HeaderFields(
                    member_name="Jane Doe",
                    chamber=None,
                    filing_type=FilingType.ANNUAL,
                    filing_year=1999,
                    filed_at=date(1999, 12, 31),
                    amendment_number=0,
                    is_amended=False,
                ),
            ),
            patch(_DISPATCH, mock_dispatch),
            patch(_RESOLVE_ARTIFACT, return_value=None),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        filing = mock_dispatch.call_args[0][1]
        assert filing.filing_type is FilingType.PTR
        assert filing.is_amended is True
        assert filing.filing_year == 2024
        assert filing.filed_at == date(2024, 3, 15)

    def test_senate_index_metadata_overrides_header_for_dispatch(self):
        conn = MagicMock()
        inp = _make_input(1, chamber="senate")
        provider = _FakeIndexProvider(
            matches=[
                ArtifactIndexMatch(
                    artifact=inp.artifact_row,
                    index_row=SenateIndexRow(
                        first_name="Jane",
                        last_name="Smith",
                        office="Senator, TX",
                        report_type="Periodic Transaction Report Amendment",
                        date_filed="01/15/2025",
                        doc_id="DOC1",
                        filing_year=2024,
                    ),
                )
            ]
        )
        mock_dispatch = MagicMock(return_value={"fields": []})
        side_effect, _ = _capturing_run_session()

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _HEADER,
                return_value=HeaderFields(
                    member_name="Jane Doe",
                    chamber=None,
                    filing_type=FilingType.ANNUAL,
                    filing_year=1998,
                    filed_at=date(1998, 6, 1),
                    amendment_number=0,
                    is_amended=False,
                ),
            ),
            patch(_DISPATCH, mock_dispatch),
            patch(_RESOLVE_ARTIFACT, return_value=None),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        filing = mock_dispatch.call_args[0][1]
        assert filing.filing_type is FilingType.PTR
        assert filing.is_amended is True
        assert filing.filing_year == 2024
        assert filing.filed_at == date(2025, 1, 15)

    def test_official_non_amendment_clears_header_amendment_number(self):
        conn = MagicMock()
        inp = _make_input(1, chamber="house")
        provider = _FakeIndexProvider(
            matches=[
                ArtifactIndexMatch(
                    artifact=inp.artifact_row,
                    index_row=_make_house_index_row(
                        filing_kind=HouseFilingKind.ANNUAL,
                        raw_filing_type="O",
                    ),
                )
            ]
        )
        mock_dispatch = MagicMock(return_value={"fields": []})
        side_effect, _ = _capturing_run_session()

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _HEADER,
                return_value=HeaderFields(
                    member_name="Jane Doe",
                    chamber=None,
                    filing_type=FilingType.ANNUAL,
                    filing_year=2024,
                    filed_at=None,
                    amendment_number=2,
                    is_amended=True,
                ),
            ),
            patch(_DISPATCH, mock_dispatch),
            patch(_RESOLVE_ARTIFACT, return_value=None),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        filing = mock_dispatch.call_args[0][1]
        assert filing.filing_type is FilingType.ANNUAL
        assert filing.is_amended is False
        assert filing.amendment_number == 0

    def test_parsed_document_filing_is_rewritten_to_official_index_metadata(self):
        conn = MagicMock()
        inp = _make_input(1, chamber="house")
        provider = _FakeIndexProvider(
            matches=[
                ArtifactIndexMatch(
                    artifact=inp.artifact_row,
                    index_row=_make_house_index_row(
                        filing_kind=HouseFilingKind.ANNUAL,
                        raw_filing_type="O",
                    ),
                )
            ]
        )
        noisy_parse_result = ParseResult(
            filing=Filing(
                member_bioguide_id="",
                chamber=Chamber.HOUSE,
                filing_year=1997,
                filing_type=FilingType.PTR,
                filed_at=date(1997, 7, 4),
                amendment_number=4,
                is_amended=True,
                source_record_id="WRONG-DOC",
            ),
            holdings=(),
            transactions=(),
            outside_positions=(),
            meta=ParserMeta(parser_name="test_parser"),
        )
        side_effect, results = _capturing_run_session()

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _HEADER,
                return_value=HeaderFields(
                    member_name="Jane Doe",
                    chamber=None,
                    filing_type=FilingType.PTR,
                    filing_year=1997,
                    filed_at=date(1997, 7, 4),
                    amendment_number=4,
                    is_amended=True,
                ),
            ),
            patch(_DISPATCH, MagicMock(return_value=noisy_parse_result)),
            patch(_RESOLVE_ARTIFACT, return_value=None),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        filing = results[0]["parsed_document"].filing
        assert filing.filing_type is FilingType.ANNUAL
        assert filing.is_amended is False
        assert filing.amendment_number == 0
        assert filing.filing_year == 2024
        assert filing.filed_at == date(2024, 3, 15)
        assert filing.source_record_id == inp.source_record_id

    def test_ocr_required_returns_explicit_skip_and_avoids_dispatch(self):
        conn = MagicMock()
        inp = _make_input(1, chamber="senate")
        mock_dispatch = MagicMock(return_value={"fields": []})
        side_effect, results = _capturing_run_session()

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics(total_pages=4, text_pages=0, avg_chars=0.0)),
            patch(_DISPATCH, mock_dispatch),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)

        assert results[0]["parsed_document"] is None
        assert results[0]["skip_reason_code"] == "ocr_required_not_implemented"
        mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# Provider protocol conformance
# ---------------------------------------------------------------------------


class TestIndexMatchProviderProtocol:
    def test_fake_provider_satisfies_protocol(self):
        """_FakeIndexProvider is a valid IndexMatchProvider at runtime."""
        provider = _FakeIndexProvider(matches=[])
        assert isinstance(provider, IndexMatchProvider)

    def test_index_match_provider_is_exported(self):
        """IndexMatchProvider is importable from disclosures_parse."""
        from src.runtime.disclosures_parse import IndexMatchProvider as _P
        assert _P is not None


# ---------------------------------------------------------------------------
# Provider-driven member resolution
# ---------------------------------------------------------------------------


class TestProviderDrivenResolution:
    """Tests for index_provider parameter and resolution path.

    No DB or network calls: _FakeIndexProvider supplies index matches and
    member rows.  resolve_artifact_member is patched where interaction with
    the member-resolution layer needs to be verified.
    """

    # ------------------------------------------------------------------
    # load_matches invocation
    # ------------------------------------------------------------------

    def test_load_matches_called_once_with_all_artifact_rows(self):
        """load_matches receives every artifact row from the loaded inputs."""
        conn = MagicMock()
        inp1 = _make_input(1, chamber="senate")
        inp2 = _make_input(2, chamber="senate")
        row1 = _make_senate_index_row(doc_id="DOC1")
        row2 = _make_senate_index_row(doc_id="DOC2")
        provider = _FakeIndexProvider(
            matches=[
                ArtifactIndexMatch(artifact=inp1.artifact_row, index_row=row1),
                ArtifactIndexMatch(artifact=inp2.artifact_row, index_row=row2),
            ],
        )

        with (
            patch(_LOAD, return_value=[inp1, inp2]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=[_make_session(1), _make_session(2)]),
            patch(_RESOLVE_ARTIFACT, return_value=None),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        assert len(provider.load_matches_calls) == 1
        ids = {r["id"] for r in provider.load_matches_calls[0]}
        assert ids == {1, 2}

    def test_load_matches_not_called_without_provider(self):
        conn = MagicMock()
        inp = _make_input(1)
        provider = _FakeIndexProvider(matches=[])

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)

        assert provider.load_matches_calls == []

    # ------------------------------------------------------------------
    # load_member_rows invocation
    # ------------------------------------------------------------------

    def test_load_member_rows_called_for_chamber_with_index_row(self):
        conn = MagicMock()
        inp = _make_input(1, chamber="senate")
        index_row = _make_senate_index_row(doc_id="DOC1")
        provider = _FakeIndexProvider(
            matches=[ArtifactIndexMatch(artifact=inp.artifact_row, index_row=index_row)],
        )

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)),
            patch(_RESOLVE_ARTIFACT, return_value=None),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        assert provider.load_member_rows_calls == ["senate"]

    def test_load_member_rows_not_called_when_index_row_is_none(self):
        conn = MagicMock()
        inp = _make_input(1, chamber="senate")
        provider = _FakeIndexProvider(
            matches=[ArtifactIndexMatch(artifact=inp.artifact_row, index_row=None)],
        )

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        assert provider.load_member_rows_calls == []

    def test_load_member_rows_not_called_without_provider(self):
        conn = MagicMock()
        inp = _make_input(1)
        provider = _FakeIndexProvider(matches=[])

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)

        assert provider.load_member_rows_calls == []

    def test_load_member_rows_called_once_per_distinct_chamber(self):
        conn = MagicMock()
        inp1 = _make_input(1, chamber="senate")
        inp2 = _make_input(2, chamber="senate")
        row1 = _make_senate_index_row(doc_id="DOC1")
        row2 = _make_senate_index_row(doc_id="DOC2")
        provider = _FakeIndexProvider(
            matches=[
                ArtifactIndexMatch(artifact=inp1.artifact_row, index_row=row1),
                ArtifactIndexMatch(artifact=inp2.artifact_row, index_row=row2),
            ],
        )

        with (
            patch(_LOAD, return_value=[inp1, inp2]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _RUN_SESSION,
                side_effect=[_make_session(1), _make_session(2)],
            ),
            patch(_RESOLVE_ARTIFACT, return_value=None),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        assert provider.load_member_rows_calls.count("senate") == 1

    # ------------------------------------------------------------------
    # resolve_artifact_member invocation
    # ------------------------------------------------------------------

    def test_resolve_artifact_member_called_when_index_row_present(self):
        conn = MagicMock()
        inp = _make_input(1, chamber="senate")
        index_row = _make_senate_index_row(doc_id="DOC1")
        resolution = {"bioguide_id": "S000001"}
        provider = _FakeIndexProvider(
            matches=[ArtifactIndexMatch(artifact=inp.artifact_row, index_row=index_row)],
        )
        mock_resolve = MagicMock(return_value=resolution)
        side_effect, results = _capturing_run_session()

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RESOLVE_ARTIFACT, mock_resolve),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        assert mock_resolve.called
        assert results[0]["resolved_member"] is resolution

    def test_resolve_artifact_member_not_called_when_index_row_none(self):
        conn = MagicMock()
        inp = _make_input(1, chamber="senate")
        provider = _FakeIndexProvider(
            matches=[ArtifactIndexMatch(artifact=inp.artifact_row, index_row=None)],
        )
        mock_resolve = MagicMock(return_value=None)
        side_effect, results = _capturing_run_session()

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RESOLVE_ARTIFACT, mock_resolve),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        mock_resolve.assert_not_called()
        assert results[0]["resolved_member"] is None

    def test_resolve_artifact_member_not_called_without_matching_entry(self):
        """Artifact with no entry in provider matches gets resolved_member=None."""
        conn = MagicMock()
        inp = _make_input(99, chamber="senate")
        other_inp = _make_input(1, chamber="senate")
        # Provider only has a match for artifact id=1, not id=99.
        provider = _FakeIndexProvider(
            matches=[
                ArtifactIndexMatch(
                    artifact=other_inp.artifact_row,
                    index_row=_make_senate_index_row(doc_id="DOC1"),
                )
            ],
        )
        mock_resolve = MagicMock(return_value=None)
        side_effect, results = _capturing_run_session()

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RESOLVE_ARTIFACT, mock_resolve),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        mock_resolve.assert_not_called()
        assert results[0]["resolved_member"] is None

    def test_resolve_artifact_member_receives_member_rows_for_chamber(self):
        """The member_lookup passed to resolve_artifact_member is keyed by chamber."""
        conn = MagicMock()
        inp = _make_input(1, chamber="senate")
        index_row = _make_senate_index_row(doc_id="DOC1")
        member_rows = [{"bioguide_id": "S000001", "last_name": "Smith"}]
        provider = _FakeIndexProvider(
            matches=[ArtifactIndexMatch(artifact=inp.artifact_row, index_row=index_row)],
            member_rows={"senate": member_rows},
        )
        mock_resolve = MagicMock(return_value=None)
        side_effect, _ = _capturing_run_session()

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RESOLVE_ARTIFACT, mock_resolve),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        call_args = mock_resolve.call_args
        member_lookup_arg = call_args[0][2]
        assert "senate" in member_lookup_arg
        assert member_lookup_arg["senate"] is member_rows

    # ------------------------------------------------------------------
    # Per-artifact failure isolation
    # ------------------------------------------------------------------

    def test_resolution_failure_does_not_break_batch(self):
        """A resolution error on one artifact does not stop the batch."""
        conn = MagicMock()
        inp1 = _make_input(1, chamber="senate")
        inp2 = _make_input(2, chamber="senate")
        row1 = _make_senate_index_row(doc_id="DOC1")
        row2 = _make_senate_index_row(doc_id="DOC2")
        provider = _FakeIndexProvider(
            matches=[
                ArtifactIndexMatch(artifact=inp1.artifact_row, index_row=row1),
                ArtifactIndexMatch(artifact=inp2.artifact_row, index_row=row2),
            ],
        )

        resolution_ok = {"bioguide_id": "S000002"}
        mock_resolve = MagicMock(
            side_effect=[RuntimeError("bad match"), resolution_ok]
        )

        with (
            patch(_LOAD, return_value=[inp1, inp2]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RESOLVE_ARTIFACT, mock_resolve),
            patch(
                _RUN_SESSION,
                side_effect=[
                    RuntimeError("session failed due to resolution error"),
                    _make_session(2),
                ],
            ),
        ):
            result = run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=provider
            )

        assert result.succeeded_count == 1
        assert result.failed_count == 1

    def test_conn_forwarded_to_load_member_rows(self):
        """The conn passed to run_disclosure_parse_runtime is forwarded to load_member_rows."""
        conn = MagicMock()
        inp = _make_input(1, chamber="senate")
        index_row = _make_senate_index_row(doc_id="DOC1")

        received_conns: list[Any] = []

        class _TrackingProvider:
            def load_matches(self, artifacts):
                return [ArtifactIndexMatch(artifact=inp.artifact_row, index_row=index_row)]

            def load_member_rows(self, c, *, chamber):
                received_conns.append(c)
                return []

        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)),
            patch(_RESOLVE_ARTIFACT, return_value=None),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, index_provider=_TrackingProvider()
            )

        assert len(received_conns) == 1
        assert received_conns[0] is conn


# ---------------------------------------------------------------------------
# Explicit parse_inputs path
# ---------------------------------------------------------------------------


class TestExplicitParseInputs:
    """When parse_inputs is provided the DB-query path must not be invoked."""

    def test_explicit_inputs_skips_load_unparsed(self):
        """load_unparsed_disclosure_artifacts is not called when parse_inputs is given."""
        conn = MagicMock()
        inp = _make_input(1)
        with (
            patch(_LOAD) as mock_load,
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, parse_inputs=[inp]
            )
        mock_load.assert_not_called()

    def test_db_path_used_when_parse_inputs_is_none(self):
        """load_unparsed_disclosure_artifacts is called when parse_inputs is None."""
        conn = MagicMock()
        with (
            patch(_LOAD, return_value=[]) as mock_load,
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(1)),
        ):
            run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT, parse_inputs=None)
        mock_load.assert_called_once()

    def test_local_root_none_rejected_when_parse_inputs_is_none(self):
        conn = MagicMock()
        with patch(_LOAD) as mock_load:
            with pytest.raises(ValueError, match="local_root is required"):
                run_disclosure_parse_runtime(conn, local_root=None, parse_inputs=None)
        mock_load.assert_not_called()

    def test_explicit_empty_inputs_yields_zero_counts(self):
        conn = MagicMock()
        with patch(_LOAD) as mock_load:
            result = run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, parse_inputs=[]
            )
        mock_load.assert_not_called()
        assert result.processed_count == 0
        assert result.succeeded_count == 0
        assert result.failed_count == 0
        assert result.parse_sessions == ()
        assert result.parsed_documents == ()

    def test_explicit_inputs_allow_local_root_none(self):
        conn = MagicMock()
        inp = _make_input(5)
        session = _make_session(5)
        with (
            patch(_LOAD) as mock_load,
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=session),
        ):
            result = run_disclosure_parse_runtime(
                conn, local_root=None, parse_inputs=[inp]
            )
        mock_load.assert_not_called()
        assert result.succeeded_count == 1
        assert result.parse_sessions == (session,)

    def test_explicit_inputs_processed_as_normal(self):
        """Inputs supplied via parse_inputs go through the full parse pipeline."""
        conn = MagicMock()
        inp = _make_input(5)
        session = _make_session(5)
        with (
            patch(_LOAD),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=session),
        ):
            result = run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, parse_inputs=[inp]
            )
        assert result.succeeded_count == 1
        assert result.failed_count == 0
        assert result.parse_sessions == (session,)

    def test_explicit_inputs_bytes_used_by_parse_fn(self):
        """parse_fn inside run_parse_session uses the bytes from the supplied input."""
        conn = MagicMock()
        inp = _FakeParseInput(
            chamber="house",
            source_record_id="DOCX",
            artifact_row={"id": 77, "chamber": "house", "storage_uri": "x.pdf"},
            local_bytes=b"explicit bytes",
        )
        captured: list[bytes] = []
        side_effect, _ = _capturing_run_session()

        with (
            patch(_LOAD),
            patch(
                _EXTRACT,
                side_effect=lambda data: captured.append(data) or _make_metrics(),
            ),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, parse_inputs=[inp]
            )

        assert captured == [b"explicit bytes"]

    def test_explicit_inputs_multiple_processed_in_order(self):
        conn = MagicMock()
        inputs = [_make_input(i) for i in range(1, 4)]
        sessions = [_make_session(i) for i in range(1, 4)]
        with (
            patch(_LOAD),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=sessions),
        ):
            result = run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, parse_inputs=inputs
            )
        assert result.succeeded_count == 3
        assert result.parse_sessions == tuple(sessions)

    def test_explicit_inputs_with_index_provider(self):
        """Index provider resolution works when inputs are supplied explicitly."""
        conn = MagicMock()
        inp = _make_input(1, chamber="senate")
        index_row = _make_senate_index_row(doc_id="DOC1")
        resolution = {"bioguide_id": "S000001"}
        provider = _FakeIndexProvider(
            matches=[ArtifactIndexMatch(artifact=inp.artifact_row, index_row=index_row)],
        )
        mock_resolve = MagicMock(return_value=resolution)
        side_effect, results = _capturing_run_session()

        with (
            patch(_LOAD),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RESOLVE_ARTIFACT, mock_resolve),
            patch(_RUN_SESSION, side_effect=side_effect),
        ):
            run_disclosure_parse_runtime(
                conn,
                local_root=_LOCAL_ROOT,
                parse_inputs=[inp],
                index_provider=provider,
            )

        assert mock_resolve.called
        assert results[0]["resolved_member"] is resolution

    def test_explicit_inputs_source_artifact_id_forwarded_to_session(self):
        conn = MagicMock()
        inp = _make_input(artifact_id=99)
        with (
            patch(_LOAD),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, return_value=_make_session(99)) as mock_run,
        ):
            run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, parse_inputs=[inp]
            )
        _, kwargs = mock_run.call_args
        assert kwargs["source_artifact_id"] == 99


# ---------------------------------------------------------------------------
# failed_artifact_ids — explicit per-failure tracking
# ---------------------------------------------------------------------------


class TestFailedArtifactIds:
    def test_no_failures_yields_empty_tuple(self):
        conn = MagicMock()
        inp = _make_input(1)
        with _patch_all(inputs=[inp], session_result=_make_session(1)):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.failed_artifact_ids == ()

    def test_failed_artifact_id_recorded_on_exception(self):
        conn = MagicMock()
        inp = _make_input(artifact_id=42)
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=RuntimeError("corrupt pdf")),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.failed_artifact_ids == (42,)

    def test_multiple_failures_all_ids_recorded(self):
        conn = MagicMock()
        inputs = [_make_input(i) for i in (10, 20, 30)]
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _RUN_SESSION,
                side_effect=[
                    RuntimeError("bad 10"),
                    RuntimeError("bad 20"),
                    RuntimeError("bad 30"),
                ],
            ),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert set(result.failed_artifact_ids) == {10, 20, 30}

    def test_failed_ids_only_from_failed_not_succeeded(self):
        conn = MagicMock()
        inputs = [_make_input(1), _make_input(2), _make_input(3)]
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _RUN_SESSION,
                side_effect=[
                    _make_session(1),
                    RuntimeError("bad"),
                    _make_session(3),
                ],
            ),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.failed_artifact_ids == (2,)

    def test_failed_artifact_ids_is_tuple(self):
        conn = MagicMock()
        inp = _make_input(1)
        with (
            patch(_LOAD, return_value=[inp]),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=ValueError("bad")),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert isinstance(result.failed_artifact_ids, tuple)

    def test_failed_artifact_ids_length_equals_failed_count(self):
        conn = MagicMock()
        inputs = [_make_input(i) for i in range(1, 5)]
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _RUN_SESSION,
                side_effect=[
                    _make_session(1),
                    RuntimeError("bad"),
                    _make_session(3),
                    RuntimeError("bad"),
                ],
            ),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert len(result.failed_artifact_ids) == result.failed_count

    def test_failed_ids_order_matches_input_order(self):
        """Failure IDs are appended in input-iteration order, not artifact-id order."""
        conn = MagicMock()
        inputs = [_make_input(30), _make_input(10), _make_input(20)]
        with (
            patch(_LOAD, return_value=inputs),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(
                _RUN_SESSION,
                side_effect=[
                    RuntimeError("bad 30"),
                    RuntimeError("bad 10"),
                    RuntimeError("bad 20"),
                ],
            ),
        ):
            result = run_disclosure_parse_runtime(conn, local_root=_LOCAL_ROOT)
        assert list(result.failed_artifact_ids) == [30, 10, 20]

    def test_explicit_parse_inputs_failed_ids_tracked(self):
        """failed_artifact_ids tracks failures from explicitly provided inputs too."""
        conn = MagicMock()
        inp = _make_input(artifact_id=55)
        with (
            patch(_LOAD),
            patch(_EXTRACT, return_value=_make_metrics()),
            patch(_RUN_SESSION, side_effect=RuntimeError("bad")),
        ):
            result = run_disclosure_parse_runtime(
                conn, local_root=_LOCAL_ROOT, parse_inputs=[inp]
            )
        assert result.failed_artifact_ids == (55,)
