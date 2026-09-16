import json
import plistlib
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from psephos import refresh, refresh_service, sources
from psephos.acquire import AcquisitionError
from psephos.retrieve import Reader
from psephos.store import Provision, writer_lock


@pytest.fixture
def scheduled(store, monkeypatch):
    store.collection(
        "uscode",
        ("us", "United States", "federal", None),
        name="Fixture",
        authority="Fixture publisher",
        kind="code",
        homepage="https://publisher.test/",
        source_status="Test data",
        access="Offline fixture",
    )
    monkeypatch.setattr(refresh, "getproxies_environment", lambda: {})
    monkeypatch.setattr(
        refresh.shutil, "disk_usage", lambda root: SimpleNamespace(free=16 * 1024**3)
    )
    refresh.configure(store.root, ["uscode"], max_mib=2, monthly_mib=4)
    calls = []
    response = {"code": 200, "body": "Publisher text", "etag": "one"}
    real_acquirer = refresh.RefreshAcquirer

    def transport(request):
        calls.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if response["code"] == 200 and request.headers.get("if-none-match") == response["etag"]:
            return httpx.Response(304)
        return httpx.Response(
            response["code"], text=response["body"], headers={"ETag": response["etag"]}
        )

    class OfflineAcquirer(real_acquirer):
        def __init__(self, store, **kwargs):
            super().__init__(store, **kwargs)
            self.delay = 0
            self.client.close()
            self.client = httpx.Client(transport=httpx.MockTransport(transport))

    def sync(s, a, limit, as_of):
        url = "https://publisher.test/source"
        s.inventory("uscode", "title-1", url, "pending")
        raw = a.fetch(url)
        text = s.artifact(raw.sha256).decode()
        s.ingest(
            collection="uscode",
            document="fixture",
            title="Fixture",
            url=url,
            acquisition=raw.id,
            snapshot_date="2026-01-01",
            snapshot_basis="Fixture date",
            parser="test-1",
            provisions=[Provision("key", "Fixture 1", "Heading", text, "", url)],
        )
        s.inventory("uscode", "title-1", url, "indexed")
        return {"inventory_items": {"uscode": ["title-1"]}}

    monkeypatch.setattr(refresh, "RefreshAcquirer", OfflineAcquirer)
    monkeypatch.setattr(sources, "_sources", lambda: {"uscode": SimpleNamespace(sync=sync)})
    return store, calls, response, sync


def test_refresh_revalidates_without_changing_legal_dates_and_retains_old_versions(scheduled):
    store, calls, response, _ = scheduled
    first = refresh.run(store.root)
    assert first["sources"]["uscode"]["status"] == "checked"  # robots 404 is permitted
    original = Reader(store).read("key")
    original_count = len(calls)
    refresh.run(store.root)
    assert len(calls) == original_count  # not due, no HTTP
    refresh.retry(store.root, "uscode")
    result = refresh.run(store.root)
    assert result["sources"]["uscode"]["added_versions"] == 0
    assert calls[-1].headers["if-none-match"] == "one"
    assert Reader(store).read("key")["id"] == original["id"]
    response.update(body="Corrected publisher text", etag="two")
    refresh.retry(store.root, "uscode")
    assert refresh.run(store.root)["sources"]["uscode"]["added_versions"] == 1
    current = Reader(store).read("key")
    assert current["text"] == response["body"]
    assert current["snapshot_date"] == original["snapshot_date"] == "2026-01-01"
    assert Reader(store).read(original["id"])["text"] == "Publisher text"
    card = Reader(store).coverage(collection="uscode")["collections"][0]
    assert card["refresh"]["status"] == "checked"


def test_partial_refresh_preserves_last_success_and_backs_off(scheduled, monkeypatch):
    store, calls, _, sync = scheduled
    last = refresh.run(store.root)["sources"]["uscode"]["last_success"]

    def partial(s, a, limit, as_of):
        result = sync(s, a, limit, as_of)
        s.inventory(
            "uscode", "title-2", "https://publisher.test/second", "failed", "Malformed title"
        )
        result["inventory_items"]["uscode"].append("title-2")
        return result

    monkeypatch.setattr(sources, "_sources", lambda: {"uscode": SimpleNamespace(sync=partial)})
    refresh.retry(store.root, "uscode")
    entry = refresh.run(store.root)["sources"]["uscode"]
    assert entry["status"] == "failed_retryable" and entry["last_success"] == last
    count = len(calls)
    assert refresh.run(store.root)["sources"]["uscode"] == entry
    assert len(calls) == count


def test_denial_latches_across_source_members(scheduled, monkeypatch):
    store, calls, response, _ = scheduled
    response["code"] = 403

    def sweep(s, a, limit, as_of):
        for item in ("one", "two"):
            try:
                a.fetch("https://publisher.test/" + item)
            except AcquisitionError:
                pass
        return {"inventory_items": {"uscode": ["one", "two"]}}

    monkeypatch.setattr(sources, "_sources", lambda: {"uscode": SimpleNamespace(sync=sweep)})
    assert refresh.run(store.root)["sources"]["uscode"]["status"] == "blocked"
    assert [r.url.path for r in calls] == ["/robots.txt", "/one"]


def test_unexpected_adapter_error_is_persisted_not_left_running(scheduled, monkeypatch):
    store, _, _, _ = scheduled

    def broken(*args):
        raise KeyError("publisher schema changed")

    monkeypatch.setattr(sources, "_sources", lambda: {"uscode": SimpleNamespace(sync=broken)})
    entry = refresh.run(store.root)["sources"]["uscode"]
    assert entry["status"] == "blocked" and "schema changed" in entry["error"]


def test_access_denial_is_not_retried_even_after_operator_retry(scheduled):
    store, calls, response, _ = scheduled
    response["code"] = 403
    assert refresh.run(store.root)["sources"]["uscode"]["status"] == "blocked"
    count = len(calls)
    refresh.run(store.root)
    refresh.retry(store.root, "uscode")
    assert refresh.run(store.root)["sources"]["uscode"]["status"] == "blocked"
    assert len(calls) == count


@pytest.mark.parametrize("code", [403, 0])
def test_resume_cache_cannot_hide_a_later_publisher_or_proxy_denial(scheduled, code):
    store, calls, _, _ = scheduled
    a = refresh.RefreshAcquirer(store, max_bytes=refresh.MIB)
    url = "https://publisher.test/source"
    try:
        a.fetch(url)
        a._record(url, url, "2026-09-16T00:00:00Z", code, {}, None, "403 Forbidden")
        a.blocked = False  # A restarted worker must inspect the retained denial too.
        count = len(calls)
        with pytest.raises(AcquisitionError, match="denial"):
            a.fetch(url, max_age_seconds=86400)
        assert len(calls) == count
    finally:
        a.close()


def test_budget_reservation_survives_restart_reconfigure_and_retry(scheduled):
    store, calls, _, _ = scheduled
    state = refresh._load(store.root)
    state["used_bytes"] = state["monthly_bytes"]
    state["sources"]["uscode"]["status"] = "running"
    refresh._save(store.root, state)
    refresh.configure(store.root, ["uscode"], max_mib=2, monthly_mib=4)
    refresh.retry(store.root, "uscode")
    result = refresh.run(store.root)
    assert result["sources"]["uscode"]["status"] == "budget_exhausted"
    assert result["used_bytes"] == 4 * refresh.MIB and not calls


def test_download_budget_accounts_for_rejected_chunk(scheduled):
    store, _, response, _ = scheduled
    response["body"] = "x" * 3 * refresh.MIB
    result = refresh.run(store.root)
    assert result["sources"]["uscode"]["status"] == "failed_retryable"
    assert 0 < result["used_bytes"] <= result["max_run_bytes"]
    assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 0


def test_lock_proxy_change_and_low_disk_prevent_network(scheduled, monkeypatch):
    store, calls, _, _ = scheduled
    with writer_lock(store.root), pytest.raises(RuntimeError, match="writer"):
        refresh.run(store.root)
    monkeypatch.setattr(refresh, "getproxies_environment", lambda: {"https": "http://proxy.test"})
    assert "network environment" in refresh.run(store.root)["worker_error"]
    assert refresh.collection_status(store.root, "uscode")["worker_error"]
    monkeypatch.setattr(refresh, "getproxies_environment", lambda: {})
    monkeypatch.setattr(refresh.shutil, "disk_usage", lambda root: SimpleNamespace(free=0))
    assert refresh.run(store.root)["sources"]["uscode"]["status"] == "disk_space_low"
    assert not calls


def test_corrupt_or_unsupported_schedule_is_not_silently_replaced(scheduled):
    store, calls, _, _ = scheduled
    with pytest.raises(ValueError, match="supports"):
        refresh.configure(store.root, ["portland-city-code"])
    state = refresh._load(store.root)
    state["period"] = "broken"
    refresh._save(store.root, state)
    with pytest.raises(ValueError, match="period"):
        refresh.run(store.root)
    assert refresh.collection_status(store.root, "uscode")["status"] == "unreadable_refresh_state"
    assert not calls


def test_current_inventory_excludes_history_but_detects_missing_titles(store):
    store.collection(
        "ecfr",
        ("us", "United States", "federal", None),
        name="Fixture",
        authority="Fixture",
        kind="regulation",
        homepage="https://publisher.test/",
        source_status="Test",
        access="Test",
    )
    store.inventory("ecfr", "1@2025-01-01", "https://publisher.test/old", "failed", "Old attempt")
    store.inventory("ecfr", "1@2026-01-01", "https://publisher.test/new", "indexed")
    current = {"ecfr": ["1@2026-01-01"]}
    assert refresh._inventory(store, "ecfr", {"inventory_items": current}) == current
    assert refresh._identities("ecfr", current["ecfr"]) == {"1"}
    current["ecfr"].append("2@2026-01-01")
    with pytest.raises(AcquisitionError, match="incomplete"):
        refresh._inventory(store, "ecfr", {"inventory_items": current})


def test_launch_agent_uses_argument_array_and_no_daemon(scheduled):
    store, _, _, _ = scheduled
    spec = plistlib.loads(plistlib.dumps(refresh_service.specification(store.root)))
    assert spec["ProgramArguments"][1:] == [
        "-m",
        "psephos.refresh_service",
        str(store.root),
    ]
    assert spec["StartInterval"] == 3600 and spec["RunAtLoad"]
    assert "KeepAlive" not in spec and "EnvironmentVariables" not in spec
    assert json.loads(refresh._path(store.root).read_text())["enabled"] is True
    assert refresh._path(store.root).stat().st_mode & 0o777 == 0o600
    assert refresh.pause(store.root)["enabled"] is False
    assert refresh.run(store.root)["status"] == "paused"
    with pytest.raises(ValueError, match="paused"):
        refresh_service.specification(store.root)


def test_supervisor_timeout_retains_spend_and_marks_interruption(scheduled, monkeypatch):
    store, _, _, _ = scheduled
    state = refresh._load(store.root)
    state["used_bytes"] = 2 * refresh.MIB
    state["sources"]["uscode"]["status"] = "running"
    refresh._save(store.root, state)

    def timeout(command, *, timeout):
        assert timeout == 1800
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(refresh_service.subprocess, "run", timeout)
    assert refresh_service.supervise(store.root) == 1
    result = refresh.status(store.root)
    assert result["used_bytes"] == 2 * refresh.MIB
    assert result["sources"]["uscode"]["status"] == "interrupted"
    assert "30 minutes" in result["worker_error"]
