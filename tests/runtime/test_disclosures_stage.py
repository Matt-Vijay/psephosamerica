"""Tests for src/runtime/disclosures_stage.py.

No live DB, no network, no real filesystem writes.  All external boundaries
are mocked: provenance store and create_source_artifact.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.runtime.disclosures_bundle import (
    DisclosureArtifactEntry,
    DisclosuresBundle,
    SenateBundledIndexRow,
)
from src.runtime.disclosures_stage import (
    DisclosureStagingResult,
    run_disclosures_bundle_stage,
    stage_disclosures_bundle,
)

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_ENSURE = "src.runtime.disclosures_stage.ensure_data_source"
_START = "src.runtime.disclosures_stage.start_ingestion_run"
_FINISH = "src.runtime.disclosures_stage.finish_ingestion_run"
_FAIL = "src.runtime.disclosures_stage.fail_ingestion_run"
_CREATE = "src.runtime.disclosures_stage.create_source_artifact"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RUN_ID = 42
_DS_ROW: dict[str, Any] = {"id": 5, "slug": "senate-disclosures"}
_ENTRY_SHA = "a" * 64

_ARTIFACT_ROW: dict[str, Any] = {
    "id": 99,
    "data_source_id": 5,
    "artifact_kind": "pdf",
    "storage_uri": "disclosures/senate/2024/S000001/DOC1.pdf",
    "sha256": _ENTRY_SHA,
    "source_url": "https://efdsearch.senate.gov/search/view/paper/DOC1/",
    "mime_type": "application/pdf",
    "fetched_at": None,
    "source_record_id": "DOC1",
    "ingestion_run_id": _RUN_ID,
}


def _make_entry(
    sha256: str = _ENTRY_SHA,
    source_slug: str = "senate-disclosures",
) -> DisclosureArtifactEntry:
    return DisclosureArtifactEntry(
        source_record_id="DOC1",
        chamber="senate",
        filing_year=2024,
        storage_uri="disclosures/senate/2024/S000001/DOC1.pdf",
        source_url="https://efdsearch.senate.gov/search/view/paper/DOC1/",
        source_slug=source_slug,
        artifact_kind="pdf",
        sha256=sha256,
        index_row=SenateBundledIndexRow(
            first_name="Jane",
            last_name="Doe",
            office="Senator, TX",
            report_type="Annual Report for CY2023",
            date_filed="01/15/2024",
            doc_id="DOC1",
        ),
    )


def _make_bundle(
    entries: tuple[DisclosureArtifactEntry, ...] = (),
) -> DisclosuresBundle:
    return DisclosuresBundle(artifacts=entries)


# ---------------------------------------------------------------------------
# Context-manager helper to patch all DB boundaries at once
# ---------------------------------------------------------------------------


@contextmanager
def _patch_all(
    ds_row: dict[str, Any] | None = None,
    run_id: int = _RUN_ID,
    artifact_row: dict[str, Any] | None = None,
):
    with (
        patch(_ENSURE, return_value=ds_row or _DS_ROW) as mock_ensure,
        patch(_START, return_value=run_id) as mock_start,
        patch(_FINISH) as mock_finish,
        patch(_FAIL) as mock_fail,
        patch(_CREATE, return_value=artifact_row or _ARTIFACT_ROW) as mock_create,
    ):
        yield {
            "ensure": mock_ensure,
            "start": mock_start,
            "finish": mock_finish,
            "fail": mock_fail,
            "create": mock_create,
        }


# ---------------------------------------------------------------------------
# Happy path — single entry, no local_root
# ---------------------------------------------------------------------------


class TestHappyPathSingleEntry:
    def test_returns_staging_result(self):
        conn = MagicMock()
        bundle = _make_bundle(entries=(_make_entry(),))

        with _patch_all():
            result = stage_disclosures_bundle(conn, bundle)

        assert isinstance(result, DisclosureStagingResult)
        assert result.staged_count == 1
        assert result.mirrored_count == 0
        assert len(result.artifact_rows) == 1
        assert result.run_id == _RUN_ID
        assert result.data_source == _DS_ROW

    def test_ensure_called_with_source_spec(self):
        conn = MagicMock()
        bundle = _make_bundle(entries=(_make_entry(),))

        with _patch_all() as mocks:
            stage_disclosures_bundle(conn, bundle)

        mocks["ensure"].assert_called_once_with(
            conn,
            "senate-disclosures",
            "Senate Financial Disclosures",
            "official",
            "https://efdsearch.senate.gov",
        )

    def test_ingestion_run_started_with_parameters(self):
        conn = MagicMock()
        bundle = _make_bundle(entries=(_make_entry(),))

        with _patch_all() as mocks:
            stage_disclosures_bundle(conn, bundle)

        mocks["start"].assert_called_once_with(
            conn,
            _DS_ROW["id"],
            "bundle_stage",
            parameters={"source_slug": "senate-disclosures", "entry_count": 1},
        )

    def test_create_artifact_called_with_correct_args(self):
        conn = MagicMock()
        entry = _make_entry()
        bundle = _make_bundle(entries=(entry,))

        with _patch_all() as mocks:
            stage_disclosures_bundle(conn, bundle)

        mocks["create"].assert_called_once_with(
            conn,
            data_source_id=_DS_ROW["id"],
            artifact_kind="pdf",
            storage_uri="disclosures/senate/2024/S000001/DOC1.pdf",
            sha256=_ENTRY_SHA,
            source_url="https://efdsearch.senate.gov/search/view/paper/DOC1/",
            mime_type="application/pdf",
            fetched_at=None,
            source_record_id="DOC1",
            ingestion_run_id=_RUN_ID,
        )

    def test_finish_called_with_row_count(self):
        conn = MagicMock()
        bundle = _make_bundle(entries=(_make_entry(),))

        with _patch_all() as mocks:
            stage_disclosures_bundle(conn, bundle)

        mocks["finish"].assert_called_once_with(conn, _RUN_ID, 1)

    def test_fail_not_called_on_success(self):
        conn = MagicMock()
        bundle = _make_bundle(entries=(_make_entry(),))

        with _patch_all() as mocks:
            stage_disclosures_bundle(conn, bundle)

        mocks["fail"].assert_not_called()


# ---------------------------------------------------------------------------
# local_root is accepted but unused
# ---------------------------------------------------------------------------


class TestLocalRootAccepted:
    def test_mirrored_count_is_zero_with_local_root(self, tmp_path: Path):
        conn = MagicMock()
        bundle = _make_bundle(entries=(_make_entry(), _make_entry()))

        with _patch_all():
            result = stage_disclosures_bundle(conn, bundle, local_root=tmp_path)

        assert result.mirrored_count == 0

    def test_staging_succeeds_with_local_root(self, tmp_path: Path):
        conn = MagicMock()
        bundle = _make_bundle(entries=(_make_entry(),))

        with _patch_all():
            result = stage_disclosures_bundle(conn, bundle, local_root=tmp_path)

        assert result.staged_count == 1


# ---------------------------------------------------------------------------
# Empty bundle — short-circuits without DB writes
# ---------------------------------------------------------------------------


class TestEmptyBundle:
    def test_no_artifact_rows_created(self):
        conn = MagicMock()
        bundle = _make_bundle(entries=())

        result = stage_disclosures_bundle(conn, bundle)

        assert result.staged_count == 0
        assert result.mirrored_count == 0
        assert result.artifact_rows == ()

    def test_no_db_calls_for_empty_bundle(self):
        conn = MagicMock()
        bundle = _make_bundle(entries=())

        with _patch_all() as mocks:
            stage_disclosures_bundle(conn, bundle)

        mocks["ensure"].assert_not_called()
        mocks["start"].assert_not_called()
        mocks["finish"].assert_not_called()
        mocks["create"].assert_not_called()


# ---------------------------------------------------------------------------
# Multiple entries
# ---------------------------------------------------------------------------


class TestMultipleEntries:
    def test_staged_count_matches_entries(self):
        conn = MagicMock()
        entries = tuple(_make_entry() for _ in range(3))
        bundle = _make_bundle(entries=entries)

        with _patch_all() as mocks:
            result = stage_disclosures_bundle(conn, bundle)

        assert result.staged_count == 3
        assert mocks["create"].call_count == 3

    def test_artifact_rows_collected_in_order(self):
        rows = [{"id": i} for i in range(3)]
        conn_mock = MagicMock()
        entries = tuple(_make_entry() for _ in range(3))
        bundle = _make_bundle(entries=entries)

        with _patch_all() as mocks:
            mocks["create"].side_effect = rows
            result = stage_disclosures_bundle(conn_mock, bundle)

        assert list(result.artifact_rows) == rows


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_fail_called_on_create_error(self):
        conn = MagicMock()
        bundle = _make_bundle(entries=(_make_entry(),))

        with _patch_all() as mocks:
            mocks["create"].side_effect = RuntimeError("db error")
            with pytest.raises(RuntimeError, match="db error"):
                stage_disclosures_bundle(conn, bundle)

        mocks["fail"].assert_called_once_with(conn, _RUN_ID, "db error")

    def test_finish_not_called_on_error(self):
        conn = MagicMock()
        bundle = _make_bundle(entries=(_make_entry(),))

        with _patch_all() as mocks:
            mocks["create"].side_effect = RuntimeError("db error")
            with pytest.raises(RuntimeError):
                stage_disclosures_bundle(conn, bundle)

        mocks["finish"].assert_not_called()


# ---------------------------------------------------------------------------
# Unknown source slug
# ---------------------------------------------------------------------------


class TestUnknownSourceSlug:
    def test_raises_key_error_for_unknown_slug(self):
        conn = MagicMock()
        entry = _make_entry(source_slug="not-a-real-source")
        bundle = _make_bundle(entries=(entry,))

        with pytest.raises(KeyError):
            stage_disclosures_bundle(conn, bundle)

    def test_no_ingestion_run_started_for_unknown_slug(self):
        conn = MagicMock()
        entry = _make_entry(source_slug="not-a-real-source")
        bundle = _make_bundle(entries=(entry,))

        with _patch_all() as mocks:
            # source_by_slug is not patched; KeyError raised before start_ingestion_run
            with pytest.raises(KeyError):
                stage_disclosures_bundle(conn, bundle)

        mocks["start"].assert_not_called()


# ---------------------------------------------------------------------------
# Mixed source slugs
# ---------------------------------------------------------------------------


class TestMixedSourceSlugs:
    def test_raises_value_error_for_mixed_slugs(self):
        conn = MagicMock()
        house_entry = _make_entry(source_slug="house-disclosures")
        senate_entry = _make_entry(source_slug="senate-disclosures")
        bundle = _make_bundle(entries=(house_entry, senate_entry))

        with pytest.raises(ValueError, match="multiple source_slugs"):
            stage_disclosures_bundle(conn, bundle)


# ---------------------------------------------------------------------------
# SHA-256 pass-through
# ---------------------------------------------------------------------------


class TestSha256PassThrough:
    def test_sha256_from_entry_forwarded_to_create(self):
        conn = MagicMock()
        custom_sha = "b" * 64
        entry = _make_entry(sha256=custom_sha)
        bundle = _make_bundle(entries=(entry,))

        with _patch_all() as mocks:
            stage_disclosures_bundle(conn, bundle)

        _, kwargs = mocks["create"].call_args
        assert kwargs["sha256"] == custom_sha


# ---------------------------------------------------------------------------
# Alias
# ---------------------------------------------------------------------------


class TestAlias:
    def test_run_disclosures_bundle_stage_is_alias(self):
        assert run_disclosures_bundle_stage is stage_disclosures_bundle
