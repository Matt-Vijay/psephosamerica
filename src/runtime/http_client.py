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

import httpx

USER_AGENT = "openpact-research/0.1 (public-record ingest)"
DEFAULT_TIMEOUT_SECONDS = 60.0


def client_or_default(
    client: httpx.Client | None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> tuple[httpx.Client, bool]:
    """The injected client unowned, or a fresh identified default the caller owns."""
    if client is not None:
        return client, False
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}), True
