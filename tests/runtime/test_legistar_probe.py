from __future__ import annotations

import httpx

from src.graph.ingest.legistar_registry import LEGISTAR_CLIENTS
from src.runtime.legistar_probe import (
    CANDIDATES,
    ProbeResult,
    probe_candidates,
    probe_legistar_client,
    registry_lines,
)


def _noop(_seconds: float) -> None:
    return None


def _client(good_codes: set[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        code = request.url.path.split("/")[2]
        if code in good_codes:
            return httpx.Response(200, json=[{"BodyId": 1}])
        return httpx.Response(404, text="<html>not found</html>")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_probe_legistar_client_accepts_json_list() -> None:
    assert probe_legistar_client("houston", client=_client({"houston"})) is True
    assert probe_legistar_client("nowhere", client=_client(set())) is False


def test_probe_legistar_client_rejects_non_list_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "soft 404"})

    assert (
        probe_legistar_client("x", client=httpx.Client(transport=httpx.MockTransport(handler)))
        is False
    )


def test_probe_legistar_client_tolerates_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    assert (
        probe_legistar_client("x", client=httpx.Client(transport=httpx.MockTransport(handler)))
        is False
    )


def test_probe_candidates_skips_registered_and_paces() -> None:
    delays: list[float] = []
    registered_code = LEGISTAR_CLIENTS[0].client
    candidates = {
        registered_code: ("city", "il", "Chicago"),  # already registered -> skipped
        "houston": ("city", "tx", "Houston"),
        "nowhere": ("city", "zz", "Nowhere"),
    }
    result = probe_candidates(
        candidates, client=_client({"houston"}), sleep=delays.append, delay_seconds=0.2
    )
    assert result.verified == ("houston",)
    assert result.probed == 2 and result.already_registered == 1
    assert delays == [0.2]  # paced between the two probes


def test_registry_lines_dedup_same_place() -> None:
    result = ProbeResult(verified=("miami", "miamifl"), probed=2, already_registered=0)
    lines = registry_lines(result)
    assert len(lines) == 1  # two spellings of the same government -> one row
    assert 'LegistarClient("miami", "city", "fl", "Miami")' in lines[0]


def test_candidates_catalog_is_well_formed() -> None:
    for code, (level, state, place) in CANDIDATES.items():
        assert code == code.lower() and code
        assert level in ("city", "county")
        assert len(state) == 2 and place


def test_probe_candidates_default_client(monkeypatch) -> None:
    import src.runtime.legistar_probe as mod

    real = httpx.Client
    router = _client({"houston"})
    monkeypatch.setattr(mod.httpx, "Client", lambda **_kw: router)
    result = probe_candidates({"houston": ("city", "tx", "Houston")}, sleep=_noop)
    assert result.verified == ("houston",)
    monkeypatch.setattr(mod.httpx, "Client", real)
