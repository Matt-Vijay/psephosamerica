from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from src.ingest.congress.official_fetch import fetch_official_congress_text


class _Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        url: str = "https://clerk.house.gov/evs/2024/index.xml",
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self._chunks = chunks if chunks is not None else [b"<xml/>"]

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):
        return iter(self._chunks)


def test_rejects_unsupported_url_before_network() -> None:
    client = MagicMock()

    with pytest.raises(ValueError, match="unsupported official Congress URL"):
        fetch_official_congress_text("https://evil.example/evs/2024/index.xml", client=client)

    client.get.assert_not_called()


def test_rejects_non_positive_max_bytes_before_network() -> None:
    client = MagicMock()

    with pytest.raises(ValueError, match="max_bytes must be positive"):
        fetch_official_congress_text(
            "https://clerk.house.gov/evs/2024/index.xml",
            client=client,
            max_bytes=0,
        )

    client.get.assert_not_called()


def test_rejects_non_positive_timeout_before_network() -> None:
    client = MagicMock()

    with pytest.raises(ValueError, match="timeout must be positive"):
        fetch_official_congress_text(
            "https://clerk.house.gov/evs/2024/index.xml",
            client=client,
            timeout=0,
        )

    client.get.assert_not_called()


def test_forces_redirects_off_on_supplied_client() -> None:
    client = MagicMock()
    client.get.return_value = _Response()

    fetch_official_congress_text("https://clerk.house.gov/evs/2024/index.xml", client=client)

    client.get.assert_called_once_with(
        "https://clerk.house.gov/evs/2024/index.xml",
        follow_redirects=False,
    )


def test_rejects_redirect_response() -> None:
    client = MagicMock()
    client.get.return_value = _Response(
        status_code=302, headers={"location": "https://evil.example/"}
    )

    with pytest.raises(ValueError, match="redirect response rejected"):
        fetch_official_congress_text("https://clerk.house.gov/evs/2024/index.xml", client=client)


def test_rejects_off_origin_response_url() -> None:
    client = MagicMock()
    client.get.return_value = _Response(url="https://evil.example/evs/2024/index.xml")

    with pytest.raises(ValueError, match="off-origin response URL"):
        fetch_official_congress_text("https://clerk.house.gov/evs/2024/index.xml", client=client)


def test_rejects_oversized_content_length() -> None:
    client = MagicMock()
    client.get.return_value = _Response(headers=httpx.Headers({"content-length": "11"}))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="exceeds maximum size"):
        fetch_official_congress_text(
            "https://clerk.house.gov/evs/2024/index.xml", client=client, max_bytes=10
        )


def test_rejects_oversized_chunked_body() -> None:
    client = MagicMock()
    client.get.return_value = _Response(chunks=[b"12345", b"67890", b"1"])

    with pytest.raises(ValueError, match="exceeds maximum size"):
        fetch_official_congress_text(
            "https://clerk.house.gov/evs/2024/index.xml", client=client, max_bytes=10
        )


def test_rejects_oversized_chunked_body_without_consuming_extra_chunk() -> None:
    class RaisingResponse(_Response):
        def iter_bytes(self):
            yield b"12345"
            yield b"678901"
            raise AssertionError("read past limit")

    client = MagicMock()
    client.get.return_value = RaisingResponse()

    with pytest.raises(ValueError, match="exceeds maximum size"):
        fetch_official_congress_text(
            "https://clerk.house.gov/evs/2024/index.xml",
            client=client,
            max_bytes=10,
        )


def test_decodes_bounded_body() -> None:
    client = MagicMock()
    client.get.return_value = _Response(chunks=[b"<vote/>"])

    assert (
        fetch_official_congress_text("https://clerk.house.gov/evs/2024/index.xml", client=client)
        == "<vote/>"
    )


def test_real_httpx_client_stops_streaming_when_body_exceeds_limit() -> None:
    class GuardedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"12345"
            yield b"678901"
            raise AssertionError("read past byte limit")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=GuardedStream(), request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(ValueError, match="exceeds maximum size"):
        fetch_official_congress_text(
            "https://clerk.house.gov/evs/2024/index.xml",
            client=client,
            max_bytes=10,
        )
