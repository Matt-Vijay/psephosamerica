"""Bounded fetch helpers for official Congress-related XML endpoints."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import urlparse

import httpx

_DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_ALLOWED_HOSTS = frozenset({"api.congress.gov", "clerk.house.gov", "www.senate.gov"})
_HTTPX_CLIENT_TYPE = httpx.Client


def _unsupported_url(url: str) -> ValueError:
    return ValueError(f"unsupported official Congress URL: {url!r}")


def _host(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise _unsupported_url(url)
    if parsed.username is not None or parsed.password is not None:
        raise _unsupported_url(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise _unsupported_url(url) from exc
    if port not in (None, 443):
        raise _unsupported_url(url)
    return parsed.hostname.lower()


def _validate_official_url(url: str) -> None:
    if _host(url) not in _ALLOWED_HOSTS:
        raise _unsupported_url(url)


def _response_url(response: Any) -> str | None:
    raw = getattr(response, "url", None)
    if isinstance(raw, str):
        return raw
    if isinstance(raw, httpx.URL):
        return str(raw)
    return None


def _content_length(response: Any) -> int | None:
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    raw = headers.get("content-length")
    if not isinstance(raw, str):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _iter_response_bytes(response: Any) -> Iterator[bytes]:
    if isinstance(response, httpx.Response):
        yield from response.iter_bytes()
        return
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        yield content
        return
    text = getattr(response, "text", None)
    if isinstance(text, str):
        yield text.encode()
        return
    iter_bytes = getattr(response, "iter_bytes", None)
    if callable(iter_bytes):
        for chunk in iter_bytes():
            if not isinstance(chunk, bytes):
                raise ValueError("official Congress response yielded non-bytes chunk")
            yield chunk


def fetch_official_congress_text(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> str:
    """Fetch a bounded text response from an official Congress-related URL."""
    _validate_official_url(url)
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if client is None:
        with httpx.Client(timeout=timeout) as managed_client:
            return _fetch_with_client(url, managed_client, max_bytes=max_bytes)
    return _fetch_with_client(url, client, max_bytes=max_bytes)


def _fetch_with_client(url: str, client: httpx.Client, *, max_bytes: int) -> str:
    if isinstance(client, _HTTPX_CLIENT_TYPE):
        with client.stream("GET", url, follow_redirects=False) as response:
            return _read_response(url, response, max_bytes=max_bytes)
    response = client.get(url, follow_redirects=False)
    return _read_response(url, response, max_bytes=max_bytes)


def _read_response(url: str, response: Any, *, max_bytes: int) -> str:
    status_code = getattr(response, "status_code", 200)
    if isinstance(status_code, int) and 300 <= status_code < 400:
        raise ValueError(f"redirect response rejected for official Congress URL: {url!r}")
    resolved_url = _response_url(response)
    if resolved_url is not None and _host(resolved_url) != _host(url):
        raise ValueError(f"off-origin response URL for official Congress URL: {resolved_url!r}")

    length = _content_length(response)
    if length is not None and length > max_bytes:
        raise ValueError(
            f"official Congress response exceeds maximum size of {max_bytes} bytes: {length}"
        )

    response.raise_for_status()

    chunks = _iter_response_bytes(response)
    total = 0
    bounded: list[bytes] = []
    for chunk in chunks:
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                f"official Congress response exceeds maximum size of {max_bytes} bytes"
            )
        bounded.append(chunk)
    return b"".join(bounded).decode("utf-8")
