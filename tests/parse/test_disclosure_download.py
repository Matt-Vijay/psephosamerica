"""Tests for parse.disclosures.download.

No network calls; httpx and provenance boundaries are mocked.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.parse.disclosures.acquire import ArtifactKind, ArtifactMeta
from src.parse.disclosures.download import (
    download_artifact_bytes,
    sha256_bytes,
    store_downloaded_artifact,
)
from src.parse.disclosures.models import Chamber


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PDF_BYTES = b"%PDF-1.4 fake pdf content"
FIXED_NOW = datetime(2025, 9, 1, 10, 0, 0, tzinfo=timezone.utc)

HOUSE_META = ArtifactMeta(
    source_slug="house-disclosures",
    chamber=Chamber.HOUSE,
    artifact_kind=ArtifactKind.PDF,
    source_url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC123.pdf",
    storage_key="disclosures/house/2024/B000001/DOC123.pdf",
    member_bioguide_id="B000001",
    filing_year=2024,
    source_record_id="DOC123",
    fetched_at=FIXED_NOW,
)

ARTIFACT_ROW = {
    "id": 42,
    "data_source_id": 5,
    "artifact_kind": "pdf",
    "storage_uri": HOUSE_META.storage_key,
    "sha256": hashlib.sha256(PDF_BYTES).hexdigest(),
    "source_url": HOUSE_META.source_url,
    "mime_type": "application/pdf",
    "fetched_at": FIXED_NOW,
    "source_record_id": "DOC123",
}


# ---------------------------------------------------------------------------
# download_artifact_bytes
# ---------------------------------------------------------------------------


class TestDownloadArtifactBytes:
    def test_returns_response_content(self):
        mock_response = MagicMock()
        mock_response.content = PDF_BYTES
        mock_response.raise_for_status = MagicMock()
        with patch("httpx.get", return_value=mock_response) as mock_get:
            result = download_artifact_bytes("https://example.com/doc.pdf")
        assert result == PDF_BYTES
        mock_get.assert_called_once_with(
            "https://example.com/doc.pdf", timeout=30.0, follow_redirects=True
        )

    def test_custom_timeout_forwarded(self):
        mock_response = MagicMock()
        mock_response.content = b"data"
        mock_response.raise_for_status = MagicMock()
        with patch("httpx.get", return_value=mock_response) as mock_get:
            download_artifact_bytes("https://example.com/x.pdf", timeout=60.0)
        assert mock_get.call_args[1]["timeout"] == 60.0

    def test_raises_on_http_error(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        with patch("httpx.get", return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError):
                download_artifact_bytes("https://example.com/missing.pdf")


# ---------------------------------------------------------------------------
# sha256_bytes
# ---------------------------------------------------------------------------


class TestSha256Bytes:
    def test_known_digest(self):
        data = b"hello"
        expected = hashlib.sha256(b"hello").hexdigest()
        assert sha256_bytes(data) == expected

    def test_returns_64_char_hex(self):
        digest = sha256_bytes(b"openpact")
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_empty_bytes(self):
        expected = hashlib.sha256(b"").hexdigest()
        assert sha256_bytes(b"") == expected

    def test_different_inputs_produce_different_digests(self):
        assert sha256_bytes(b"a") != sha256_bytes(b"b")


# ---------------------------------------------------------------------------
# store_downloaded_artifact
# ---------------------------------------------------------------------------


def _make_conn():
    conn = MagicMock()
    cursor_cm = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = {"id": 42}
    cur.fetchall.return_value = []
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor_cm
    return conn


class TestStoreDownloadedArtifact:
    def test_returns_artifact_row(self):
        conn = _make_conn()
        with patch(
            "src.parse.disclosures.download._fetch_data_source_id", return_value=5
        ), patch(
            "src.parse.disclosures.download.create_source_artifact",
            return_value=ARTIFACT_ROW,
        ):
            row = store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)
        assert row["id"] == 42
        assert row["artifact_kind"] == "pdf"

    def test_sha256_computed_from_data(self):
        conn = _make_conn()
        expected_digest = hashlib.sha256(PDF_BYTES).hexdigest()
        captured = {}
        def _capture(conn, *, sha256, **kw):
            captured["sha256"] = sha256
            return ARTIFACT_ROW
        with patch(
            "src.parse.disclosures.download._fetch_data_source_id", return_value=5
        ), patch(
            "src.parse.disclosures.download.create_source_artifact",
            side_effect=_capture,
        ):
            store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)
        assert captured["sha256"] == expected_digest

    def test_storage_uri_is_storage_key(self):
        conn = _make_conn()
        captured = {}
        def _capture(conn, *, storage_uri, **kw):
            captured["storage_uri"] = storage_uri
            return ARTIFACT_ROW
        with patch(
            "src.parse.disclosures.download._fetch_data_source_id", return_value=5
        ), patch(
            "src.parse.disclosures.download.create_source_artifact",
            side_effect=_capture,
        ):
            store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)
        assert captured["storage_uri"] == HOUSE_META.storage_key

    def test_mime_type_pdf(self):
        conn = _make_conn()
        captured = {}
        def _capture(conn, *, mime_type, **kw):
            captured["mime_type"] = mime_type
            return ARTIFACT_ROW
        with patch(
            "src.parse.disclosures.download._fetch_data_source_id", return_value=5
        ), patch(
            "src.parse.disclosures.download.create_source_artifact",
            side_effect=_capture,
        ):
            store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)
        assert captured["mime_type"] == "application/pdf"

    def test_mime_type_html(self):
        conn = _make_conn()
        html_meta = ArtifactMeta(
            source_slug="senate-disclosures",
            chamber=Chamber.SENATE,
            artifact_kind=ArtifactKind.HTML,
            source_url="https://efdsearch.senate.gov/search/view/paper/XYZ/",
            storage_key="disclosures/senate/2024/S000001/XYZ.html",
            member_bioguide_id="S000001",
            filing_year=2024,
            source_record_id="XYZ",
        )
        captured = {}
        def _capture(conn, *, mime_type, **kw):
            captured["mime_type"] = mime_type
            return {**ARTIFACT_ROW, "mime_type": mime_type}
        with patch(
            "src.parse.disclosures.download._fetch_data_source_id", return_value=6
        ), patch(
            "src.parse.disclosures.download.create_source_artifact",
            side_effect=_capture,
        ):
            store_downloaded_artifact(conn, html_meta, b"<html></html>")
        assert captured["mime_type"] == "text/html"

    def test_source_record_id_forwarded(self):
        conn = _make_conn()
        captured = {}
        def _capture(conn, *, source_record_id, **kw):
            captured["source_record_id"] = source_record_id
            return ARTIFACT_ROW
        with patch(
            "src.parse.disclosures.download._fetch_data_source_id", return_value=5
        ), patch(
            "src.parse.disclosures.download.create_source_artifact",
            side_effect=_capture,
        ):
            store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)
        assert captured["source_record_id"] == "DOC123"

    def test_fetched_at_forwarded(self):
        conn = _make_conn()
        captured = {}
        def _capture(conn, *, fetched_at, **kw):
            captured["fetched_at"] = fetched_at
            return ARTIFACT_ROW
        with patch(
            "src.parse.disclosures.download._fetch_data_source_id", return_value=5
        ), patch(
            "src.parse.disclosures.download.create_source_artifact",
            side_effect=_capture,
        ):
            store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)
        assert captured["fetched_at"] == FIXED_NOW

    def test_raises_when_data_source_missing(self):
        conn = _make_conn()
        with patch(
            "src.parse.disclosures.download.fetch_all", return_value=[]
        ):
            with pytest.raises(ValueError, match="data_source slug not found"):
                store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)
