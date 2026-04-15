"""End-to-end staging tests driven by real canonical bundle fixtures.

Proves:
- canonical bundle contract is consumed directly (bundle loaded via
  disclosures_bundle_from_dict, not by constructing dataclasses by hand)
- ArtifactMeta is derived correctly from bundle entries
- SHA-256 verification uses actual file bytes (written to tmp_path)
- staging rows reflect real bundle entries (sha256, storage_uri,
  source_url, source_record_id all match the bundle JSON)

DB boundary (ensure_data_source, start_ingestion_run, finish_ingestion_run,
fail_ingestion_run, create_source_artifact) is patched with unittest.mock.patch
so no live database is required.  No sys.modules injection anywhere.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.parse.disclosures.acquire import ArtifactKind
from src.parse.disclosures.models import Chamber
from src.runtime.disclosures_bundle import (
    DisclosuresBundle,
    disclosures_bundle_from_dict,
)
from src.runtime.disclosures_bundle_files import (
    entry_artifact_meta,
    read_and_verify_entry,
    verify_entry_sha256,
)
from src.runtime.disclosures_stage import stage_disclosures_bundle

# ---------------------------------------------------------------------------
# Patch targets (DB boundary only — no sys.modules tricks)
# ---------------------------------------------------------------------------

_ENSURE = "src.runtime.disclosures_stage.ensure_data_source"
_START = "src.runtime.disclosures_stage.start_ingestion_run"
_FINISH = "src.runtime.disclosures_stage.finish_ingestion_run"
_FAIL = "src.runtime.disclosures_stage.fail_ingestion_run"
_CREATE = "src.runtime.disclosures_stage.create_source_artifact"

_RUN_ID = 7
_DS_ROW: dict[str, Any] = {"id": 3, "slug": "senate-disclosures"}

# ---------------------------------------------------------------------------
# Canonical bundle JSON builders — all sha256 values computed from real bytes
# ---------------------------------------------------------------------------


def _senate_bundle_json(storage_uri: str, data: bytes) -> dict[str, Any]:
    """Return a canonical artifact bundle dict for one Senate entry."""
    sha256 = hashlib.sha256(data).hexdigest()
    return {
        "artifacts": [
            {
                "source_record_id": "UUID-E2E-1",
                "chamber": "senate",
                "filing_year": 2024,
                "storage_uri": storage_uri,
                "source_url": "https://efdsearch.senate.gov/search/view/paper/UUID-E2E-1/",
                "source_slug": "senate-disclosures",
                "artifact_kind": "pdf",
                "sha256": sha256,
                "index_row": {
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "office": "Senator, TX",
                    "report_type": "Annual Report for CY2023",
                    "date_filed": "01/15/2024",
                    "doc_id": "UUID-E2E-1",
                },
            }
        ]
    }


def _house_bundle_json(storage_uri: str, data: bytes) -> dict[str, Any]:
    """Return a canonical artifact bundle dict for one House entry."""
    sha256 = hashlib.sha256(data).hexdigest()
    return {
        "artifacts": [
            {
                "source_record_id": "30001",
                "chamber": "house",
                "filing_year": 2024,
                "storage_uri": storage_uri,
                "source_url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/30001.pdf",
                "source_slug": "house-disclosures",
                "artifact_kind": "pdf",
                "sha256": sha256,
                "index_row": {
                    "last_name": "Smith",
                    "first_name": "John",
                    "suffix": "",
                    "raw_filing_type": "O",
                    "state_dst": "CA08",
                    "filing_date": "2024-01-15",
                    "doc_id": "30001",
                    "filing_kind": "annual",
                },
            }
        ]
    }


def _multi_entry_senate_bundle_json(entries: list[tuple[str, bytes]]) -> dict[str, Any]:
    """Return a canonical bundle dict for multiple Senate entries.

    entries: list of (storage_uri, data) tuples.
    """
    artifacts = []
    for i, (storage_uri, data) in enumerate(entries):
        sha256 = hashlib.sha256(data).hexdigest()
        doc_id = f"UUID-MULTI-{i}"
        artifacts.append(
            {
                "source_record_id": doc_id,
                "chamber": "senate",
                "filing_year": 2024,
                "storage_uri": storage_uri,
                "source_url": f"https://efdsearch.senate.gov/search/view/paper/{doc_id}/",
                "source_slug": "senate-disclosures",
                "artifact_kind": "pdf",
                "sha256": sha256,
                "index_row": {
                    "first_name": "Jane",
                    "last_name": f"Doe{i}",
                    "office": "Senator, TX",
                    "report_type": "Annual Report for CY2023",
                    "date_filed": "01/15/2024",
                    "doc_id": doc_id,
                },
            }
        )
    return {"artifacts": artifacts}


# ---------------------------------------------------------------------------
# File-writing helper
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, uri: str, data: bytes) -> Path:
    p = tmp_path / uri
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# ---------------------------------------------------------------------------
# DB patch context manager
# ---------------------------------------------------------------------------


@contextmanager
def _patch_db(
    ds_row: dict[str, Any] | None = None,
    run_id: int = _RUN_ID,
    artifact_row: dict[str, Any] | None = None,
):
    """Patch all five DB boundary functions and yield the mock handles."""
    default_artifact: dict[str, Any] = {
        "id": 99,
        "data_source_id": (ds_row or _DS_ROW)["id"],
        "artifact_kind": "pdf",
        "sha256": "placeholder",
        "ingestion_run_id": run_id,
    }
    with (
        patch(_ENSURE, return_value=ds_row or _DS_ROW) as m_ensure,
        patch(_START, return_value=run_id) as m_start,
        patch(_FINISH) as m_finish,
        patch(_FAIL) as m_fail,
        patch(_CREATE, return_value=artifact_row or default_artifact) as m_create,
    ):
        yield {
            "ensure": m_ensure,
            "start": m_start,
            "finish": m_finish,
            "fail": m_fail,
            "create": m_create,
        }


# ---------------------------------------------------------------------------
# Helper: make create_source_artifact echo back the sha256 from its kwargs
# so we can assert the real sha256 reaches the DB boundary
# ---------------------------------------------------------------------------


def _echo_sha256_row(ds_id: int, run_id: int) -> Any:
    """Return a side_effect for create_source_artifact that mirrors sha256."""
    counter = [0]

    def _side_effect(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        counter[0] += 1
        return {
            "id": counter[0],
            "data_source_id": ds_id,
            "artifact_kind": kwargs.get("artifact_kind", "pdf"),
            "storage_uri": kwargs.get("storage_uri", ""),
            "sha256": kwargs.get("sha256", ""),
            "source_url": kwargs.get("source_url", ""),
            "source_record_id": kwargs.get("source_record_id", ""),
            "ingestion_run_id": run_id,
        }

    return _side_effect


# ---------------------------------------------------------------------------
# TestBundleContractConsumedDirectly
# ---------------------------------------------------------------------------


class TestBundleContractConsumedDirectly:
    """Bundle is built from canonical JSON — no direct dataclass construction."""

    def test_bundle_parsed_from_json_has_correct_sha256(self) -> None:
        data = b"senate annual disclosure pdf"
        bundle_dict = _senate_bundle_json("senate/2024/UUID-E2E-1.pdf", data)
        bundle = disclosures_bundle_from_dict(bundle_dict)
        entry = bundle.artifacts[0]
        assert entry.sha256 == hashlib.sha256(data).hexdigest()

    def test_bundle_parsed_from_json_has_correct_source_record_id(self) -> None:
        data = b"pdf bytes"
        bundle_dict = _senate_bundle_json("senate/2024/UUID-E2E-1.pdf", data)
        bundle = disclosures_bundle_from_dict(bundle_dict)
        assert bundle.artifacts[0].source_record_id == "UUID-E2E-1"

    def test_bundle_parsed_from_json_has_correct_storage_uri(self) -> None:
        data = b"pdf bytes"
        storage_uri = "senate/2024/UUID-E2E-1.pdf"
        bundle_dict = _senate_bundle_json(storage_uri, data)
        bundle = disclosures_bundle_from_dict(bundle_dict)
        assert bundle.artifacts[0].storage_uri == storage_uri

    def test_bundle_loaded_from_path(self, tmp_path: Path) -> None:
        data = b"pdf bytes"
        bundle_dict = _senate_bundle_json("senate/2024/UUID-E2E-1.pdf", data)
        bundle_path = tmp_path / "bundle.json"
        bundle_path.write_text(json.dumps(bundle_dict), encoding="utf-8")
        from src.runtime.disclosures_bundle import load_disclosures_bundle

        bundle = load_disclosures_bundle(bundle_path)
        assert len(bundle.artifacts) == 1
        assert bundle.artifacts[0].sha256 == hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# TestArtifactMetaDerivedCorrectly
# ---------------------------------------------------------------------------


class TestArtifactMetaDerivedCorrectly:
    """ArtifactMeta is derived from real bundle entries, not hand-constructed."""

    def test_house_entry_yields_house_chamber(self) -> None:
        data = b"house pdf"
        bundle = disclosures_bundle_from_dict(
            _house_bundle_json("house/2024/30001.pdf", data)
        )
        meta = entry_artifact_meta(bundle.artifacts[0], bioguide_id="S000001")
        assert meta.chamber == Chamber.HOUSE

    def test_senate_entry_yields_senate_chamber(self) -> None:
        data = b"senate pdf"
        bundle = disclosures_bundle_from_dict(
            _senate_bundle_json("senate/2024/UUID-E2E-1.pdf", data)
        )
        meta = entry_artifact_meta(bundle.artifacts[0], bioguide_id="D000001")
        assert meta.chamber == Chamber.SENATE

    def test_artifact_kind_pdf(self) -> None:
        data = b"pdf"
        bundle = disclosures_bundle_from_dict(
            _senate_bundle_json("senate/2024/UUID-E2E-1.pdf", data)
        )
        meta = entry_artifact_meta(bundle.artifacts[0], bioguide_id="D000001")
        assert meta.artifact_kind == ArtifactKind.PDF

    def test_sha256_in_meta_matches_real_bytes(self) -> None:
        data = b"deterministic pdf payload"
        bundle = disclosures_bundle_from_dict(
            _senate_bundle_json("senate/2024/UUID-E2E-1.pdf", data)
        )
        meta = entry_artifact_meta(bundle.artifacts[0], bioguide_id="D000001")
        assert meta.sha256 == hashlib.sha256(data).hexdigest()

    def test_storage_key_equals_storage_uri(self) -> None:
        data = b"pdf"
        storage_uri = "senate/2024/UUID-E2E-1.pdf"
        bundle = disclosures_bundle_from_dict(_senate_bundle_json(storage_uri, data))
        meta = entry_artifact_meta(bundle.artifacts[0], bioguide_id="D000001")
        assert meta.storage_key == storage_uri

    def test_member_bioguide_id_threaded_into_meta(self) -> None:
        data = b"pdf"
        bundle = disclosures_bundle_from_dict(
            _senate_bundle_json("senate/2024/UUID-E2E-1.pdf", data)
        )
        meta = entry_artifact_meta(bundle.artifacts[0], bioguide_id="W000802")
        assert meta.member_bioguide_id == "W000802"

    def test_source_url_carried_into_meta(self) -> None:
        data = b"pdf"
        bundle_dict = _senate_bundle_json("senate/2024/UUID-E2E-1.pdf", data)
        expected_url = bundle_dict["artifacts"][0]["source_url"]
        bundle = disclosures_bundle_from_dict(bundle_dict)
        meta = entry_artifact_meta(bundle.artifacts[0], bioguide_id="D000001")
        assert meta.source_url == expected_url

    def test_filing_year_carried_into_meta(self) -> None:
        data = b"pdf"
        bundle = disclosures_bundle_from_dict(
            _senate_bundle_json("senate/2024/UUID-E2E-1.pdf", data)
        )
        meta = entry_artifact_meta(bundle.artifacts[0], bioguide_id="D000001")
        assert meta.filing_year == 2024


# ---------------------------------------------------------------------------
# TestSha256UsesActualFileBytes
# ---------------------------------------------------------------------------


class TestSha256UsesActualFileBytes:
    """SHA-256 verification reads the on-disk file, not a cached value."""

    def test_verify_passes_when_bytes_match(self, tmp_path: Path) -> None:
        data = b"exact senate filing content"
        storage_uri = "senate/2024/UUID-E2E-1.pdf"
        bundle = disclosures_bundle_from_dict(_senate_bundle_json(storage_uri, data))
        entry = bundle.artifacts[0]
        _write(tmp_path, storage_uri, data)
        verify_entry_sha256(entry, tmp_path)  # must not raise

    def test_verify_fails_when_bytes_differ(self, tmp_path: Path) -> None:
        from src.runtime.disclosures_bundle_files import Sha256Mismatch

        data = b"original filing"
        storage_uri = "senate/2024/UUID-E2E-1.pdf"
        bundle = disclosures_bundle_from_dict(_senate_bundle_json(storage_uri, data))
        entry = bundle.artifacts[0]
        _write(tmp_path, storage_uri, b"modified bytes - tampered")
        with pytest.raises(Sha256Mismatch):
            verify_entry_sha256(entry, tmp_path)

    def test_read_and_verify_returns_exact_bytes(self, tmp_path: Path) -> None:
        data = b"complete filing pdf bytes"
        storage_uri = "senate/2024/UUID-E2E-1.pdf"
        bundle = disclosures_bundle_from_dict(_senate_bundle_json(storage_uri, data))
        entry = bundle.artifacts[0]
        _write(tmp_path, storage_uri, data)
        result = read_and_verify_entry(entry, tmp_path)
        assert result == data

    def test_house_verify_passes(self, tmp_path: Path) -> None:
        data = b"house pdf filing"
        storage_uri = "house/2024/30001.pdf"
        bundle = disclosures_bundle_from_dict(_house_bundle_json(storage_uri, data))
        entry = bundle.artifacts[0]
        _write(tmp_path, storage_uri, data)
        verify_entry_sha256(entry, tmp_path)  # must not raise

    def test_empty_file_verified_against_empty_hash(self, tmp_path: Path) -> None:
        data = b""
        storage_uri = "senate/2024/UUID-E2E-1.pdf"
        bundle = disclosures_bundle_from_dict(_senate_bundle_json(storage_uri, data))
        entry = bundle.artifacts[0]
        _write(tmp_path, storage_uri, data)
        verify_entry_sha256(entry, tmp_path)  # must not raise

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        data = b"pdf"
        storage_uri = "senate/2024/UUID-E2E-1.pdf"
        bundle = disclosures_bundle_from_dict(_senate_bundle_json(storage_uri, data))
        entry = bundle.artifacts[0]
        # file is never written
        with pytest.raises(FileNotFoundError):
            verify_entry_sha256(entry, tmp_path)


# ---------------------------------------------------------------------------
# TestStagingRowsReflectRealBundleEntries
# ---------------------------------------------------------------------------


class TestStagingRowsReflectRealBundleEntries:
    """stage_disclosures_bundle creates artifact rows whose fields come from
    the real bundle entries loaded from canonical JSON."""

    def _make_senate_bundle(self, data: bytes) -> DisclosuresBundle:
        bundle_dict = _senate_bundle_json("senate/2024/UUID-E2E-1.pdf", data)
        return disclosures_bundle_from_dict(bundle_dict)

    def test_staged_count_matches_bundle_entries(self) -> None:
        conn = MagicMock()
        bundle = self._make_senate_bundle(b"filing")
        ds_row: dict[str, Any] = {"id": 10, "slug": "senate-disclosures"}
        with _patch_db(ds_row=ds_row) as mocks:
            mocks["create"].side_effect = _echo_sha256_row(10, _RUN_ID)
            result = stage_disclosures_bundle(conn, bundle)
        assert result.staged_count == 1
        assert len(result.artifact_rows) == 1

    def test_artifact_row_sha256_matches_real_bytes(self) -> None:
        conn = MagicMock()
        data = b"deterministic pdf payload for sha256 test"
        expected_sha256 = hashlib.sha256(data).hexdigest()
        bundle = self._make_senate_bundle(data)
        ds_row: dict[str, Any] = {"id": 10, "slug": "senate-disclosures"}
        with _patch_db(ds_row=ds_row) as mocks:
            mocks["create"].side_effect = _echo_sha256_row(10, _RUN_ID)
            result = stage_disclosures_bundle(conn, bundle)
        assert result.artifact_rows[0]["sha256"] == expected_sha256

    def test_create_called_with_sha256_from_real_bundle(self) -> None:
        conn = MagicMock()
        data = b"senate filing bytes"
        expected_sha256 = hashlib.sha256(data).hexdigest()
        bundle = self._make_senate_bundle(data)
        ds_row: dict[str, Any] = {"id": 10, "slug": "senate-disclosures"}
        with _patch_db(ds_row=ds_row) as mocks:
            stage_disclosures_bundle(conn, bundle)
        _, kwargs = mocks["create"].call_args
        assert kwargs["sha256"] == expected_sha256

    def test_create_called_with_storage_uri_from_bundle(self) -> None:
        conn = MagicMock()
        data = b"pdf"
        bundle = self._make_senate_bundle(data)
        ds_row: dict[str, Any] = {"id": 10, "slug": "senate-disclosures"}
        with _patch_db(ds_row=ds_row) as mocks:
            stage_disclosures_bundle(conn, bundle)
        _, kwargs = mocks["create"].call_args
        assert kwargs["storage_uri"] == "senate/2024/UUID-E2E-1.pdf"

    def test_create_called_with_source_url_from_bundle(self) -> None:
        conn = MagicMock()
        data = b"pdf"
        bundle_dict = _senate_bundle_json("senate/2024/UUID-E2E-1.pdf", data)
        expected_url = bundle_dict["artifacts"][0]["source_url"]
        bundle = disclosures_bundle_from_dict(bundle_dict)
        ds_row: dict[str, Any] = {"id": 10, "slug": "senate-disclosures"}
        with _patch_db(ds_row=ds_row) as mocks:
            stage_disclosures_bundle(conn, bundle)
        _, kwargs = mocks["create"].call_args
        assert kwargs["source_url"] == expected_url

    def test_create_called_with_source_record_id_from_bundle(self) -> None:
        conn = MagicMock()
        data = b"pdf"
        bundle = self._make_senate_bundle(data)
        ds_row: dict[str, Any] = {"id": 10, "slug": "senate-disclosures"}
        with _patch_db(ds_row=ds_row) as mocks:
            stage_disclosures_bundle(conn, bundle)
        _, kwargs = mocks["create"].call_args
        assert kwargs["source_record_id"] == "UUID-E2E-1"

    def test_create_called_with_pdf_mime_type(self) -> None:
        conn = MagicMock()
        data = b"pdf"
        bundle = self._make_senate_bundle(data)
        ds_row: dict[str, Any] = {"id": 10, "slug": "senate-disclosures"}
        with _patch_db(ds_row=ds_row) as mocks:
            stage_disclosures_bundle(conn, bundle)
        _, kwargs = mocks["create"].call_args
        assert kwargs["mime_type"] == "application/pdf"

    def test_run_id_in_staging_result(self) -> None:
        conn = MagicMock()
        data = b"pdf"
        bundle = self._make_senate_bundle(data)
        with _patch_db(run_id=42) as _mocks:
            result = stage_disclosures_bundle(conn, bundle)
        assert result.run_id == 42

    def test_data_source_in_staging_result(self) -> None:
        conn = MagicMock()
        data = b"pdf"
        bundle = self._make_senate_bundle(data)
        ds_row: dict[str, Any] = {"id": 10, "slug": "senate-disclosures"}
        with _patch_db(ds_row=ds_row) as _mocks:
            result = stage_disclosures_bundle(conn, bundle)
        assert result.data_source == ds_row

    def test_mirrored_count_is_zero(self) -> None:
        conn = MagicMock()
        data = b"pdf"
        bundle = self._make_senate_bundle(data)
        with _patch_db() as _mocks:
            result = stage_disclosures_bundle(conn, bundle)
        assert result.mirrored_count == 0

    def test_finish_called_with_entry_count(self) -> None:
        conn = MagicMock()
        data = b"pdf"
        bundle = self._make_senate_bundle(data)
        with _patch_db() as mocks:
            stage_disclosures_bundle(conn, bundle)
        mocks["finish"].assert_called_once_with(conn, _RUN_ID, 1)

    # --- multi-entry bundle ---

    def test_multi_entry_bundle_all_sha256_forwarded(self) -> None:
        conn = MagicMock()
        payloads = [b"first filing", b"second filing", b"third filing"]
        entries = [
            (f"senate/2024/UUID-MULTI-{i}.pdf", data)
            for i, data in enumerate(payloads)
        ]
        bundle_dict = _multi_entry_senate_bundle_json(entries)
        bundle = disclosures_bundle_from_dict(bundle_dict)
        ds_row: dict[str, Any] = {"id": 10, "slug": "senate-disclosures"}
        with _patch_db(ds_row=ds_row) as mocks:
            mocks["create"].side_effect = _echo_sha256_row(10, _RUN_ID)
            result = stage_disclosures_bundle(conn, bundle)

        assert result.staged_count == 3
        forwarded_hashes = [row["sha256"] for row in result.artifact_rows]
        expected_hashes = [hashlib.sha256(d).hexdigest() for d in payloads]
        assert forwarded_hashes == expected_hashes

    def test_multi_entry_storage_uris_forwarded_in_order(self) -> None:
        conn = MagicMock()
        payloads = [b"a", b"b"]
        entries = [
            (f"senate/2024/UUID-MULTI-{i}.pdf", data)
            for i, data in enumerate(payloads)
        ]
        bundle_dict = _multi_entry_senate_bundle_json(entries)
        bundle = disclosures_bundle_from_dict(bundle_dict)
        ds_row: dict[str, Any] = {"id": 10, "slug": "senate-disclosures"}
        with _patch_db(ds_row=ds_row) as mocks:
            mocks["create"].side_effect = _echo_sha256_row(10, _RUN_ID)
            result = stage_disclosures_bundle(conn, bundle)

        uris = [row["storage_uri"] for row in result.artifact_rows]
        assert uris == [
            "senate/2024/UUID-MULTI-0.pdf",
            "senate/2024/UUID-MULTI-1.pdf",
        ]
