from __future__ import annotations

import httpx
import pytest

from src.runtime.http_client import USER_AGENT, client_or_default


def test_injected_client_is_returned_unowned() -> None:
    injected = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    client, owns = client_or_default(injected)
    assert client is injected and owns is False


def test_default_client_is_owned_and_identified(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    real = httpx.Client

    def fake(**kwargs: object) -> httpx.Client:
        captured.update(kwargs)
        return real(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    monkeypatch.setattr(httpx, "Client", fake)
    client, owns = client_or_default(None, timeout=120.0)
    assert owns is True
    assert captured["timeout"] == 120.0
    assert captured["headers"] == {"User-Agent": USER_AGENT}
    client.close()
