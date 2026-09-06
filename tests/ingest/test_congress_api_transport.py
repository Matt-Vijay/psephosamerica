"""Tests that the CongressAPIClient transport layer sends the API key
via headers only — never in request URLs or raised error strings.

No network calls; httpx.Client is constructed with a captured mock
so we can inspect the actual URL and headers sent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.ingest.congress.congress_api import (
    CongressAPIClient,
    bill_detail_url,
    bills_url,
    committees_url,
    cosponsors_url,
    member_detail_url,
    members_url,
)

# ---------------------------------------------------------------------------
# URL builders are key-free
# ---------------------------------------------------------------------------


class TestUrlBuildersAreKeyFree:
    """All URL builders must produce key-free strings regardless of input."""

    _SENTINEL = "SECRET_API_KEY"

    def _assert_no_key(self, url: str) -> None:
        assert self._SENTINEL not in url
        assert "api_key" not in url

    def test_members_url(self):
        self._assert_no_key(members_url())

    def test_members_url_with_congress(self):
        self._assert_no_key(members_url(119))

    def test_member_detail_url(self):
        self._assert_no_key(member_detail_url("A000001"))

    def test_committees_url(self):
        self._assert_no_key(committees_url(119))

    def test_committees_url_with_chamber(self):
        self._assert_no_key(committees_url(119, "senate"))

    def test_bills_url(self):
        self._assert_no_key(bills_url(119))

    def test_bills_url_with_type(self):
        self._assert_no_key(bills_url(119, "hr"))

    def test_bill_detail_url(self):
        self._assert_no_key(bill_detail_url(119, "hr", 42))

    def test_cosponsors_url(self):
        self._assert_no_key(cosponsors_url(119, "hr", 42))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(body: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = body
    return resp


def _make_transport_client(
    api_key: str, response_body: dict
) -> tuple[CongressAPIClient, MagicMock]:
    """Returns (client, mock_http_get) where mock_http_get records all calls."""
    mock_http_get = MagicMock(return_value=_make_mock_response(response_body))
    with patch("httpx.Client") as mock_client_cls:
        mock_client_instance = MagicMock()
        mock_client_instance.get = mock_http_get
        mock_client_cls.return_value = mock_client_instance
        client = CongressAPIClient(api_key)
    return client, mock_http_get


# ---------------------------------------------------------------------------
# Key goes into headers, not URL
# ---------------------------------------------------------------------------


class TestApiKeyNotInRequestUrl:
    """The URL passed to the underlying HTTP GET must never contain the key."""

    _API_KEY = "SUPER_SECRET_KEY_XYZ"

    def _get_called_url(self, mock_http_get: MagicMock) -> str:
        return mock_http_get.call_args[0][0]

    def test_get_member_detail_url_has_no_key(self):
        body = {
            "member": {
                "bioguideId": "A000001",
                "firstName": "Ada",
                "lastName": "Lovelace",
                "directOrderName": "Ada Lovelace",
                "terms": {"item": [{"chamber": "House of Representatives"}]},
                "currentMember": True,
            }
        }
        client, mock_get = _make_transport_client(self._API_KEY, body)
        client.get_member_detail("A000001")
        url = self._get_called_url(mock_get)
        assert self._API_KEY not in url
        assert "api_key" not in url

    def test_get_bill_detail_url_has_no_key(self):
        body = {
            "bill": {
                "congress": 119,
                "type": "HR",
                "number": 42,
                "title": "Test",
                "introducedDate": "2025-01-01",
            }
        }
        client, mock_get = _make_transport_client(self._API_KEY, body)
        client.get_bill_detail_payload(119, "hr", 42)
        url = self._get_called_url(mock_get)
        assert self._API_KEY not in url
        assert "api_key" not in url

    def test_iter_members_url_has_no_key(self):
        client, mock_get = _make_transport_client(self._API_KEY, {"members": [], "pagination": {}})
        list(client.iter_members())
        url = self._get_called_url(mock_get)
        assert self._API_KEY not in url
        assert "api_key" not in url
        assert mock_get.call_args.kwargs["follow_redirects"] is False

    def test_iter_committees_url_has_no_key(self):
        client, mock_get = _make_transport_client(
            self._API_KEY, {"committees": [], "pagination": {}}
        )
        list(client.iter_committees(119))
        url = self._get_called_url(mock_get)
        assert self._API_KEY not in url
        assert "api_key" not in url

    def test_iter_bills_url_has_no_key(self):
        client, mock_get = _make_transport_client(self._API_KEY, {"bills": [], "pagination": {}})
        list(client.iter_bills(119))
        url = self._get_called_url(mock_get)
        assert self._API_KEY not in url
        assert "api_key" not in url

    def test_off_origin_pagination_next_is_rejected_before_second_request(self):
        body = {
            "members": [
                {
                    "bioguideId": "A000001",
                    "firstName": "Ada",
                    "lastName": "Lovelace",
                    "terms": {"item": [{"chamber": "House of Representatives"}]},
                }
            ],
            "pagination": {"next": "https://evil.example/leak"},
        }
        mock_get = MagicMock(
            side_effect=[
                _make_mock_response(body),
                AssertionError("off-origin pagination URL was fetched"),
            ]
        )
        with patch("httpx.Client") as mock_client_cls:
            mock_client_instance = MagicMock()
            mock_client_instance.get = mock_get
            mock_client_cls.return_value = mock_client_instance
            client = CongressAPIClient(self._API_KEY)

        with pytest.raises(ValueError, match="pagination.next"):
            list(client.iter_members())

        assert mock_get.call_count == 1

    def test_repeated_pagination_next_is_rejected_before_refetch(self):
        url = "https://api.congress.gov/v3/member?limit=250&offset=0&format=json"
        body = {
            "members": [
                {
                    "bioguideId": "A000001",
                    "firstName": "Ada",
                    "lastName": "Lovelace",
                    "terms": {"item": [{"chamber": "House of Representatives"}]},
                }
            ],
            "pagination": {"next": url},
        }
        mock_get = MagicMock(
            side_effect=[
                _make_mock_response(body),
                AssertionError("repeated pagination URL was fetched"),
            ]
        )
        with patch("httpx.Client") as mock_client_cls:
            mock_client_instance = MagicMock()
            mock_client_instance.get = mock_get
            mock_client_cls.return_value = mock_client_instance
            client = CongressAPIClient(self._API_KEY)

        with pytest.raises(ValueError, match="pagination.next repeated URL"):
            list(client.iter_members())

        assert mock_get.call_count == 1

    def test_pagination_page_cap_is_enforced(self):
        first_url = "https://api.congress.gov/v3/member?limit=250&offset=0&format=json"
        second_url = "https://api.congress.gov/v3/member?limit=250&offset=250&format=json"
        first_body = {
            "members": [
                {
                    "bioguideId": "A000001",
                    "firstName": "Ada",
                    "lastName": "Lovelace",
                    "terms": {"item": [{"chamber": "House of Representatives"}]},
                }
            ],
            "pagination": {"next": second_url},
        }
        mock_get = MagicMock(
            side_effect=[
                _make_mock_response(first_body),
                AssertionError("page cap was not enforced"),
            ]
        )
        with (
            patch("httpx.Client") as mock_client_cls,
            patch("src.ingest.congress.congress_api.MAX_PAGINATION_PAGES", 1),
        ):
            mock_client_instance = MagicMock()
            mock_client_instance.get = mock_get
            mock_client_cls.return_value = mock_client_instance
            client = CongressAPIClient(self._API_KEY)

            with pytest.raises(ValueError, match="pagination exceeded maximum page count"):
                list(client.iter_members())

        assert mock_get.call_count == 1
        assert mock_get.call_args_list[0].args[0] == first_url

    def test_rejects_http_url_before_request(self):
        client, mock_get = _make_transport_client(self._API_KEY, {"members": []})

        with pytest.raises(ValueError, match="Congress API URL"):
            client._get("http://api.congress.gov/v3/member?format=json")

        mock_get.assert_not_called()

    def test_rejects_off_origin_url_before_request(self):
        client, mock_get = _make_transport_client(self._API_KEY, {"members": []})

        with pytest.raises(ValueError, match="Congress API URL"):
            client._get("https://evil.example/v3/member?format=json")

        mock_get.assert_not_called()

    def test_rejects_non_official_base_url_override_before_client_construction(self):
        with patch("httpx.Client") as mock_client_cls:
            with pytest.raises(ValueError, match="Congress API base URL"):
                CongressAPIClient(
                    self._API_KEY,
                    base_url="https://evil.example/v3/",
                )

        mock_client_cls.assert_not_called()

    def test_rejects_redirect_response(self):
        import httpx

        url = "https://api.congress.gov/v3/member?format=json"
        with patch("httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.get.return_value = httpx.Response(
                302,
                headers={"location": "https://evil.example/v3/member"},
                request=httpx.Request("GET", url),
            )
            mock_client_cls.return_value = mock_http
            client = CongressAPIClient(self._API_KEY)

        with pytest.raises(ValueError, match="redirect response rejected"):
            client._get(url)

    def test_rejects_oversized_content_length_before_json_parse(self):
        import httpx

        url = "https://api.congress.gov/v3/member?format=json"
        response = httpx.Response(
            200,
            content=b"{}",
            headers={"content-length": str(25 * 1024 * 1024 + 1)},
            request=httpx.Request("GET", url),
        )
        response.json = MagicMock(side_effect=AssertionError("json parsed before size check"))  # type: ignore[method-assign]
        with patch("httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.get.return_value = response
            mock_client_cls.return_value = mock_http
            client = CongressAPIClient(self._API_KEY)

        with pytest.raises(ValueError, match="exceeds maximum size"):
            client._get(url)

    def test_rejects_chunked_response_before_reading_past_max_bytes(self):
        import httpx

        url = "https://api.congress.gov/v3/member?format=json"

        class RaisingStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"12345"
                yield b"678901"
                raise AssertionError("read past limit")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=RaisingStream(), request=request)

        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as real_http,
            patch("src.ingest.congress.congress_api.httpx.Client", return_value=real_http),
            patch("src.ingest.congress.congress_api.MAX_API_RESPONSE_BYTES", 10),
        ):
            client = CongressAPIClient(self._API_KEY)

            with pytest.raises(ValueError, match="exceeds maximum size"):
                client._get(url)


class TestApiKeyInHeaders:
    """The httpx.Client must be initialized with the key in headers."""

    _API_KEY = "HEADER_KEY_TEST"

    def test_rejects_empty_api_key_before_client_construction(self):
        with patch("httpx.Client") as mock_client_cls:
            with pytest.raises(ValueError, match="api_key is required"):
                CongressAPIClient("  ")

        mock_client_cls.assert_not_called()

    def test_rejects_non_positive_timeout_before_client_construction(self):
        with patch("httpx.Client") as mock_client_cls:
            with pytest.raises(ValueError, match="timeout must be positive"):
                CongressAPIClient(self._API_KEY, timeout=0)

        mock_client_cls.assert_not_called()

    def test_client_constructed_with_x_api_key_header(self):
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value = MagicMock()
            CongressAPIClient(self._API_KEY)
        _, kwargs = mock_client_cls.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("X-Api-Key") == self._API_KEY

    def test_key_not_in_client_constructor_positional_args(self):
        """The key must not appear as a positional arg to the httpx.Client constructor."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value = MagicMock()
            CongressAPIClient(self._API_KEY)
        args, _ = mock_client_cls.call_args
        for arg in args:
            assert self._API_KEY not in str(arg)


# ---------------------------------------------------------------------------
# Key does not leak into raised error strings from HTTP errors
# ---------------------------------------------------------------------------


class TestApiKeyNotInHttpErrors:
    """When raise_for_status raises, the key must not appear in the exception."""

    _API_KEY = "LEAKED_KEY_GUARD"

    def test_http_error_does_not_expose_key(self):
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_client_cls.return_value = mock_http
            mock_resp = MagicMock()
            # Simulate a 403 response whose .url attribute does not contain the key.
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "403 Forbidden",
                request=MagicMock(),
                response=MagicMock(),
            )
            mock_http.get.return_value = mock_resp
            client = CongressAPIClient(self._API_KEY)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client._get("https://api.congress.gov/v3/member?format=json")

        assert self._API_KEY not in str(exc_info.value)
