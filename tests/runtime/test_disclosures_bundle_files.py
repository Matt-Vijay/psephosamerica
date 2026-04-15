"""Tests for src/runtime/disclosures_bundle_files.py

No network calls.  No DB.  All I/O uses tmp_path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.parse.disclosures.acquire import ArtifactKind, ArtifactMeta
from src.parse.disclosures.models import Chamber
from src.runtime.disclosures_bundle import (
    DisclosureArtifactEntry,
    HouseBundledIndexRow,
    SenateBundledIndexRow,
    disclosures_bundle_from_dict,
)
from src.runtime.disclosures_bundle_files import (
    Sha256Mismatch,
    entry_artifact_meta,
    entry_local_path,
    read_and_verify_entry,
    read_entry_bytes,
    verify_entry_sha256,
)

_SHA256 = "a" * 64

_HOUSE_INDEX_ROW = HouseBundledIndexRow(
    last_name="Smith",
    first_name="John",
    suffix="",
    raw_filing_type="O",
    state_dst="CA08",
    filing_date="2024-01-15",
    doc_id="12345",
    filing_kind="annual",
)

_SENATE_INDEX_ROW = SenateBundledIndexRow(
    first_name="Jane",
    last_name="Doe",
    office="Senator, TX",
    report_type="Annual Report for CY2023",
    date_filed="01/15/2024",
    doc_id="uuid-xyz",
)


def _make_house_entry(
    *,
    storage_uri: str = "house/2024/12345.pdf",
    sha256: str = _SHA256,
    artifact_kind: str = "pdf",
) -> DisclosureArtifactEntry:
    return DisclosureArtifactEntry(
        source_record_id="12345",
        chamber="house",
        filing_year=2024,
        storage_uri=storage_uri,
        source_url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/12345.pdf",
        source_slug="house_disclosures",
        artifact_kind=artifact_kind,
        sha256=sha256,
        index_row=_HOUSE_INDEX_ROW,
    )


def _make_senate_entry(*, sha256: str = _SHA256) -> DisclosureArtifactEntry:
    return DisclosureArtifactEntry(
        source_record_id="uuid-xyz",
        chamber="senate",
        filing_year=2024,
        storage_uri="senate/2024/uuid-xyz.pdf",
        source_url="https://efdsearch.senate.gov/search/view/paper/uuid-xyz/",
        source_slug="senate_disclosures",
        artifact_kind="pdf",
        sha256=sha256,
        index_row=_SENATE_INDEX_ROW,
    )


def _write_file(root: Path, uri: str, data: bytes) -> None:
    path = root / uri
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ---------------------------------------------------------------------------
# entry_artifact_meta
# ---------------------------------------------------------------------------


class TestEntryArtifactMeta:
    def test_returns_artifact_meta(self) -> None:
        meta = entry_artifact_meta(_make_house_entry(), bioguide_id="S000001")
        assert isinstance(meta, ArtifactMeta)

    def test_house_chamber(self) -> None:
        meta = entry_artifact_meta(_make_house_entry(), bioguide_id="S000001")
        assert meta.chamber == Chamber.HOUSE

    def test_senate_chamber(self) -> None:
        meta = entry_artifact_meta(_make_senate_entry(), bioguide_id="D000001")
        assert meta.chamber == Chamber.SENATE

    def test_artifact_kind_pdf(self) -> None:
        meta = entry_artifact_meta(_make_house_entry(), bioguide_id="S000001")
        assert meta.artifact_kind == ArtifactKind.PDF

    def test_source_slug_carried(self) -> None:
        meta = entry_artifact_meta(_make_house_entry(), bioguide_id="S000001")
        assert meta.source_slug == "house_disclosures"

    def test_source_url_carried(self) -> None:
        entry = _make_house_entry()
        meta = entry_artifact_meta(entry, bioguide_id="S000001")
        assert meta.source_url == entry.source_url

    def test_storage_key_equals_storage_uri(self) -> None:
        entry = _make_house_entry(storage_uri="house/2024/99.pdf")
        meta = entry_artifact_meta(entry, bioguide_id="S000001")
        assert meta.storage_key == "house/2024/99.pdf"

    def test_member_bioguide_id(self) -> None:
        meta = entry_artifact_meta(_make_house_entry(), bioguide_id="S000001")
        assert meta.member_bioguide_id == "S000001"

    def test_filing_year_carried(self) -> None:
        meta = entry_artifact_meta(_make_house_entry(), bioguide_id="S000001")
        assert meta.filing_year == 2024

    def test_sha256_carried(self) -> None:
        entry = _make_house_entry()
        meta = entry_artifact_meta(entry, bioguide_id="S000001")
        assert meta.sha256 == entry.sha256

    def test_source_record_id_carried(self) -> None:
        entry = _make_house_entry()
        meta = entry_artifact_meta(entry, bioguide_id="S000001")
        assert meta.source_record_id == entry.source_record_id

    def test_unknown_artifact_kind_raises(self) -> None:
        entry = _make_house_entry(
            storage_uri="house/2024/1.xml",
            artifact_kind="xml",
        )
        with pytest.raises(ValueError):
            entry_artifact_meta(entry, bioguide_id="S000001")

    def test_html_artifact_kind_accepted(self) -> None:
        entry = _make_house_entry(
            storage_uri="house/2024/1.html",
            artifact_kind="html",
        )
        meta = entry_artifact_meta(entry, bioguide_id="S000001")
        assert meta.artifact_kind == ArtifactKind.HTML


# ---------------------------------------------------------------------------
# entry_local_path
# ---------------------------------------------------------------------------


class TestEntryLocalPath:
    def test_path_is_local_root_plus_uri(self, tmp_path: Path) -> None:
        entry = _make_house_entry(storage_uri="house/2024/12345.pdf")
        assert entry_local_path(entry, tmp_path) == tmp_path / "house/2024/12345.pdf"

    def test_returns_path_instance(self, tmp_path: Path) -> None:
        result = entry_local_path(_make_house_entry(), tmp_path)
        assert isinstance(result, Path)

    def test_senate_path(self, tmp_path: Path) -> None:
        entry = _make_senate_entry()
        assert entry_local_path(entry, tmp_path) == tmp_path / "senate/2024/uuid-xyz.pdf"

    def test_nested_uri(self, tmp_path: Path) -> None:
        entry = _make_house_entry(storage_uri="a/b/c/d.pdf")
        assert entry_local_path(entry, tmp_path) == tmp_path / "a/b/c/d.pdf"


# ---------------------------------------------------------------------------
# read_entry_bytes
# ---------------------------------------------------------------------------


class TestReadEntryBytes:
    def test_returns_bytes_on_present_file(self, tmp_path: Path) -> None:
        entry = _make_house_entry()
        data = b"pdf-content"
        _write_file(tmp_path, entry.storage_uri, data)
        assert read_entry_bytes(entry, tmp_path) == data

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        entry = _make_house_entry()
        with pytest.raises(FileNotFoundError):
            read_entry_bytes(entry, tmp_path)

    def test_returns_exact_bytes(self, tmp_path: Path) -> None:
        entry = _make_house_entry()
        data = b"\x00\x01\x02" * 100
        _write_file(tmp_path, entry.storage_uri, data)
        assert read_entry_bytes(entry, tmp_path) == data

    def test_senate_file_read(self, tmp_path: Path) -> None:
        entry = _make_senate_entry()
        data = b"senate-bytes"
        _write_file(tmp_path, entry.storage_uri, data)
        assert read_entry_bytes(entry, tmp_path) == data


# ---------------------------------------------------------------------------
# verify_entry_sha256
# ---------------------------------------------------------------------------


class TestVerifyEntrySha256:
    def test_passes_on_correct_digest(self, tmp_path: Path) -> None:
        data = b"real pdf bytes"
        sha256 = hashlib.sha256(data).hexdigest()
        entry = _make_house_entry(sha256=sha256)
        _write_file(tmp_path, entry.storage_uri, data)
        verify_entry_sha256(entry, tmp_path)  # must not raise

    def test_raises_sha256_mismatch_on_wrong_digest(self, tmp_path: Path) -> None:
        data = b"real pdf bytes"
        entry = _make_house_entry(sha256=_SHA256)  # _SHA256 != hash of data
        _write_file(tmp_path, entry.storage_uri, data)
        with pytest.raises(Sha256Mismatch):
            verify_entry_sha256(entry, tmp_path)

    def test_mismatch_message_contains_storage_uri(self, tmp_path: Path) -> None:
        data = b"data"
        entry = _make_house_entry(sha256=_SHA256)
        _write_file(tmp_path, entry.storage_uri, data)
        with pytest.raises(Sha256Mismatch, match="house/2024/12345.pdf"):
            verify_entry_sha256(entry, tmp_path)

    def test_mismatch_is_value_error_subclass(self, tmp_path: Path) -> None:
        data = b"data"
        entry = _make_house_entry(sha256=_SHA256)
        _write_file(tmp_path, entry.storage_uri, data)
        with pytest.raises(ValueError):
            verify_entry_sha256(entry, tmp_path)

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        entry = _make_house_entry()
        with pytest.raises(FileNotFoundError):
            verify_entry_sha256(entry, tmp_path)

    def test_empty_file_with_correct_digest(self, tmp_path: Path) -> None:
        data = b""
        sha256 = hashlib.sha256(data).hexdigest()
        entry = _make_house_entry(sha256=sha256)
        _write_file(tmp_path, entry.storage_uri, data)
        verify_entry_sha256(entry, tmp_path)  # must not raise


# ---------------------------------------------------------------------------
# read_and_verify_entry
# ---------------------------------------------------------------------------


class TestReadAndVerifyEntry:
    def test_returns_bytes_on_correct_digest(self, tmp_path: Path) -> None:
        data = b"valid pdf"
        sha256 = hashlib.sha256(data).hexdigest()
        entry = _make_house_entry(sha256=sha256)
        _write_file(tmp_path, entry.storage_uri, data)
        assert read_and_verify_entry(entry, tmp_path) == data

    def test_raises_sha256_mismatch_on_wrong_digest(self, tmp_path: Path) -> None:
        data = b"valid pdf"
        entry = _make_house_entry(sha256=_SHA256)
        _write_file(tmp_path, entry.storage_uri, data)
        with pytest.raises(Sha256Mismatch):
            read_and_verify_entry(entry, tmp_path)

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        entry = _make_house_entry()
        with pytest.raises(FileNotFoundError):
            read_and_verify_entry(entry, tmp_path)

    def test_senate_entry_returns_bytes(self, tmp_path: Path) -> None:
        data = b"senate pdf"
        sha256 = hashlib.sha256(data).hexdigest()
        entry = _make_senate_entry(sha256=sha256)
        _write_file(tmp_path, entry.storage_uri, data)
        assert read_and_verify_entry(entry, tmp_path) == data

    def test_mismatch_message_contains_storage_uri(self, tmp_path: Path) -> None:
        data = b"pdf"
        entry = _make_house_entry(sha256=_SHA256)
        _write_file(tmp_path, entry.storage_uri, data)
        with pytest.raises(Sha256Mismatch, match="house/2024/12345.pdf"):
            read_and_verify_entry(entry, tmp_path)

    def test_mismatch_is_value_error_subclass(self, tmp_path: Path) -> None:
        data = b"pdf"
        entry = _make_house_entry(sha256=_SHA256)
        _write_file(tmp_path, entry.storage_uri, data)
        with pytest.raises(ValueError):
            read_and_verify_entry(entry, tmp_path)

    def test_large_file_verified(self, tmp_path: Path) -> None:
        data = b"x" * 10_000
        sha256 = hashlib.sha256(data).hexdigest()
        entry = _make_house_entry(sha256=sha256)
        _write_file(tmp_path, entry.storage_uri, data)
        result = read_and_verify_entry(entry, tmp_path)
        assert len(result) == 10_000


# ---------------------------------------------------------------------------
# Bundle-from-JSON end-to-end — canonical contract consumed directly
# ---------------------------------------------------------------------------


def _house_bundle_dict(sha256: str) -> dict:
    """Minimal canonical House bundle JSON dict with a computable sha256."""
    return {
        "artifacts": [
            {
                "source_record_id": "20001",
                "chamber": "house",
                "filing_year": 2024,
                "storage_uri": "house/2024/20001.pdf",
                "source_url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/20001.pdf",
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
                    "doc_id": "20001",
                    "filing_kind": "annual",
                },
            }
        ]
    }


def _senate_bundle_dict(sha256: str) -> dict:
    """Minimal canonical Senate bundle JSON dict with a computable sha256."""
    return {
        "artifacts": [
            {
                "source_record_id": "uuid-e2e",
                "chamber": "senate",
                "filing_year": 2024,
                "storage_uri": "senate/2024/uuid-e2e.pdf",
                "source_url": "https://efdsearch.senate.gov/search/view/paper/uuid-e2e/",
                "source_slug": "senate-disclosures",
                "artifact_kind": "pdf",
                "sha256": sha256,
                "index_row": {
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "office": "Senator, TX",
                    "report_type": "Annual Report for CY2023",
                    "date_filed": "01/15/2024",
                    "doc_id": "uuid-e2e",
                },
            }
        ]
    }


class TestBundleFromJson:
    """Prove the canonical bundle JSON contract flows directly into file ops.

    All entries come from disclosures_bundle_from_dict so the full JSON
    parsing path is exercised — no direct dataclass construction.
    """

    # --- helpers ---

    def _house_entry_with_real_bytes(self, data: bytes) -> DisclosureArtifactEntry:
        sha256 = hashlib.sha256(data).hexdigest()
        bundle = disclosures_bundle_from_dict(_house_bundle_dict(sha256))
        return bundle.artifacts[0]

    def _senate_entry_with_real_bytes(self, data: bytes) -> DisclosureArtifactEntry:
        sha256 = hashlib.sha256(data).hexdigest()
        bundle = disclosures_bundle_from_dict(_senate_bundle_dict(sha256))
        return bundle.artifacts[0]

    # --- canonical contract is consumed directly ---

    def test_bundle_loaded_from_json_has_one_entry(self) -> None:
        sha256 = "a" * 64
        bundle = disclosures_bundle_from_dict(_house_bundle_dict(sha256))
        assert len(bundle.artifacts) == 1

    def test_entry_fields_match_json(self) -> None:
        data = b"pdf bytes for house filer"
        entry = self._house_entry_with_real_bytes(data)
        assert entry.source_record_id == "20001"
        assert entry.chamber == "house"
        assert entry.filing_year == 2024
        assert entry.storage_uri == "house/2024/20001.pdf"
        assert entry.source_slug == "house-disclosures"
        assert entry.artifact_kind == "pdf"

    def test_sha256_in_entry_matches_real_file_bytes(self, tmp_path: Path) -> None:
        data = b"real house pdf content"
        entry = self._house_entry_with_real_bytes(data)
        _write_file(tmp_path, entry.storage_uri, data)
        # verify_entry_sha256 reads the file and compares; must not raise
        verify_entry_sha256(entry, tmp_path)

    def test_tampered_bytes_detected_for_json_bundle(self, tmp_path: Path) -> None:
        data = b"real house pdf content"
        entry = self._house_entry_with_real_bytes(data)
        _write_file(tmp_path, entry.storage_uri, b"tampered bytes")
        with pytest.raises(Sha256Mismatch):
            verify_entry_sha256(entry, tmp_path)

    # --- ArtifactMeta derived correctly from JSON bundle ---

    def test_artifact_meta_chamber_from_json(self) -> None:
        data = b"house pdf"
        entry = self._house_entry_with_real_bytes(data)
        meta = entry_artifact_meta(entry, bioguide_id="S000001")
        assert meta.chamber == Chamber.HOUSE

    def test_artifact_meta_senate_chamber_from_json(self) -> None:
        data = b"senate pdf"
        entry = self._senate_entry_with_real_bytes(data)
        meta = entry_artifact_meta(entry, bioguide_id="D000001")
        assert meta.chamber == Chamber.SENATE

    def test_artifact_meta_sha256_matches_real_bytes(self) -> None:
        data = b"house pdf content"
        entry = self._house_entry_with_real_bytes(data)
        meta = entry_artifact_meta(entry, bioguide_id="S000001")
        assert meta.sha256 == hashlib.sha256(data).hexdigest()

    def test_artifact_meta_storage_key_equals_storage_uri(self) -> None:
        data = b"pdf"
        entry = self._house_entry_with_real_bytes(data)
        meta = entry_artifact_meta(entry, bioguide_id="S000001")
        assert meta.storage_key == entry.storage_uri

    def test_artifact_meta_bioguide_id_threaded_through(self) -> None:
        data = b"pdf"
        entry = self._house_entry_with_real_bytes(data)
        meta = entry_artifact_meta(entry, bioguide_id="P000197")
        assert meta.member_bioguide_id == "P000197"

    def test_artifact_meta_source_url_from_json(self) -> None:
        data = b"pdf"
        entry = self._house_entry_with_real_bytes(data)
        meta = entry_artifact_meta(entry, bioguide_id="S000001")
        assert meta.source_url == entry.source_url

    def test_artifact_meta_filing_year_from_json(self) -> None:
        data = b"pdf"
        entry = self._house_entry_with_real_bytes(data)
        meta = entry_artifact_meta(entry, bioguide_id="S000001")
        assert meta.filing_year == 2024

    # --- SHA-256 verification uses actual file bytes ---

    def test_read_and_verify_returns_real_bytes(self, tmp_path: Path) -> None:
        data = b"deterministic pdf payload"
        entry = self._house_entry_with_real_bytes(data)
        _write_file(tmp_path, entry.storage_uri, data)
        result = read_and_verify_entry(entry, tmp_path)
        assert result == data

    def test_read_and_verify_senate_returns_real_bytes(self, tmp_path: Path) -> None:
        data = b"senate deterministic payload"
        entry = self._senate_entry_with_real_bytes(data)
        _write_file(tmp_path, entry.storage_uri, data)
        result = read_and_verify_entry(entry, tmp_path)
        assert result == data

    def test_missing_file_raises_for_json_bundle_entry(self, tmp_path: Path) -> None:
        data = b"pdf"
        entry = self._house_entry_with_real_bytes(data)
        # file never written
        with pytest.raises(FileNotFoundError):
            read_and_verify_entry(entry, tmp_path)

    def test_local_path_resolves_under_tmp_root(self, tmp_path: Path) -> None:
        data = b"pdf"
        entry = self._house_entry_with_real_bytes(data)
        assert entry_local_path(entry, tmp_path) == tmp_path / "house/2024/20001.pdf"
