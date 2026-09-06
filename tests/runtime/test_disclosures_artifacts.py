"""Tests for src/runtime/disclosures_artifacts.py.

No live DB, no network, no filesystem writes.  All external boundaries are
mocked: provenance store, discovery, download, and artifact_store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.parse.disclosures.acquire import ArtifactKind, ArtifactMeta
from src.parse.disclosures.models import Chamber
from src.runtime.disclosures_artifacts import (
    DisclosureArtifactIngestResult,
    run_disclosure_artifact_ingest,
)

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_ENSURE = "src.runtime.disclosures_artifacts.ensure_data_source"
_START = "src.runtime.disclosures_artifacts.start_ingestion_run"
_FINISH = "src.runtime.disclosures_artifacts.finish_ingestion_run"
_FAIL = "src.runtime.disclosures_artifacts.fail_ingestion_run"
_DISCOVER = "src.runtime.disclosures_artifacts.fetch_disclosure_artifacts"
_DOWNLOAD = "src.runtime.disclosures_artifacts.download_artifact_bytes"
_STORE = "src.runtime.disclosures_artifacts.store_downloaded_artifact"
_WRITE = "src.runtime.disclosures_artifacts.write_artifact"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RUN_ID = 11
_DS_ROW: dict[str, Any] = {"id": 7, "slug": "senate-disclosures"}
_PDF = b"%PDF-1.4 fake"

_SENATE_META = ArtifactMeta(
    source_slug="senate-disclosures",
    chamber=Chamber.SENATE,
    artifact_kind=ArtifactKind.PDF,
    source_url="https://efdsearch.senate.gov/search/view/paper/DOC1/",
    storage_key="disclosures/senate/2024/S000001/DOC1.pdf",
    member_bioguide_id="S000001",
    filing_year=2024,
    source_record_id="DOC1",
)

_HOUSE_META = ArtifactMeta(
    source_slug="house-disclosures",
    chamber=Chamber.HOUSE,
    artifact_kind=ArtifactKind.PDF,
    source_url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC2.pdf",
    storage_key="disclosures/house/2024/H000001/DOC2.pdf",
    member_bioguide_id="H000001",
    filing_year=2024,
    source_record_id="DOC2",
)

_ARTIFACT_ROW: dict[str, Any] = {
    "id": 99,
    "data_source_id": 7,
    "artifact_kind": "pdf",
    "storage_uri": _SENATE_META.storage_key,
    "sha256": "a" * 64,
    "source_url": _SENATE_META.source_url,
    "mime_type": "application/pdf",
    "fetched_at": None,
    "source_record_id": "DOC1",
}


def _patch_all(
    metas=None,
    artifact_row=None,
    ds_row=None,
    run_id=_RUN_ID,
):
    """Context manager wrapping the full happy-path mock set."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        with (
            patch(_ENSURE, return_value=ds_row or _DS_ROW) as mock_ensure,
            patch(_START, return_value=run_id) as mock_start,
            patch(_FINISH) as mock_finish,
            patch(_FAIL) as mock_fail,
            patch(
                _DISCOVER, return_value=metas if metas is not None else [_SENATE_META]
            ) as mock_discover,
            patch(_DOWNLOAD, return_value=_PDF) as mock_download,
            patch(_STORE, return_value=artifact_row or _ARTIFACT_ROW) as mock_store,
            patch(_WRITE) as mock_write,
        ):
            yield {
                "ensure": mock_ensure,
                "start": mock_start,
                "finish": mock_finish,
                "fail": mock_fail,
                "discover": mock_discover,
                "download": mock_download,
                "store": mock_store,
                "write": mock_write,
            }

    return _ctx()


# ---------------------------------------------------------------------------
# Invalid chamber
# ---------------------------------------------------------------------------


class TestInvalidChamber:
    def test_raises_for_unknown_chamber(self):
        conn = MagicMock()
        with pytest.raises(ValueError, match="chamber must be 'house' or 'senate'"):
            run_disclosure_artifact_ingest(conn, chamber="federal", year=2024)

    def test_raises_before_any_db_call(self):
        conn = MagicMock()
        with patch(_ENSURE) as mock_ensure:
            with pytest.raises(ValueError):
                run_disclosure_artifact_ingest(conn, chamber="bad", year=2024)
        mock_ensure.assert_not_called()


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_returns_typed_result(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert isinstance(result, DisclosureArtifactIngestResult)

    def test_result_contains_data_source(self):
        conn = MagicMock()
        with _patch_all(ds_row=_DS_ROW):
            result = run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert result.data_source == _DS_ROW

    def test_result_contains_run_id(self):
        conn = MagicMock()
        with _patch_all(run_id=42):
            result = run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert result.run_id == 42

    def test_discovered_count_matches_metas(self):
        conn = MagicMock()
        two_metas = [_SENATE_META, _SENATE_META]
        with _patch_all(metas=two_metas):
            result = run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert result.discovered_count == 2

    def test_stored_count_matches_rows(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert result.stored_count == 1

    def test_artifact_rows_is_tuple(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert isinstance(result.artifact_rows, tuple)

    def test_artifact_rows_contains_stored_rows(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert result.artifact_rows == (_ARTIFACT_ROW,)

    def test_empty_metas_produces_zero_counts(self):
        conn = MagicMock()
        with _patch_all(metas=[]):
            result = run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert result.discovered_count == 0
        assert result.stored_count == 0
        assert result.artifact_rows == ()


# ---------------------------------------------------------------------------
# Provenance step ordering
# ---------------------------------------------------------------------------


class TestProvenanceOrdering:
    def test_ensure_called_before_start(self):
        order: list[str] = []
        conn = MagicMock()
        with (
            patch(_ENSURE, side_effect=lambda *a, **k: order.append("ensure") or _DS_ROW),
            patch(_START, side_effect=lambda *a, **k: order.append("start") or _RUN_ID),
            patch(_FINISH),
            patch(_FAIL),
            patch(_DISCOVER, return_value=[]),
            patch(_DOWNLOAD, return_value=_PDF),
            patch(_STORE, return_value=_ARTIFACT_ROW),
            patch(_WRITE),
        ):
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert order.index("ensure") < order.index("start")

    def test_start_called_before_discover(self):
        order: list[str] = []
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, side_effect=lambda *a, **k: order.append("start") or _RUN_ID),
            patch(_FINISH),
            patch(_FAIL),
            patch(_DISCOVER, side_effect=lambda *a, **k: order.append("discover") or []),
            patch(_DOWNLOAD, return_value=_PDF),
            patch(_STORE, return_value=_ARTIFACT_ROW),
            patch(_WRITE),
        ):
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert order.index("start") < order.index("discover")

    def test_finish_called_after_store(self):
        order: list[str] = []
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH, side_effect=lambda *a, **k: order.append("finish")),
            patch(_FAIL),
            patch(_DISCOVER, return_value=[_SENATE_META]),
            patch(_DOWNLOAD, return_value=_PDF),
            patch(_STORE, side_effect=lambda *a, **k: order.append("store") or _ARTIFACT_ROW),
            patch(_WRITE),
        ):
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert order.index("store") < order.index("finish")

    def test_fail_not_called_on_success(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        mocks["fail"].assert_not_called()

    def test_finish_not_called_on_failure(self):
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH) as mock_finish,
            patch(_FAIL),
            patch(_DISCOVER, side_effect=RuntimeError("network down")),
            patch(_DOWNLOAD, return_value=_PDF),
            patch(_STORE, return_value=_ARTIFACT_ROW),
            patch(_WRITE),
        ):
            with pytest.raises(RuntimeError):
                run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        mock_finish.assert_not_called()


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_store_uses_outer_transaction(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)

        assert mocks["store"].call_args.kwargs["commit"] is False

    def test_store_error_rolls_back_partial_artifacts_before_marking_failed(self):
        conn = MagicMock()
        order: list[str] = []
        conn.rollback.side_effect = lambda: order.append("rollback")

        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL, side_effect=lambda *a, **k: order.append("fail")),
            patch(_DISCOVER, return_value=[_SENATE_META]),
            patch(_DOWNLOAD, return_value=_PDF),
            patch(_STORE, side_effect=RuntimeError("db error")),
            patch(_WRITE),
        ):
            with pytest.raises(RuntimeError, match="db error"):
                run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)

        assert order == ["rollback", "fail"]

    def test_local_write_error_rolls_back_stored_artifact_before_marking_failed(self):
        conn = MagicMock()
        root = Path("/tmp/artifacts")
        order: list[str] = []
        conn.rollback.side_effect = lambda: order.append("rollback")

        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL, side_effect=lambda *a, **k: order.append("fail")),
            patch(_DISCOVER, return_value=[_SENATE_META]),
            patch(_DOWNLOAD, return_value=_PDF),
            patch(_STORE, return_value=_ARTIFACT_ROW),
            patch(_WRITE, side_effect=OSError("disk full")),
        ):
            with pytest.raises(OSError, match="disk full"):
                run_disclosure_artifact_ingest(
                    conn,
                    chamber="senate",
                    year=2024,
                    local_root=root,
                )

        assert order == ["rollback", "fail"]

    def test_discover_error_marks_run_failed(self):
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL) as mock_fail,
            patch(_DISCOVER, side_effect=OSError("timeout")),
            patch(_DOWNLOAD, return_value=_PDF),
            patch(_STORE, return_value=_ARTIFACT_ROW),
            patch(_WRITE),
        ):
            with pytest.raises(IOError):
                run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        mock_fail.assert_called_once_with(conn, _RUN_ID, "timeout")

    def test_download_error_marks_run_failed(self):
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL) as mock_fail,
            patch(_DISCOVER, return_value=[_SENATE_META]),
            patch(_DOWNLOAD, side_effect=ValueError("bad url")),
            patch(_STORE, return_value=_ARTIFACT_ROW),
            patch(_WRITE),
        ):
            with pytest.raises(ValueError):
                run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        mock_fail.assert_called_once_with(conn, _RUN_ID, "bad url")

    def test_error_is_reraised(self):
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL),
            patch(_DISCOVER, side_effect=RuntimeError("boom")),
            patch(_DOWNLOAD, return_value=_PDF),
            patch(_STORE, return_value=_ARTIFACT_ROW),
            patch(_WRITE),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)

    def test_fail_receives_run_id(self):
        conn = MagicMock()
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=55),
            patch(_FINISH),
            patch(_FAIL) as mock_fail,
            patch(_DISCOVER, side_effect=RuntimeError("oops")),
            patch(_DOWNLOAD, return_value=_PDF),
            patch(_STORE, return_value=_ARTIFACT_ROW),
            patch(_WRITE),
        ):
            with pytest.raises(RuntimeError):
                run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        mock_fail.assert_called_once_with(conn, 55, "oops")


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestWiring:
    def test_ensure_receives_senate_slug(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        args = mocks["ensure"].call_args[0]
        assert args[1] == "senate-disclosures"

    def test_ensure_receives_house_slug(self):
        conn = MagicMock()
        with _patch_all(
            ds_row={"id": 3, "slug": "house-disclosures"},
            metas=[_HOUSE_META],
        ) as mocks:
            run_disclosure_artifact_ingest(conn, chamber="house", year=2024, filing_kind="ptr")
        args = mocks["ensure"].call_args[0]
        assert args[1] == "house-disclosures"

    def test_start_receives_data_source_id(self):
        conn = MagicMock()
        ds = {"id": 22, "slug": "senate-disclosures"}
        with _patch_all(ds_row=ds) as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert mocks["start"].call_args[0][1] == 22

    def test_start_uses_schema_ingest_run_type_with_artifact_stage_parameter(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        assert mocks["start"].call_args[0][2] == "ingest"
        assert mocks["start"].call_args.kwargs["parameters"]["stage"] == "artifact_ingest"

    def test_discover_receives_chamber_and_year(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2023)
        args, kwargs = mocks["discover"].call_args
        assert args[0] == "senate"
        assert args[1] == 2023

    def test_discover_receives_filing_kind(self):
        conn = MagicMock()
        with _patch_all(
            ds_row={"id": 3, "slug": "house-disclosures"},
            metas=[_HOUSE_META],
        ) as mocks:
            run_disclosure_artifact_ingest(conn, chamber="house", year=2024, filing_kind="annual")
        _, kwargs = mocks["discover"].call_args
        assert kwargs.get("filing_kind") == "annual"

    def test_discover_receives_client(self):
        conn = MagicMock()
        fake_client = object()
        with _patch_all() as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024, client=fake_client)
        _, kwargs = mocks["discover"].call_args
        assert kwargs.get("client") is fake_client

    def test_download_called_with_source_url(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        mocks["download"].assert_called_once_with(_SENATE_META.source_url)

    def test_store_called_with_meta_and_bytes(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        mocks["store"].assert_called_once_with(
            conn,
            _SENATE_META,
            _PDF,
            ingestion_run_id=_RUN_ID,
            commit=False,
        )

    def test_finish_receives_stored_count(self):
        conn = MagicMock()
        two_metas = [_SENATE_META, _SENATE_META]
        with _patch_all(metas=two_metas) as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        mocks["finish"].assert_called_once_with(conn, _RUN_ID, 2)


# ---------------------------------------------------------------------------
# Local artifact write
# ---------------------------------------------------------------------------


class TestLocalArtifactWrite:
    def test_write_not_called_without_local_root(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024)
        mocks["write"].assert_not_called()

    def test_write_called_with_local_root(self):
        conn = MagicMock()
        root = Path("/tmp/artifacts")
        with _patch_all() as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024, local_root=root)
        mocks["write"].assert_called_once_with(root, _SENATE_META, _PDF)

    def test_write_called_once_per_artifact(self):
        conn = MagicMock()
        root = Path("/tmp/artifacts")
        two_metas = [_SENATE_META, _SENATE_META]
        with _patch_all(metas=two_metas) as mocks:
            run_disclosure_artifact_ingest(conn, chamber="senate", year=2024, local_root=root)
        assert mocks["write"].call_count == 2

    def test_write_failure_marks_run_failed(self):
        conn = MagicMock()
        root = Path("/tmp/artifacts")
        with (
            patch(_ENSURE, return_value=_DS_ROW),
            patch(_START, return_value=_RUN_ID),
            patch(_FINISH),
            patch(_FAIL) as mock_fail,
            patch(_DISCOVER, return_value=[_SENATE_META]),
            patch(_DOWNLOAD, return_value=_PDF),
            patch(_STORE, return_value=_ARTIFACT_ROW),
            patch(_WRITE, side_effect=OSError("disk full")),
        ):
            with pytest.raises(OSError):
                run_disclosure_artifact_ingest(conn, chamber="senate", year=2024, local_root=root)
        mock_fail.assert_called_once()
