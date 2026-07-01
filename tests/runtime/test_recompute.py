"""Tests for src/runtime/recompute.py.

No live DB — all provenance and pipeline boundaries are mocked.
"""

from __future__ import annotations

import contextlib
import datetime as dt
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.db.load_report import LoadSummary, WarnErrorSummary, build_load_summary  # noqa: E402
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef  # noqa: E402
from src.pipeline.recompute_run import RecomputeRunResult  # noqa: E402
from src.runtime.output import summarize_recompute_result  # noqa: E402
from src.runtime.recompute import (  # noqa: E402
    RuntimeRecomputeResult,
    _null_issuer_sector_resolver,
    run_recompute_runtime,
)

# ---------------------------------------------------------------------------
# Constants / fixtures
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = dt.date(2024, 6, 1)
_RUN_ID = 7
_DATA_SOURCE = {"id": 3, "slug": "recompute_internal", "name": "Psephos America Internal Recompute"}

_MODULE = "src.runtime.recompute"


def _empty_run_result() -> RecomputeRunResult:
    return RecomputeRunResult(rule_fires=[], evidence_cards=[], load_summary=None)


def _load_summary() -> LoadSummary:
    return build_load_summary([], warn_error=WarnErrorSummary(), run_id=_RUN_ID)


def _ontology_edge() -> OntologyEdgePayload:
    from src.export.contracts import SourceAnchor

    return OntologyEdgePayload(
        edge_id="ont-edge-test",
        edge_type="member_committee_assignment",
        subject=OntologyNodeRef(node_type="member", node_id="A000001"),
        object=OntologyNodeRef(node_type="committee", node_id="HSEN"),
        source_anchors=[
            SourceAnchor(
                source_type="committee_membership",
                source_id="10",
                url="https://api.congress.gov/v3/committee/house/HSEN?format=json",
                label="Committee membership: Energy",
            )
        ],
    )


def _taxonomy() -> Any:
    return MagicMock()


@contextlib.contextmanager
def _patched_runtime(
    *, run_result=None, ensure=None, start=None, recompute=None, finish=None, fail=None
):
    """Patch the five provenance/pipeline boundaries for run_recompute_runtime.

    Each kwarg overrides the default mock behaviour for that boundary.
    Yields a dict of all mock objects keyed by name.
    """
    if run_result is None:
        run_result = _empty_run_result()

    with (
        patch(
            f"{_MODULE}.ensure_data_source",
            side_effect=ensure,
            return_value=_DATA_SOURCE if ensure is None else None,
        ) as m_ensure,
        patch(
            f"{_MODULE}.start_ingestion_run",
            side_effect=start,
            return_value=_RUN_ID if start is None else None,
        ) as m_start,
        patch(
            f"{_MODULE}.run_recompute",
            side_effect=recompute,
            return_value=run_result if recompute is None else None,
        ) as m_recompute,
        patch(f"{_MODULE}.finish_ingestion_run", side_effect=finish) as m_finish,
        patch(f"{_MODULE}.fail_ingestion_run", side_effect=fail) as m_fail,
    ):
        yield {
            "ensure": m_ensure,
            "start": m_start,
            "recompute": m_recompute,
            "finish": m_finish,
            "fail": m_fail,
        }


# ---------------------------------------------------------------------------
# Null resolver
# ---------------------------------------------------------------------------


def test_null_issuer_sector_resolver_always_none() -> None:
    assert _null_issuer_sector_resolver("Acme Corp", "ACM") is None
    assert _null_issuer_sector_resolver("Unknown", None) is None


# ---------------------------------------------------------------------------
# Happy-path: all boundaries explicit
# ---------------------------------------------------------------------------


def test_run_recompute_runtime_returns_typed_result() -> None:
    conn = MagicMock()
    taxonomy = _taxonomy()
    run_result = _empty_run_result()

    with _patched_runtime(run_result=run_result) as mocks:
        result = run_recompute_runtime(
            conn,
            _SNAPSHOT_DATE,
            taxonomy=taxonomy,
            issuer_sector_resolver=lambda n, t: None,
        )

    assert isinstance(result, RuntimeRecomputeResult)
    assert result.data_source is _DATA_SOURCE
    assert result.run_id == _RUN_ID
    assert result.recompute_result is run_result
    mocks["fail"].assert_not_called()


def test_provenance_calls_in_order() -> None:
    """ensure_data_source → start_ingestion_run → finish_ingestion_run."""
    call_log: list[str] = []

    with _patched_runtime(
        ensure=lambda *_a, **_kw: (call_log.append("ensure"), _DATA_SOURCE)[1],
        start=lambda *_a, **_kw: (call_log.append("start"), _RUN_ID)[1],
        finish=lambda *_a, **_kw: call_log.append("finish"),
    ):
        run_recompute_runtime(MagicMock(), _SNAPSHOT_DATE, taxonomy=_taxonomy())

    assert call_log == ["ensure", "start", "finish"]


# ---------------------------------------------------------------------------
# run_recompute is called with the correct keyword arguments
# ---------------------------------------------------------------------------


def test_run_recompute_receives_correct_kwargs() -> None:
    conn = MagicMock()
    taxonomy = _taxonomy()

    def resolver(name: str, ticker: str | None) -> str | None:
        return "finance"

    def contribution_resolver(row: dict[str, Any]) -> str | None:
        return "finance" if row.get("donor_name") else None

    statement_rows = [{"member_bioguide_id": "A000001", "sector": "energy"}]

    with _patched_runtime() as mocks:
        run_recompute_runtime(
            conn,
            _SNAPSHOT_DATE,
            taxonomy=taxonomy,
            issuer_sector_resolver=resolver,
            contribution_sector_resolver=contribution_resolver,
            statement_rows=statement_rows,
        )

    mocks["recompute"].assert_called_once_with(
        conn,
        recompute_run_id=_RUN_ID,
        snapshot_date=_SNAPSHOT_DATE,
        taxonomy=taxonomy,
        issuer_sector_resolver=resolver,
        contribution_sector_resolver=contribution_resolver,
        statement_rows=statement_rows,
        commit=False,
    )


def test_start_ingestion_run_records_statement_row_source_metadata() -> None:
    statement_rows = [{"member_bioguide_id": "A000001", "sector": "energy"}]
    statement_rows_source = {
        "path": "/tmp/statements.jsonl",
        "sha256": "abc123",
        "row_count": 1,
    }

    with _patched_runtime() as mocks:
        run_recompute_runtime(
            MagicMock(),
            _SNAPSHOT_DATE,
            taxonomy=_taxonomy(),
            statement_rows=statement_rows,
            statement_rows_source=statement_rows_source,
        )

    parameters = mocks["start"].call_args.kwargs["parameters"]
    assert parameters == {
        "snapshot_date": _SNAPSHOT_DATE.isoformat(),
        "statement_rows_source": statement_rows_source,
    }


# ---------------------------------------------------------------------------
# record_count passed to finish_ingestion_run
# ---------------------------------------------------------------------------


def test_finish_receives_combined_record_count() -> None:
    from src.rules.models import RuleFire

    fires = [MagicMock(spec=RuleFire), MagicMock(spec=RuleFire)]
    cards = [MagicMock()]
    run_result = RecomputeRunResult(rule_fires=fires, evidence_cards=cards, load_summary=None)

    conn = MagicMock()
    with _patched_runtime(run_result=run_result) as mocks:
        run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    mocks["finish"].assert_called_once_with(conn, _RUN_ID, record_count=3)


def test_runtime_result_exposes_unresolved_committee_matches() -> None:
    conn = MagicMock()
    run_result = RecomputeRunResult(
        rule_fires=[],
        evidence_cards=[],
        load_summary=_load_summary(),
        review_required_rows_by_family={
            "committee_sector_trade": [
                {
                    "member_bioguide_id": "A000001",
                    "committee_name": "Committee on Energy",
                    "committee_membership_id": 10,
                    "committee_sector": "energy",
                    "committee_mapping_tier": "review_required",
                    "financial_disclosure_id": 100,
                }
            ]
        },
    )

    with _patched_runtime(run_result=run_result):
        result = run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    assert len(result.unresolved_committee_matches) == 1
    assert result.unresolved_committee_matches[0].family == "committee_sector_trade"
    assert result.unresolved_committee_matches[0].status == "unresolved_review_required"
    assert result.unresolved_committee_matches[0].scored is False
    assert result.unresolved_committee_matches[0].deterministic is False


def test_summary_surfaces_unresolved_committee_matches() -> None:
    conn = MagicMock()
    run_result = RecomputeRunResult(
        rule_fires=[],
        evidence_cards=[],
        load_summary=_load_summary(),
        review_required_rows_by_family={
            "committee_sector_trade": [
                {
                    "member_bioguide_id": "A000001",
                    "committee_name": "Committee on Energy",
                    "committee_membership_id": 10,
                    "committee_sector": "energy",
                    "committee_mapping_tier": "review_required",
                    "financial_disclosure_id": 100,
                }
            ]
        },
    )

    with _patched_runtime(run_result=run_result):
        result = run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    summary = summarize_recompute_result(result)

    assert summary["load"]["unresolved_committee_matches"]["count"] == 1
    item = summary["load"]["unresolved_committee_matches"]["items"][0]
    assert item["family"] == "committee_sector_trade"
    assert item["status"] == "unresolved_review_required"
    assert item["scored"] is False
    assert item["deterministic"] is False


def test_summary_surfaces_ontology_edge_counts_by_type() -> None:
    result = RuntimeRecomputeResult(
        data_source=_DATA_SOURCE,
        run_id=_RUN_ID,
        recompute_result=RecomputeRunResult(
            rule_fires=[],
            evidence_cards=[],
            load_summary=_load_summary(),
            ontology_edges=[_ontology_edge()],
        ),
    )

    summary = summarize_recompute_result(result)

    assert summary["ontology_edges"] == {
        "count": 1,
        "by_type": {"member_committee_assignment": 1},
    }


# ---------------------------------------------------------------------------
# Failure: run_recompute raises → fail_ingestion_run called, exception re-raised
# ---------------------------------------------------------------------------


def test_pipeline_failure_marks_run_failed_and_reraises() -> None:
    conn = MagicMock()
    boom = RuntimeError("db exploded")

    with _patched_runtime(recompute=boom) as mocks:
        with pytest.raises(RuntimeError, match="db exploded"):
            run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    mocks["fail"].assert_called_once_with(conn, _RUN_ID, error_message="db exploded")
    mocks["finish"].assert_not_called()


def test_pipeline_failure_rolls_back_uncommitted_recompute_before_marking_failed() -> None:
    conn = MagicMock()
    order: list[str] = []
    conn.rollback.side_effect = lambda: order.append("rollback")

    with _patched_runtime(
        recompute=RuntimeError("db exploded"),
        fail=lambda *_a, **_kw: order.append("fail"),
    ):
        with pytest.raises(RuntimeError, match="db exploded"):
            run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    assert order == ["rollback", "fail"]


def test_finish_failure_rolls_back_uncommitted_recompute() -> None:
    conn = MagicMock()

    with _patched_runtime(finish=RuntimeError("finish failed")):
        with pytest.raises(RuntimeError, match="finish failed"):
            run_recompute_runtime(conn, _SNAPSHOT_DATE, taxonomy=_taxonomy())

    conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# Default resolvers: taxonomy loaded lazily; null resolver used when absent
# ---------------------------------------------------------------------------


def test_null_resolver_used_when_issuer_resolver_omitted() -> None:
    """run_recompute should receive _null_issuer_sector_resolver by default."""
    with _patched_runtime() as mocks:
        run_recompute_runtime(MagicMock(), _SNAPSHOT_DATE, taxonomy=_taxonomy())

    resolver = mocks["recompute"].call_args[1]["issuer_sector_resolver"]
    assert resolver("Anything", "TICK") is None


def test_taxonomy_loaded_from_data_root_when_none() -> None:
    """When taxonomy=None, load_taxonomy_runtime should be called once."""
    fake_taxonomy = _taxonomy()

    with (
        _patched_runtime() as mocks,
        patch(
            "src.normalize.taxonomy_runtime.load_taxonomy_runtime", return_value=fake_taxonomy
        ) as mock_load,
    ):
        run_recompute_runtime(MagicMock(), _SNAPSHOT_DATE)

    mock_load.assert_called_once()
    assert mocks["recompute"].call_args[1]["taxonomy"] is fake_taxonomy


# ---------------------------------------------------------------------------
# Source constants are correct
# ---------------------------------------------------------------------------


def test_ensure_data_source_called_with_canonical_slug() -> None:
    with _patched_runtime() as mocks:
        run_recompute_runtime(MagicMock(), _SNAPSHOT_DATE, taxonomy=_taxonomy())

    args, kwargs = mocks["ensure"].call_args
    slug = kwargs.get("slug") or args[1]
    assert slug == "conflict-recompute"


def test_start_ingestion_run_called_with_recompute_type() -> None:
    with _patched_runtime() as mocks:
        run_recompute_runtime(MagicMock(), _SNAPSHOT_DATE, taxonomy=_taxonomy())

    args, kwargs = mocks["start"].call_args
    run_type = kwargs.get("run_type") or args[2]
    assert run_type == "recompute"
