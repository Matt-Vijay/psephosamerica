"""Tests for parse.disclosures.download.

No network calls; httpx and provenance boundaries are mocked.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
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
FIXED_NOW = datetime(2025, 9, 1, 10, 0, 0, tzinfo=UTC)

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
    HOUSE_PTR_URL = "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC123.pdf"
    HOUSE_ANNUAL_URL = "https://disclosures.house.gov/public_disc/financial-pdfs/2024/DOC123.pdf"
    SENATE_URL = "https://efdsearch.senate.gov/search/view/paper/XYZ/"

    def _stream_response(
        self,
        *,
        chunks: list[bytes] | None = None,
        headers: dict[str, str] | None = None,
        status_code: int = 200,
        raise_error: Exception | None = None,
        url: str | None = None,
    ) -> MagicMock:
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.headers = headers or {}
        response.status_code = status_code
        response.url = url
        response.iter_bytes.return_value = chunks if chunks is not None else [PDF_BYTES]
        if raise_error is not None:
            response.raise_for_status.side_effect = raise_error
        return response

    def test_returns_response_content(self):
        mock_response = self._stream_response()
        with (
            patch("httpx.get", side_effect=AssertionError("unsafe direct get")),
            patch("httpx.stream", return_value=mock_response) as mock_stream,
        ):
            result = download_artifact_bytes(self.HOUSE_PTR_URL)
        assert result == PDF_BYTES
        mock_stream.assert_called_once_with(
            "GET", self.HOUSE_PTR_URL, timeout=30.0, follow_redirects=False
        )

    @pytest.mark.parametrize("url", [HOUSE_PTR_URL, HOUSE_ANNUAL_URL, SENATE_URL])
    def test_official_disclosure_urls_allowed(self, url: str):
        mock_response = self._stream_response(chunks=[b"ok"])
        with patch("httpx.stream", return_value=mock_response):
            assert download_artifact_bytes(url) == b"ok"

    @pytest.mark.parametrize(
        "url",
        [
            "http://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC123.pdf",
            "file:///tmp/DOC123.pdf",
            "https://evil.test/public_disc/ptr-pdfs/2024/DOC123.pdf",
            "https://disclosures.house.gov.evil.test/public_disc/ptr-pdfs/2024/DOC123.pdf",
            "https://user@disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC123.pdf",
            "https://disclosures.house.gov:444/public_disc/ptr-pdfs/2024/DOC123.pdf",
            "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC123.pdf?download=1",
            "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC123.pdf#top",
            "https://disclosures.house.gov/public_disc/other/2024/DOC123.pdf",
            "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/../DOC123.pdf",
            "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC%2F123.pdf",
            "https://efdsearch.senate.gov/search/view/html/XYZ/",
        ],
    )
    def test_rejects_untrusted_urls_before_network(self, url: str):
        with patch("httpx.get") as mock_get, patch("httpx.stream") as mock_stream:
            with pytest.raises(ValueError, match="unsupported disclosure artifact URL"):
                download_artifact_bytes(url)
        mock_get.assert_not_called()
        mock_stream.assert_not_called()

    def test_custom_timeout_forwarded(self):
        mock_response = self._stream_response(chunks=[b"data"])
        with patch("httpx.stream", return_value=mock_response) as mock_stream:
            download_artifact_bytes(self.SENATE_URL, timeout=60.0)
        assert mock_stream.call_args[1]["timeout"] == 60.0

    def test_rejects_non_positive_timeout_before_network(self):
        with patch("httpx.stream") as mock_stream:
            with pytest.raises(ValueError, match="timeout must be positive"):
                download_artifact_bytes(self.HOUSE_PTR_URL, timeout=0)

        mock_stream.assert_not_called()

    def test_rejects_non_positive_max_bytes_before_network(self):
        with patch("httpx.stream") as mock_stream:
            with pytest.raises(ValueError, match="max_bytes must be positive"):
                download_artifact_bytes(self.HOUSE_PTR_URL, max_bytes=0)

        mock_stream.assert_not_called()

    def test_raises_on_http_error(self):
        mock_response = self._stream_response(
            raise_error=httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
        )
        with patch("httpx.stream", return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError):
                download_artifact_bytes(self.HOUSE_PTR_URL)

    def test_rejects_redirect_without_following_location(self):
        mock_response = self._stream_response(
            chunks=[],
            headers={"location": "https://evil.test/leak"},
            status_code=302,
        )
        with patch("httpx.stream", return_value=mock_response) as mock_stream:
            with pytest.raises(ValueError, match="redirect"):
                download_artifact_bytes(self.HOUSE_PTR_URL)
        mock_stream.assert_called_once()

    def test_rejects_off_origin_response_url(self):
        mock_response = self._stream_response(
            chunks=[b"data"],
            url="https://evil.test/public_disc/ptr-pdfs/2024/DOC123.pdf",
        )
        with patch("httpx.stream", return_value=mock_response):
            with pytest.raises(ValueError, match="off-origin disclosure artifact response URL"):
                download_artifact_bytes(self.HOUSE_PTR_URL)

    def test_rejects_oversized_content_length_before_streaming(self):
        mock_response = self._stream_response(
            chunks=[b"not-read"],
            headers={"content-length": "11"},
        )
        with patch("httpx.stream", return_value=mock_response):
            with pytest.raises(ValueError, match="exceeds maximum"):
                download_artifact_bytes(self.HOUSE_PTR_URL, max_bytes=10)
        mock_response.iter_bytes.assert_not_called()

    def test_rejects_chunked_body_that_exceeds_max_bytes(self):
        mock_response = self._stream_response(chunks=[b"12345", b"678901"])
        with patch("httpx.stream", return_value=mock_response):
            with pytest.raises(ValueError, match="exceeds maximum"):
                download_artifact_bytes(self.HOUSE_PTR_URL, max_bytes=10)


# ---------------------------------------------------------------------------
# sha256_bytes
# ---------------------------------------------------------------------------


class TestSha256Bytes:
    def test_known_digest(self):
        data = b"hello"
        expected = hashlib.sha256(b"hello").hexdigest()
        assert sha256_bytes(data) == expected

    def test_returns_64_char_hex(self):
        digest = sha256_bytes(b"psephosamerica")
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
        with (
            patch("src.parse.disclosures.download._fetch_data_source_id", return_value=5),
            patch(
                "src.parse.disclosures.download.create_source_artifact",
                return_value=ARTIFACT_ROW,
            ),
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

        with (
            patch("src.parse.disclosures.download._fetch_data_source_id", return_value=5),
            patch(
                "src.parse.disclosures.download.create_source_artifact",
                side_effect=_capture,
            ),
        ):
            store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)
        assert captured["sha256"] == expected_digest

    def test_storage_uri_is_storage_key(self):
        conn = _make_conn()
        captured = {}

        def _capture(conn, *, storage_uri, **kw):
            captured["storage_uri"] = storage_uri
            return ARTIFACT_ROW

        with (
            patch("src.parse.disclosures.download._fetch_data_source_id", return_value=5),
            patch(
                "src.parse.disclosures.download.create_source_artifact",
                side_effect=_capture,
            ),
        ):
            store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)
        assert captured["storage_uri"] == HOUSE_META.storage_key

    def test_mime_type_pdf(self):
        conn = _make_conn()
        captured = {}

        def _capture(conn, *, mime_type, **kw):
            captured["mime_type"] = mime_type
            return ARTIFACT_ROW

        with (
            patch("src.parse.disclosures.download._fetch_data_source_id", return_value=5),
            patch(
                "src.parse.disclosures.download.create_source_artifact",
                side_effect=_capture,
            ),
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

        with (
            patch("src.parse.disclosures.download._fetch_data_source_id", return_value=6),
            patch(
                "src.parse.disclosures.download.create_source_artifact",
                side_effect=_capture,
            ),
        ):
            store_downloaded_artifact(conn, html_meta, b"<html></html>")
        assert captured["mime_type"] == "text/html"

    def test_source_record_id_forwarded(self):
        conn = _make_conn()
        captured = {}

        def _capture(conn, *, source_record_id, **kw):
            captured["source_record_id"] = source_record_id
            return ARTIFACT_ROW

        with (
            patch("src.parse.disclosures.download._fetch_data_source_id", return_value=5),
            patch(
                "src.parse.disclosures.download.create_source_artifact",
                side_effect=_capture,
            ),
        ):
            store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)
        assert captured["source_record_id"] == "DOC123"

    def test_fetched_at_forwarded(self):
        conn = _make_conn()
        captured = {}

        def _capture(conn, *, fetched_at, **kw):
            captured["fetched_at"] = fetched_at
            return ARTIFACT_ROW

        with (
            patch("src.parse.disclosures.download._fetch_data_source_id", return_value=5),
            patch(
                "src.parse.disclosures.download.create_source_artifact",
                side_effect=_capture,
            ),
        ):
            store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)
        assert captured["fetched_at"] == FIXED_NOW

    def test_ingestion_run_id_forwarded(self):
        conn = _make_conn()
        captured = {}

        def _capture(conn, *, ingestion_run_id, **kw):
            captured["ingestion_run_id"] = ingestion_run_id
            return ARTIFACT_ROW

        with (
            patch("src.parse.disclosures.download._fetch_data_source_id", return_value=5),
            patch(
                "src.parse.disclosures.download.create_source_artifact",
                side_effect=_capture,
            ),
        ):
            store_downloaded_artifact(
                conn,
                HOUSE_META,
                PDF_BYTES,
                ingestion_run_id=123,
            )
        assert captured["ingestion_run_id"] == 123

    def test_commit_false_forwarded(self):
        conn = _make_conn()
        captured = {}

        def _capture(conn, *, commit, **kw):
            captured["commit"] = commit
            return ARTIFACT_ROW

        with (
            patch("src.parse.disclosures.download._fetch_data_source_id", return_value=5),
            patch(
                "src.parse.disclosures.download.create_source_artifact",
                side_effect=_capture,
            ),
        ):
            store_downloaded_artifact(
                conn,
                HOUSE_META,
                PDF_BYTES,
                commit=False,
            )

        assert captured["commit"] is False

    def test_raises_when_data_source_missing(self):
        conn = _make_conn()
        with patch("src.parse.disclosures.download.fetch_all", return_value=[]):
            with pytest.raises(ValueError, match="data_source slug not found"):
                store_downloaded_artifact(conn, HOUSE_META, PDF_BYTES)


# ---------------------------------------------------------------------------
# Additional branch coverage: content-length, chunking, response-url, db id
# ---------------------------------------------------------------------------

_PTR_URL = "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC123.pdf"
_ANNUAL_URL = "https://disclosures.house.gov/public_disc/financial-pdfs/2024/DOC123.pdf"


def _resp(*, chunks: list[bytes], headers: dict[str, str] | None = None, url: str | None = None):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.headers = headers or {}
    response.status_code = 200
    response.url = url
    response.iter_bytes.return_value = chunks
    return response


def test_download_ignores_non_integer_content_length() -> None:
    resp = _resp(chunks=[b"ok"], headers={"content-length": "not-a-number"})
    with patch("httpx.stream", return_value=resp):
        assert download_artifact_bytes(_PTR_URL) == b"ok"


def test_download_skips_empty_chunks() -> None:
    resp = _resp(chunks=[b"", b"da", b"", b"ta"])
    with patch("httpx.stream", return_value=resp):
        assert download_artifact_bytes(_PTR_URL) == b"data"


def test_download_allows_empty_response_url() -> None:
    resp = _resp(chunks=[b"ok"], url="")
    with patch("httpx.stream", return_value=resp):
        assert download_artifact_bytes(_PTR_URL) == b"ok"


def test_download_rejects_off_origin_final_url() -> None:
    # A different (but still official) final URL must be rejected as off-origin.
    resp = _resp(chunks=[b"ok"], url=_ANNUAL_URL)
    with patch("httpx.stream", return_value=resp):
        with pytest.raises(ValueError, match="off-origin disclosure artifact response URL"):
            download_artifact_bytes(_PTR_URL)


def test_fetch_data_source_id_returns_integer() -> None:
    from src.parse.disclosures.download import _fetch_data_source_id

    with patch("src.parse.disclosures.download.fetch_all", return_value=[{"id": 5}]):
        assert _fetch_data_source_id(MagicMock(), "house-disclosures") == 5


def test_fetch_data_source_id_rejects_boolean_and_non_integer_ids() -> None:
    from src.parse.disclosures.download import _fetch_data_source_id

    with patch("src.parse.disclosures.download.fetch_all", return_value=[{"id": True}]):
        with pytest.raises(ValueError, match="must be an integer"):
            _fetch_data_source_id(MagicMock(), "house-disclosures")
    with patch("src.parse.disclosures.download.fetch_all", return_value=[{"id": "x"}]):
        with pytest.raises(ValueError, match="must be an integer"):
            _fetch_data_source_id(MagicMock(), "house-disclosures")


def test_download_accepts_matching_response_url() -> None:
    # final_url == requested_url is the safe, non-redirected case.
    resp = _resp(chunks=[b"ok"], url=_PTR_URL)
    with patch("httpx.stream", return_value=resp):
        assert download_artifact_bytes(_PTR_URL) == b"ok"
