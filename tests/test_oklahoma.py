import json
from types import SimpleNamespace

import httpx
import pytest

from psephos import oklahoma as ok
from psephos.acquire import Acquirer, AcquisitionError
from psephos.store import Provision


def test_original_charges_and_uncertain_reservation_are_not_reset(store, tmp_path):
    ledger = store.root / "collectors/oklahoma/http_ledger.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "cap": ok.CAP,
                "charged_bytes": 23152311,
                "decoded_bytes": 6375095,
                "requests": [{"complete": False, "reserved": 16777216}],
                "hosts": {},
            }
        )
    )
    a = ok.OklahomaAcquirer(store, tmp_path)
    try:
        assert a.downloaded == 23152311 and a.budget["decoded_bytes"] == 6375095
        assert a.max_bytes == ok.CAP and a.budget_path == ledger
        a.downloaded += 30
        saved = json.loads(ledger.read_text())
        assert saved["charged_bytes"] - saved["decoded_bytes"] == 16777216
        assert saved["requests"] == [{"complete": False, "reserved": 16777216}]
        with pytest.raises(AcquisitionError, match="reviewed Senate host"):
            a.fetch("https://example.test/law.pdf")
        a._record(ok.INDEX, ok.INDEX, "2026-09-01T00:00:00Z", 403, {}, None, "Denied")
        with pytest.raises(AcquisitionError, match="Retained access denial"):
            a.fetch(ok.INDEX)
    finally:
        a.close()


def test_robots_literal_query_wildcards_and_specific_rules():
    rules = [
        "User-agent: *",
        "Disallow: /search?",
        "Disallow: /search/",
        "Disallow: /core/",
        "Allow: /core/*.css$",
    ]
    assert ok.policy_allows(rules, ok.INDEX + "?page=2")
    assert not ok.policy_allows(rules, ok.BASE + "/search?q=statutes")
    assert not ok.policy_allows(rules, ok.BASE + "/search/statutes")
    assert not ok.policy_allows(rules, ok.BASE + "/core/private")
    assert ok.policy_allows(rules, ok.BASE + "/core/public.css")
    assert not ok.policy_allows(rules, ok.BASE + "/core/public.css/private")
    specific = rules + ["User-agent: Psephos", "Disallow: /"]
    assert not ok.policy_allows(specific, ok.INDEX)


def test_inventory_uses_native_pagination_and_does_not_merge_parallel_title_exports():
    data = b'<a href="/sites/os85.pdf">Title 85</a><a href="/sites/os85_0.pdf">Title 85. Workers Compensation</a><a href="?page=6">Last</a>'
    entries, pages = ok.inventory_page(data, ok.INDEX)
    assert len(entries) == 2 and pages == {0, 6}
    assert ok.slug(entries[0][0]) == ok.slug(entries[1][0]) == "title-85"
    with pytest.raises(ValueError, match="Unexpected Oklahoma PDF link"):
        ok.inventory_page(
            data.replace(b"/sites/os85.pdf", b"https://outside.test/os85.pdf"), ok.INDEX
        )
    with pytest.raises(ValueError, match="duplicate"):
        ok.inventory_page(data + data, ok.INDEX)


def test_pdf_identity_and_contents_only_exports_fail_closed(store, monkeypatch):
    a = Acquirer(store)
    receipt = a._record(
        ok.BASE + "/os1.pdf", ok.BASE + "/os1.pdf", "2026-01-01T00:00:00Z", 200, {}, None, None
    )
    # The parser verifies stored bytes before consulting extraction/reader output.
    from psephos.store import digest

    data = b"%PDF-fixture"
    sha = digest(data)
    path = store.object_path(sha)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    rec = ok.Receipt(receipt, ok.BASE + "/os1.pdf", sha, "2026-01-01T00:00:00Z", len(data))

    class Page(dict):
        images = []

    page = "OKLAHOMA STATUTES\nTITLE 1. ABSTRACTING\n\u00a71-1. Requirement.\nThe officer shall retain the original source.\n"
    monkeypatch.setattr(ok, "PdfReader", lambda p: SimpleNamespace(pages=[Page()]))
    monkeypatch.setattr(ok.shutil, "which", lambda x: "/mock/pdftotext")
    monkeypatch.setattr(
        ok.subprocess, "run", lambda *args, **kw: SimpleNamespace(stdout=page + "\f")
    )
    try:
        assert ok.pdf_units(store, rec, "Title 1. Abstracting", "title-1")[0].text == page
        with pytest.raises(ValueError, match="opening identity"):
            ok.pdf_units(store, rec, "Title 11. Other", "title-11")
        page = "TITLE 1. ABSTRACTING\n\u00a71-1. Officer shall retain source...................................3\n"
        with pytest.raises(ValueError, match="No demonstrated legal text"):
            ok.pdf_units(store, rec, "Title 1. Abstracting", "title-1")
        page = "ARTICLE VII-A - Court on the Judiciary\nSECTION VII-A-1. There shall be a court.\n"
        with pytest.raises(ValueError, match="opening identity"):
            ok.pdf_units(store, rec, "Article 7. Judicial Department", "article-7")
        page = "OKLAHOMA CONSTITUTION\nSECTION XXVIII-A-1\nAll beverages shall be governed by this article.\n"
        assert ok.pdf_units(store, rec, "Article 28-A. Alcoholic Beverage Laws", "article-28-A")
        with pytest.raises(ValueError, match="opening identity"):
            ok.pdf_units(store, rec, "Article 28. Alcoholic Beverage Laws", "article-28")
        page = "TITLE 74, APPENDIX I, ETHICS COMMISSION RULES\nRule 1.1. Officers shall retain these disclosures.\n"
        unit = ok.pdf_units(store, rec, "Title 74E. Ethics Rules", "title-74E")[0]
        assert "Title 74, Appendix I" in unit.citation and "74E" not in unit.citation
        assert unit.metadata["source_inventory_label"] == "Title 74E. Ethics Rules"
    finally:
        a.close()


def test_resume_counts_new_attempts_and_parallel_exports_remain_distinct(store, monkeypatch):
    pages = {
        ok.BASE + "/robots.txt": b"User-agent: *\nAllow: /",
        ok.TERMS: b"Publisher policy",
        ok.INDEX: b'<a href="/os85.pdf">Title 85</a><a href="?page=1">Next</a>',
        ok.INDEX
        + "?page=1": b'<a href="/os85_0.pdf">Title 85. Compensation</a><a href="?page=0">First</a>',
        ok.BASE + "/os85.pdf": b"first",
        ok.BASE + "/os85_0.pdf": b"second",
    }
    requests = []

    def response(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=pages[str(request.url)])

    a = Acquirer(store, delay=0)
    a.client.close()
    a.client = httpx.Client(transport=httpx.MockTransport(response))
    a._pause = lambda *args, **kwargs: None
    monkeypatch.setattr(
        ok,
        "pdf_units",
        lambda s, r, label, key: [Provision("ok:" + key, label, label, "source", "", r.url)],
    )
    try:
        assert ok.sync_oklahoma(store, a, 1, None)["accepted_this_run"] == 1
        assert ok.sync_oklahoma(store, a, 1, None)["accepted_this_run"] == 1
        assert ok.sync_oklahoma(store, a, None, None)["attempted"] == 0
        assert {r[0] for r in store.db.execute("SELECT id FROM documents")} == {
            "ok-senate:title-85/os85",
            "ok-senate:title-85/os85_0",
        }
        assert len(requests) == len(pages)
    finally:
        a.close()
