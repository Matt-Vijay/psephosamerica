import json
from io import BytesIO

import httpx
import pytest
from conftest import retain
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from psephos import idaho
from psephos.acquire import Acquirer, AcquisitionError
from psephos.retrieve import Reader
from psephos.store import Provision, Reference


def row(number, *, title=None, notice=False):
    kind = "TITLE" if title is None else "CHAPTER"
    url = (
        idaho.INDEX + f"Title{number}"
        if title is None
        else idaho.BASE
        + f"/wp-content/uploads/statutesrules/idstat/Title{title}/T{title}CH{number}.pdf"
    )
    link = "" if notice else f'<a href="{url}">Export</a>'
    return f"<tr><td>{kind} {number}</td><td></td><td>{'[REPEALED]' if notice else 'Name'}</td><td>{link}</td></tr>"


def table(*rows):
    return ("<table>" + "".join(rows) + "</table>").encode()


def test_inventory_has_exact_native_membership_and_explicit_nonexports():
    data = table(row("1", title="2"), row("3", title="2", notice=True))
    entries = idaho.inventory(data, "2")
    assert entries[0][2].endswith("T2CH1.pdf") and entries[1][2] is None
    with pytest.raises(ValueError, match="Missing exact native export"):
        idaho.inventory(data.replace(b"T2CH1.pdf", b"T3CH1.pdf"), "2")
    with pytest.raises(ValueError, match="Missing exact native export"):
        idaho.inventory(data.replace(b"[REPEALED]", b"Unavailable"), "2")
    with pytest.raises(ValueError, match="duplicate"):
        idaho.inventory(table(row("1"), row("1")))
    missing_pdf = b'<table><tr><td><a href="/statutesrules/idstat/Title15/T15CH15">Chapter 15</a></td><td></td><td>Native chapter without PDF</td></tr></table>'
    assert idaho.inventory(missing_pdf, "15") == [("15", "Native chapter without PDF", None)]
    subchapter = table(row("26A", title="41")).replace(b"T41CH26A.pdf", b"T41CH26SCH26A.pdf")
    assert idaho.inventory(subchapter, "41")[0][2].endswith("T41CH26SCH26A.pdf")
    numbered = table(row("22", title="48")).replace(b"CHAPTER 22", b"CHAPTER 21 [22]")
    assert idaho.inventory(numbered, "48")[0][:2] == ("22", "CHAPTER 21 [22] Name")
    repealed = table(row("32", title="49", notice=True)).replace(b"CHAPTER 32", b"CHAPTER [33] 32")
    assert idaho.inventory(repealed, "49") == [
        ("source-label:[33]32", "CHAPTER [33] 32 [REPEALED]", None)
    ]


def test_original_budget_reserve_and_host_are_preserved(store, tmp_path):
    ledger = store.root / "collectors/idaho/bytes.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"cap_bytes": 200 * 1024**2, "decoded_bytes": 6422473}))
    a = idaho.IdahoAcquirer(store, tmp_path)
    try:
        assert a.downloaded == 6422473 and a.max_bytes == 199 * 1024**2
        assert a.budget_path == ledger and not (tmp_path / "budget.json").exists()
        with pytest.raises(AcquisitionError, match="reviewed publisher host"):
            a.fetch("https://example.test/file.pdf")
    finally:
        a.close()


def test_html_error_is_not_accepted_as_pdf(store):
    with pytest.raises(ValueError, match="Expected publisher PDF"):
        idaho.chapter_units(store, retain(store, b"<html>Error</html>"), "1", "1", idaho.BASE)


def test_short_history_note_requires_the_entire_native_note_not_arbitrary_short_text():
    note = "29\n[18-8011, added 1998, ch. 152, sec. 4, p. 527.]"
    assert idaho.short_history_note(note, 29)
    assert idaho.short_history_note(
        "3\n[7-805, added 1998, ch. 411, sec. 3, p. 1290; am. 2000, ch. 469, sec.\n17, p. 1467.]", 3
    )
    for invalid in (
        "DRAFT",
        "29",
        "",
        "PLEADINGS -- [REPEALED]",
        note[:-1],
        note + " omitted body",
        "FIGURE " + note,
    ):
        assert not idaho.short_history_note(invalid, 29)


def test_pdf_reference_navigates_only_to_retained_chapter_not_an_unverified_section(store):
    store.collection(
        "id-statutes",
        ("us-id", "Idaho", "state", "us"),
        name="Idaho",
        authority="Fixture",
        kind="statutes",
        homepage=idaho.INDEX,
        source_status="Fixture",
        access="Fixture",
    )
    pdf = idaho.BASE + "/wp-content/uploads/statutesrules/idstat/Title1/T1CH2.pdf"
    target = idaho.INDEX + "Title1/T1CH2/SECT1-201"
    other_targets = [
        target + "?edition=2025",
        target + "#subsection",
        target.replace("Title1", "Title2"),
        target.replace(idaho.BASE, "https://example.test"),
    ]
    source = retain(store, b"Source link", observed="2026-01-01T00:00:00Z")
    store.ingest(
        collection="test",
        document="links",
        title="Links",
        url=source.url,
        acquisition=source.id,
        parser="fixture",
        snapshot_basis="Unknown",
        provisions=[
            Provision(
                "links",
                "Links",
                "Links",
                "Links",
                "",
                source.url,
                references=[
                    Reference(t, "publisher_pdf_link", t, "Source annotation")
                    for t in [target, *other_targets]
                ],
            )
        ],
    )
    receipt = retain(store, b"Target chapter", observed="2026-02-01T00:00:00Z")
    with store.db:
        store.db.execute(
            "UPDATE acquisitions SET url=?,final_url=? WHERE id=?", (pdf, pdf, receipt.id)
        )
    key = "id-statutes:title-1/chapter-2/page-1"
    store.ingest(
        collection="id-statutes",
        document="id-statutes:T1CH2",
        title="Title 1 Chapter 2",
        url=pdf,
        acquisition=receipt.id,
        parser="fixture",
        snapshot_basis="Unknown",
        provisions=[
            Provision(
                key,
                "PDF page 1",
                "TITLE 1",
                "1-201. Fixture section text.",
                "",
                pdf + "#page=1",
                unit_kind="pdf_page",
            )
        ],
    )
    reader = Reader(store)
    identifier = reader.read("links")["id"]
    refs = {r["target"]: r for r in reader.references(identifier)["references"]}
    assert refs[target]["resolution_status"] == "chapter_retained_section_location_unverified"
    assert refs[target]["acquired_targets"] == []
    assert refs[target]["navigation"] == {
        "collection": "id-statutes",
        "document": "id-statutes:T1CH2",
        "search_query": "1-201",
        "first_key": key,
        "scope": "enclosing_chapter_only",
    }
    assert all("navigation" not in refs[t] for t in other_targets)
    before = reader.references(identifier, observation_cutoff="2026-01-15T00:00:00Z")["references"]
    assert (
        next(r for r in before if r["target"] == target)["resolution_status"]
        == "not_acquired_at_cutoffs"
    )
    assert not reader.references(identifier, as_of="2026-09-17")["references"]


def test_pdf_link_evidence_is_repeatable_and_omits_runtime_object_ids(store, monkeypatch):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Annots")] = ArrayObject(
        [
            DictionaryObject(
                {
                    NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/Rect"): ArrayObject([NumberObject(x) for x in (1, 2, 30, 40)]),
                    NameObject("/A"): DictionaryObject(
                        {
                            NameObject("/S"): NameObject("/URI"),
                            NameObject("/URI"): TextStringObject(idaho.INDEX),
                        }
                    ),
                }
            )
        ]
    )
    buffer = BytesIO()
    writer.write(buffer)
    receipt = retain(store, buffer.getvalue())
    unit = Provision(
        "p",
        "fixture",
        "TITLE 1",
        "TITLE 1 CHAPTER 2 source text",
        "",
        idaho.INDEX,
        metadata={"pdf_page": 1, "text_quality": "layout_text_unverified"},
    )
    monkeypatch.setattr(idaho, "pdf_units", lambda *args: iter([unit]))
    a = idaho.chapter_units(store, receipt, "1", "2", idaho.INDEX)
    b = idaho.chapter_units(store, receipt, "1", "2", idaho.INDEX)
    assert a == b
    assert json.loads(a[0].references[0].evidence) == {
        "pdf_page": 1,
        "annotation_subtype": "/Link",
        "rectangle": [1, 2, 30, 40],
        "uri": idaho.INDEX,
    }


def test_sync_resume_skips_completed_bodies_and_retains_nonexport_notice(store, monkeypatch):
    title_url = idaho.INDEX + "Title2/"
    data = {
        idaho.BASE + "/robots.txt": b"User-agent: *\nAllow: /\n",
        idaho.INDEX: table(row("2")),
        idaho.TERMS: b"LEXIS published official copies",
        idaho.CURRENCY: b"current through the 2026 Legislative Session",
        title_url: table(
            row("1", title="2"), row("2", title="2"), row("3", title="2", notice=True)
        ),
    }
    for number in ("1", "2"):
        data[idaho.inventory(table(row(number, title="2")), "2")[0][2]] = (
            b"Fixture " + number.encode()
        )
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=data[str(request.url)])

    monkeypatch.setattr(
        idaho,
        "chapter_units",
        lambda s, r, t, c, u: [
            Provision(
                key=f"id-statutes:title-{t}/chapter-{c}/page-1",
                citation="Fixture",
                heading="Fixture",
                text="Fixture source text",
                markup="",
                url=u + "#page=1",
                unit_kind="pdf_page",
            )
        ],
    )
    a = Acquirer(store, delay=0)
    a.client.close()
    a.client = httpx.Client(transport=httpx.MockTransport(respond))
    a._pause = lambda *args, **kwargs: None
    try:
        assert idaho.sync_idaho(store, a, 1, None)["accepted_this_run"] == 1
        assert (
            store.db.execute("SELECT status FROM inventories WHERE item='title:2'").fetchone()[0]
            == "partial"
        )
        assert idaho.sync_idaho(store, a, 1, None)["accepted_this_run"] == 1
        assert (
            store.db.execute("SELECT status FROM inventories WHERE item='title:2'").fetchone()[0]
            == "indexed"
        )
        assert (
            store.db.execute("SELECT status FROM inventories WHERE item='T2CH3'").fetchone()[0]
            == "nonexport_notice"
        )
        assert idaho.sync_idaho(store, a, None, None)["attempted"] == 0
        assert len(requests) == len(data)
        assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 2
    finally:
        a.close()
