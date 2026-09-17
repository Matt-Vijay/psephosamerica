import json

import httpx
import pytest
from conftest import retain

from psephos.acquire import Acquirer, AcquisitionError
from psephos.minnesota import BASE, INDEX, MinnesotaAcquirer, chapter_units, sync_minnesota
from psephos.retrieve import Reader
from psephos.store import Provision, Reference


def page(body):
    return f"<main><h1>2025 Minnesota Statutes</h1>{body}</main>".encode()


def chapter(number):
    return page(
        f'<div id="xtend"><div><h2 class="chapter_title">CHAPTER {number}. TITLE</h2>'
        f'<div id="chapter_analysis"><a href="#stat.{number}.01">{number}.01</a></div>Scope note.'
        f'<div class="section" id="stat.{number}.01"><h1>{number}.01 HEADING.</h1>'
        "<p>Exact text.</p><table><tr><td>Class</td><td>10</td></tr></table></div>"
        "<p>History: 2025 act.</p></div></div>"
    )


def test_section_membership_history_and_table_fidelity():
    units = chapter_units(chapter("1"), "1", BASE + "/statutes/cite/1/full")
    assert [u.unit_kind for u in units] == ["scope_notes", "section"]
    assert "Scope note." in units[0].text
    assert units[1].key == "mn:statutes/1/stat.1.01"
    assert "History: 2025 act." in units[1].text and "Class\t10" in units[1].text
    assert units[1].metadata["tables"] == 1
    with pytest.raises(ValueError, match="Section inventory mismatch"):
        chapter_units(chapter("1").replace(b'href="#stat.1.01"', b'href="#stat.1.02"'), "1", BASE)
    with pytest.raises(ValueError, match="wrong-chapter"):
        chapter_units(chapter("1").replace(b'id="stat.1.01"', b'id="stat.2.01"'), "1", BASE)


def test_original_decoded_budget_and_abandoned_reservation_are_preserved(store, tmp_path):
    ledger = store.root / "collectors/mn/budget.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps({"cap_bytes": 200 * 1024**2, "decoded_bytes": 1234, "reserved_bytes": 65536})
    )
    a = MinnesotaAcquirer(store, tmp_path)
    try:
        assert a.downloaded == 66770 and a.budget["uncertain_reserved_bytes"] == 65536
        assert a.budget_path == ledger
        with pytest.raises(AcquisitionError, match="publisher host"):
            a.fetch("https://example.test/unreviewed")
        a.downloaded += 10
    finally:
        a.close()
    data = json.loads(ledger.read_text())
    assert data["decoded_bytes"] == 66780 and "consumed_bytes" not in data
    assert not (tmp_path / "budget.json").exists()


def test_section_links_require_native_identity_and_do_not_redirect_dated_links(store):
    store.collection(
        "mn-statutes",
        ("us-mn", "Minnesota", "state", "us"),
        name="Fixture",
        authority="Fixture",
        kind="statutes",
        homepage=INDEX,
        source_status="Fixture",
        access="Fixture",
    )
    receipt = retain(store, b"Minnesota fixture")
    current = BASE + "/statutes/cite/1.01"
    targets = [
        current,
        BASE + "/statutes/2024/cite/1.01",
        current + "/subd/1",
        current + "?x=1",
        "https://example.test/statutes/cite/1.01",
    ]
    links = Provision(
        "links",
        "links",
        "links",
        "Exact links",
        "",
        INDEX,
        references=[Reference(url, "publisher_link", url, "Fixture link") for url in targets],
    )
    store.ingest(
        collection="test",
        document="links",
        title="Links",
        url=INDEX,
        acquisition=receipt.id,
        parser="fixture",
        snapshot_basis="Unknown fixture snapshot",
        provisions=[links],
    )
    store.ingest(
        collection="mn-statutes",
        document="mn:statutes/1",
        title="Chapter 1",
        url=BASE + "/statutes/cite/1/full",
        acquisition=receipt.id,
        parser="fixture",
        snapshot_basis="Unknown fixture snapshot",
        provisions=chapter_units(chapter("1"), "1", BASE + "/statutes/cite/1/full"),
    )
    reader = Reader(store)
    identifier = reader.read("links")["id"]
    references = {r["target"]: r for r in reader.references(identifier)["references"]}
    assert references[current]["acquired_targets"][0]["key"] == "mn:statutes/1/stat.1.01"
    assert references[current]["resolution_status"] == "resolved"
    assert all(not references[url]["acquired_targets"] for url in targets[1:])
    assert not reader.references(identifier, as_of="2026-01-01")["references"]


def test_resume_skips_existing_bodies_and_does_not_download_pdf_companions(store):
    part = BASE + "/statutes/part/FIXTURE"
    urls = {number: BASE + f"/statutes/cite/{number}/full" for number in ("1", "2")}
    bodies = {
        BASE + "/robots.txt": b"User-agent: *\nAllow: /\n",
        INDEX: page(
            f'<table id="toc_table"><tbody><tr><td><a href="{part}">1-2</a></td><td>FIXTURE</td></tr></tbody></table>'
        ),
        BASE + "/statutes/info": page("Official Versions of Minnesota Statutes"),
        part: page(
            '<table id="chapters_table"><tr><td><a href="/statutes/cite/1">1</a><a href="/statutes/cite/2">2</a></td></tr></table>'
        ),
        **{
            url: chapter(n).replace(
                b"</main>", f'<a href="/statutes/cite/{n}/pdf">PDF</a></main>'.encode()
            )
            for n, url in urls.items()
        },
    }
    requests = []

    def respond(request):
        url = str(request.url)
        requests.append(url)
        return httpx.Response(200, content=bodies[url])

    a = Acquirer(store, delay=0)
    a.client.close()
    a.client = httpx.Client(transport=httpx.MockTransport(respond))
    a._pause = lambda *args, **kwargs: None
    try:
        assert sync_minnesota(store, a, 1, None)["accepted_this_run"] == 1
        first = [tuple(r) for r in store.db.execute("SELECT * FROM versions")]
        assert (
            store.db.execute("SELECT status FROM inventories WHERE item='part:FIXTURE'").fetchone()[
                0
            ]
            == "partial"
        )
        assert sync_minnesota(store, a, 1, None)["accepted_this_run"] == 1
        assert all(
            row in [tuple(r) for r in store.db.execute("SELECT * FROM versions")] for row in first
        )
        assert (
            store.db.execute("SELECT status FROM inventories WHERE item='part:FIXTURE'").fetchone()[
                0
            ]
            == "indexed"
        )
        assert len(requests) == len(bodies)
        assert sync_minnesota(store, a, None, None)["attempted"] == 0
        assert len(requests) == len(bodies)
    finally:
        a.close()
