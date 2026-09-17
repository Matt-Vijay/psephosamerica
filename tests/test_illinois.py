import json

import httpx
import pytest

from psephos import illinois as il
from psephos.acquire import Acquirer, AcquisitionError

CHAPTER = il.BASE + "/Legislation/ILCS/Acts?ChapterID=2&ChapterNumber=5"


def anchor(url, text):
    return f'<a href="{url.replace("&", "&amp;")}">{text}</a>'


def listing(act):
    return il.BASE + f"/Legislation/ILCS/Articles?ActID={act}&ChapterID=2"


def export(act):
    return (
        il.BASE
        + f"/legislation/ILCS/details?ActID={act}&ChapterID=2&ChapAct=FullText&PublisherLabel=retained"
    )


def body(text):
    return ('<div id="billtextanchor">' + text + "</div>").encode()


def section(citation="5 ILCS 10/1", text="Source wording"):
    return f"<table><tr><td>({citation})<br/>{text}</td></tr></table>"


def test_inventory_identity_and_actual_export_link():
    assert il.inventory(anchor(CHAPTER, "CHAPTER 5 GENERAL PROVISIONS").encode())[0][0] == "5"
    data = anchor(listing("1"), "5 ILCS 10/ First Act.").encode()
    assert il.inventory(data, CHAPTER)[0][0] == "1"
    with pytest.raises(ValueError, match="duplicate"):
        il.inventory(data + data, CHAPTER)
    with pytest.raises(ValueError, match="different chapter"):
        il.inventory(data.replace(b"5 ILCS", b"10 ILCS"), CHAPTER)
    with pytest.raises(ValueError, match="publisher link"):
        il.inventory(data.replace(b"https://ilga.gov", b"https://example.test"), CHAPTER)
    full = anchor(export("1"), "View Entire Act").encode()
    assert il.whole_act_link(full, listing("1")) == export("1")
    with pytest.raises(ValueError, match="identity mismatch"):
        il.whole_act_link(full, listing("2"))
    with pytest.raises(ValueError, match="unique native"):
        il.whole_act_link(b"<p>No full export</p>", listing("1"))


def test_projection_preserves_alternate_text_nested_tables_and_missing_media():
    data = body(
        section(text="Effective until 2027<table><tr><td>3/4</td></tr></table>")
        + section(text='Effective in 2027<img src="/figure.png" alt="Boundary"/>')
    )
    units = il.act_units(data, export("1"), "1", "5 ILCS 10")
    assert [u.key for u in units] == ["ilcs:5 ILCS 10/1", "ilcs:5 ILCS 10/1/occurrence/2"]
    assert "3/4" in units[0].text and units[0].metadata["tables"] == 1
    assert units[1].metadata["media"]
    with pytest.raises(ValueError, match="outside unit tables"):
        il.act_units(
            body(section() + "<p>Additional operative note</p>"), export("1"), "1", "5 ILCS 10"
        )
    with pytest.raises(ValueError, match="citation differs"):
        il.act_units(data, export("1"), "1", "5 ILCS 100")
    with pytest.raises(ValueError, match="Heading-only"):
        il.act_units(body(section("5 ILCS 10 heading")), export("1"), "1", "5 ILCS 10")


def test_original_lifetime_spending_survives_cap_metadata_upgrade(store, tmp_path):
    ledger = store.root / "collectors/il/budget.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"decoded_bytes": 38099825}))
    a = il.IllinoisAcquirer(store, tmp_path)
    try:
        assert a.downloaded == 38099825 and a.max_bytes == 200 * 1024**2
        assert a.delay == 10.1 and a.budget_path == ledger
        assert json.loads(ledger.read_text())["cap_bytes"] == 200 * 1024**2
        assert not (tmp_path / "budget.json").exists()
        with pytest.raises(AcquisitionError, match="reviewed publisher host"):
            a.fetch("https://example.test/law")
        a._record(
            il.BASE + "/robots.txt",
            il.BASE + "/robots.txt",
            "2026-01-01T00:00:00Z",
            403,
            {"psephos_request_method": "HEAD"},
            None,
            "Diagnostic HEAD denied",
        )
        with pytest.raises(AcquisitionError, match="Retained access denial"):
            a.fetch(il.BASE + "/robots.txt")
    finally:
        a.close()


def test_limited_resume_uses_native_links_and_keeps_notices_distinct(store):
    responses = {
        il.BASE + "/robots.txt": b"User-agent: *\nAllow: /",
        il.INDEX: anchor(CHAPTER, "CHAPTER 5 GENERAL PROVISIONS").encode(),
        il.TERMS: b"Publisher terms",
        CHAPTER: (
            anchor(listing("1"), "5 ILCS 10/ First Act.")
            + anchor(listing("2"), "5 ILCS 20/ Second Act.")
            + anchor(listing("3"), "5 ILCS 30/ Old Act. (Repealed by P.A. 1)")
        ).encode(),
    }
    for act, number in (("1", "10"), ("2", "20")):
        responses[listing(act)] = anchor(export(act), "View Entire Act").encode()
        responses[export(act)] = body(section(f"5 ILCS {number}/1"))
    requested = []

    def respond(request):
        url = str(request.url)
        requested.append(url)
        return httpx.Response(200, content=responses[url])

    a = Acquirer(store, delay=0)
    a.client.close()
    a.client = httpx.Client(transport=httpx.MockTransport(respond))
    a._pause = lambda *args, **kwargs: None
    try:
        assert il.sync_illinois(store, a, 1, None)["accepted_this_run"] == 1
        assert (
            store.db.execute("SELECT status FROM inventories WHERE item='chapter:5'").fetchone()[0]
            == "partial"
        )
        assert il.sync_illinois(store, a, 1, None)["accepted_this_run"] == 1
        assert il.sync_illinois(store, a, None, None)["attempted"] == 0
        assert (
            store.db.execute("SELECT status FROM inventories WHERE item='chapter:5'").fetchone()[0]
            == "indexed"
        )
        assert (
            store.db.execute("SELECT status FROM inventories WHERE item='act:3'").fetchone()[0]
            == "metadata_only"
        )
        assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 2
        assert requested.count(export("1")) == 1 and len(requested) == len(responses)
    finally:
        a.close()
