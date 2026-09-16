"""Fresh native discovery, bounded resumes and portable legacy entry points."""

import json
from types import SimpleNamespace

import httpx
import pytest
from conftest import retain

from psephos import collect_georgia as ga
from psephos import collect_virginia as va
from psephos.acquire import AcquisitionError
from psephos.store import Provision


def mock_campaign(monkeypatch, module, cls_name, responses):
    cls = getattr(module, cls_name)
    monkeypatch.setattr(cls, "_pause", lambda *args: None)
    calls = []

    def handler(request):
        url = str(request.url)
        calls.append(url)
        if request.url.path == "/robots.txt":
            raw = b"User-agent: *\nAllow: /"
        else:
            assert url in responses, url
            raw = responses[url]
        return httpx.Response(200, stream=httpx.ByteStream(raw))

    def factory(*args, **kwargs):
        a = cls(*args, **kwargs)
        a.client.close()
        a.client = httpx.Client(
            transport=httpx.MockTransport(handler), event_hooks={"response": [a._bounded_response]}
        )
        return a

    monkeypatch.setattr(module, cls_name, factory)
    return calls


def caller(*, refresh=False, max_bytes=2**20):
    return SimpleNamespace(refresh=refresh, downloaded=0, max_bytes=max_bytes)


def counts(store):
    return {
        table: store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("acquisitions", "versions", "provisions")
    }


def test_georgia_fresh_inventory_and_limited_resume_without_campaign_seed(
    store, monkeypatch, capsys
):
    index = b"".join(
        f'<a href="?st=GASOS&amp;dept=Departments&amp;pdf=Department {n} Source">Department {n}. Source</a>'.encode()
        for n in (1, 2)
    )
    items = ga.inventory(index)
    calls = mock_campaign(
        monkeypatch,
        ga,
        "GeorgiaAcquirer",
        {ga.INDEX: index, **{i["url"]: b"%PDF-fixture" for i in items}},
    )

    def project(path, item, cache, reviews):
        assert reviews == ga.REVIEWED_MEDIA
        return (
            [
                Provision(
                    "ga-rules:department-" + item["department"] + ":page-1",
                    "Page",
                    "Source",
                    "Law",
                    "",
                    item["url"],
                )
            ],
            "2026-09-01",
            {"machine_ocr_pages": [], "media_only_pages": []},
        )

    monkeypatch.setattr(ga, "pdf_projection", project)
    a = caller()
    ga.sync_georgia(store, a, 1, None)
    assert counts(store)["versions"] == 1
    assert a.downloaded == sum(len(v) for v in (b"User-agent: *\nAllow: /", index, b"%PDF-fixture"))
    ga.sync_georgia(store, a, 1, None)
    assert counts(store)["versions"] == 2
    assert len(calls) == 4
    saved, spent = counts(store), a.downloaded
    ga.sync_georgia(store, a, None, None)
    assert counts(store) == saved and a.downloaded == spent and len(calls) == 4
    budget = json.loads((store.root / "georgia-completion/budget.json").read_bytes())
    assert budget["consumed_bytes"] == a.downloaded
    assert not (store.root / "georgia-completion/plan.json").exists()
    assert capsys.readouterr().out == ""
    with pytest.raises(ValueError, match="historical"):
        ga.sync_georgia(store, a, None, "2020-01-01")


def virginia_responses():
    base = va.BASE
    body = b'<article id="admincode"><h2>Chapter 1. Source</h2><p class="vacno">1VAC1-1-10. General.</p><p>Source law.</p></article>'
    return {
        base + "/developers": b"Publisher developer documentation",
        base
        + "/xmlapi/": b'<a href="/api/AdministrativeCodeGetTitleListOfXml">Titles</a><a href="/api/AdministrativeCodePrefaceXml">Prefaces</a>',
        "https://codecommission.dls.virginia.gov/faq_va_admin_code.shtml": b"Publisher notices",
        base + "/admincode/": b'<a href="/admincodeexpand/">All titles</a>',
        base + "/api/AdministrativeCodeGetTitleListOfXml/": b'[{"TitleNumber":"1"}]',
        base + "/admincodeexpand/": b'<a href="/admincodeexpand/title1/">Title 1</a>',
        base
        + "/admincodeexpand/title1/": b'<dl><dt><a href="/admincode/title1/agency1/">Agency</a></dt><dd>Source agency</dd><dt><a href="/admincode/title1/agency1/chapter1/">Chapter</a></dt><dd>Source chapter</dd></dl>',
        base
        + "/admincode/title1/agency1/chapter1/": b'<a href="/admincodefull/title1/agency1/chapter1">Read Chapter</a><dl><dt><a href="/admincode/title1/agency1/chapter1/section10/">Section 10</a></dt><dd>General</dd></dl>',
        base + "/admincodefull/title1/agency1/chapter1": body,
        base
        + "/api/AdministrativeCodePrefaceXml/1/1/": b'{"TitleNumber":"1","AgencyNumber":"1","PrefaceSummary":"<p>Agency summary.</p>"}',
    }


def test_virginia_empty_store_discovers_bodies_and_resumes_every_stage(store, monkeypatch, capsys):
    responses = virginia_responses()
    calls = mock_campaign(monkeypatch, va, "VirginiaAcquirer", responses)
    a = caller()
    va.sync_virginia(store, a, None, None)
    assert counts(store)["versions"] == 2
    assert (
        store.db.execute("SELECT text FROM provisions WHERE key='va-vac:1VAC1-1-10'")
        .fetchone()[0]
        .endswith("Source law.")
    )
    assert a.downloaded == sum(map(len, responses.values())) + 2 * len(b"User-agent: *\nAllow: /")
    saved, spent, request_count = counts(store), a.downloaded, len(calls)
    va.sync_virginia(store, a, None, None)
    assert counts(store) == saved and a.downloaded == spent and len(calls) == request_count
    assert capsys.readouterr().out == ""
    with pytest.raises(ValueError, match="historical"):
        va.sync_virginia(store, a, None, "2020-01-01")


def test_virginia_refresh_failure_resumes_new_retained_bytes_without_losing_old_ids(
    store, monkeypatch
):
    responses = virginia_responses()
    calls = mock_campaign(monkeypatch, va, "VirginiaAcquirer", responses)
    a = caller()
    va.sync_virginia(store, a, 1, None)
    original_ids = {row[0] for row in store.db.execute("SELECT id FROM provisions")}
    body_url = va.BASE + "/admincodefull/title1/agency1/chapter1"
    responses[body_url] = responses[body_url].replace(b"Source law.", b"Amended source law.")
    parse = va.chapter_units

    def fail(*args):
        raise ValueError("Fixture interruption after acquisition")

    monkeypatch.setattr(va, "chapter_units", fail)
    a.refresh = True
    with pytest.raises(ValueError, match="unresolved"):
        va.sync_virginia(store, a, 1, None)
    assert counts(store)["versions"] == 1
    assert list((store.root / "virginia-completion").glob("inventory-*.json"))
    request_count, spent = len(calls), a.downloaded
    monkeypatch.setattr(va, "chapter_units", parse)
    a.refresh = False
    va.sync_virginia(store, a, 1, None)
    assert counts(store)["versions"] == 2
    assert original_ids <= {row[0] for row in store.db.execute("SELECT id FROM provisions")}
    assert len(calls) == request_count and a.downloaded == spent


@pytest.mark.parametrize("module,source_url", [(ga, ga.INDEX), (va, va.BASE + "/admincode/")])
def test_portable_source_data_without_operator_budget_cannot_reset_allowance(
    store, module, source_url
):
    receipt = retain(store, b"Imported source evidence")
    with store.db:
        store.db.execute("UPDATE acquisitions SET url=? WHERE id=?", (source_url, receipt.id))
    sync = ga.sync_georgia if module is ga else va.sync_virginia
    a = caller()
    before = counts(store)
    with pytest.raises(AcquisitionError, match="Restore the original"):
        sync(store, a, 1, None)
    assert counts(store) == before and a.downloaded == 0


@pytest.mark.parametrize(
    "module,cls_name,sync",
    [(ga, "GeorgiaAcquirer", ga.sync_georgia), (va, "VirginiaAcquirer", va.sync_virginia)],
)
def test_collectors_obey_callers_remaining_cap_and_release_lock(
    store, monkeypatch, module, cls_name, sync
):
    mock_campaign(monkeypatch, module, cls_name, {})
    a = caller(max_bytes=10)
    with pytest.raises(AcquisitionError, match="cap"):
        sync(store, a, 1, None)
    assert a.downloaded == 0
    # A failure must not retain the writer lock or reset the allowance.
    with pytest.raises(AcquisitionError, match="cap"):
        sync(store, a, 1, None)


def test_legacy_script_exports_are_package_functions(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    import complete_georgia
    import complete_virginia

    assert complete_georgia.run is ga.run and complete_georgia.old_state is ga.old_state
    assert complete_virginia.run is va.run and complete_virginia.discover is va.discover
    assert complete_virginia.media is va.media and complete_virginia.prefaces is va.prefaces
