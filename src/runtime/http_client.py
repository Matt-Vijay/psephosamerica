"""Shared default HTTP client for the runtime ingestion runners.

Every runner takes an injectable ``httpx.Client`` (MockTransport in tests) and
falls back to a sensible default for live runs. That fallback used to live as
``_client`` on the govinfo bills runner, with five sibling runners importing the
private helper from a module about bills — and one more keeping its own copy.
This is the helper's real home.

The returned ``owns`` flag tells the caller whether it created the client and is
therefore responsible for closing it (injected clients stay caller-owned).
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
USER_AGENT = "psephosamerica-research/0.1 (public-record ingest)"
DEFAULT_TIMEOUT_SECONDS = 60.0


def client_or_default(
    client: httpx.Client | None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> tuple[httpx.Client, bool]:
    """The injected client unowned, or a fresh identified default the caller owns."""
    if client is not None:
        return client, False
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}), True


def get_with_backoff(
    http: httpx.Client,
    url: str,
    *,
    max_attempts: int = 6,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response | None:
    """GET ``url`` retrying rate-limit/transient failures; ``None`` if exhausted."""
    delay = base_delay
    for attempt in range(1, max_attempts + 1):
        try:
            resp = http.get(url, follow_redirects=True)
        except httpx.TransportError:
            if attempt == max_attempts:
                return None
            sleep(delay)
            delay *= 2
            continue
        if resp.status_code in _RETRYABLE_STATUS and attempt < max_attempts:
            retry_after = resp.headers.get("retry-after")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
            sleep(wait)
            delay *= 2
            continue
        return resp
    return None
