"""Tests for parse.disclosures.artifact_store.

No network calls; all I/O uses tmp_path.
"""

from __future__ import annotations

import hashlib

import pytest

from src.parse.disclosures.acquire import ArtifactKind, ArtifactMeta
from src.parse.disclosures.artifact_store import (
    artifact_path,
    read_artifact,
    verify_artifact,
    write_artifact,
)
from src.parse.disclosures.models import Chamber

PDF_BYTES = b"%PDF-1.4 fake pdf content"

HOUSE_META = ArtifactMeta(
    source_slug="house-disclosures",
    chamber=Chamber.HOUSE,
    artifact_kind=ArtifactKind.PDF,
    source_url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC123.pdf",
    storage_key="disclosures/house/2024/B000001/DOC123.pdf",
    member_bioguide_id="B000001",
    filing_year=2024,
    source_record_id="DOC123",
)

SENATE_META = ArtifactMeta(
    source_slug="senate-disclosures",
    chamber=Chamber.SENATE,
    artifact_kind=ArtifactKind.PDF,
    source_url="https://efdsearch.senate.gov/search/view/paper/SEN456/",
    storage_key="disclosures/senate/2023/S000002/SEN456.pdf",
    member_bioguide_id="S000002",
    filing_year=2023,
    source_record_id="SEN456",
)


class TestArtifactPath:
    def test_path_equals_root_plus_storage_key(self, tmp_path):
        p = artifact_path(tmp_path, HOUSE_META)
        assert p == tmp_path / HOUSE_META.storage_key

    def test_senate_path_uses_senate_key(self, tmp_path):
        p = artifact_path(tmp_path, SENATE_META)
        assert p == tmp_path / SENATE_META.storage_key

    def test_path_components_match_meta_fields(self, tmp_path):
        p = artifact_path(tmp_path, HOUSE_META)
        assert "house" in p.parts
        assert "2024" in p.parts
        assert "B000001" in p.parts
        assert p.name == "DOC123.pdf"


class TestWriteArtifact:
    def test_creates_file_with_correct_content(self, tmp_path):
        write_artifact(tmp_path, HOUSE_META, PDF_BYTES)
        dest = tmp_path / HOUSE_META.storage_key
        assert dest.read_bytes() == PDF_BYTES

    def test_creates_intermediate_directories(self, tmp_path):
        write_artifact(tmp_path, HOUSE_META, PDF_BYTES)
        assert (tmp_path / "disclosures" / "house" / "2024" / "B000001").is_dir()

    def test_returns_destination_path(self, tmp_path):
        dest = write_artifact(tmp_path, HOUSE_META, PDF_BYTES)
        assert dest == tmp_path / HOUSE_META.storage_key

    def test_overwrites_existing_file(self, tmp_path):
        write_artifact(tmp_path, HOUSE_META, b"old")
        write_artifact(tmp_path, HOUSE_META, PDF_BYTES)
        assert (tmp_path / HOUSE_META.storage_key).read_bytes() == PDF_BYTES

    def test_senate_artifact_written_to_senate_path(self, tmp_path):
        write_artifact(tmp_path, SENATE_META, b"senate data")
        dest = tmp_path / SENATE_META.storage_key
        assert dest.read_bytes() == b"senate data"


class TestReadArtifact:
    def test_returns_written_bytes(self, tmp_path):
        write_artifact(tmp_path, HOUSE_META, PDF_BYTES)
        result = read_artifact(tmp_path, HOUSE_META)
        assert result == PDF_BYTES

    def test_raises_file_not_found_when_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_artifact(tmp_path, HOUSE_META)

    def test_error_message_contains_path(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="DOC123.pdf"):
            read_artifact(tmp_path, HOUSE_META)

    def test_round_trip_preserves_binary_content(self, tmp_path):
        data = bytes(range(256))
        write_artifact(tmp_path, SENATE_META, data)
        assert read_artifact(tmp_path, SENATE_META) == data


class TestVerifyArtifact:
    def test_returns_true_for_matching_digest(self, tmp_path):
        write_artifact(tmp_path, HOUSE_META, PDF_BYTES)
        expected = hashlib.sha256(PDF_BYTES).hexdigest()
        assert verify_artifact(tmp_path, HOUSE_META, expected) is True

    def test_returns_false_for_wrong_digest(self, tmp_path):
        write_artifact(tmp_path, HOUSE_META, PDF_BYTES)
        assert verify_artifact(tmp_path, HOUSE_META, "a" * 64) is False

    def test_raises_when_file_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            verify_artifact(tmp_path, HOUSE_META, "a" * 64)

    def test_different_content_produces_false(self, tmp_path):
        write_artifact(tmp_path, HOUSE_META, PDF_BYTES)
        wrong_digest = hashlib.sha256(b"other content").hexdigest()
        assert verify_artifact(tmp_path, HOUSE_META, wrong_digest) is False
