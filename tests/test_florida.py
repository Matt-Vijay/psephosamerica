"""Small generated Florida fixtures; no publisher requests or accepted-source replay."""

import json
from copy import deepcopy

import httpx
import pytest
from conftest import retain
from lxml import html

from psephos import florida
from psephos.acquire import Acquirer
from psephos.parse import markup, readable
from psephos.store import stable_id

EDITION = "<h2>2026 Florida Statutes</h2>"
URL = florida.BASE + "/Laws/Statutes/2026/Chapter83/All"


def page(content):
    return ("<html><body>" + content + "</body></html>").encode()


def section(number="83.01", wording="Complete statutory body."):
    return (
        f'<div class="Section"><span class="SectionNumber">{number}</span>'
        '<span class="Catchline"><span class="CatchlineText">Fixture rule.</span></span>'
        f'<span class="SectionBody">{wording}</span></div>'
    )


def chapter(sections=None, numbers=("83.01",), chapter_number="83"):
    if sections is None:
        sections = section(chapter_number + ".01")
    index = "".join(
        f'<div class="IndexItem"><span class="SectionNumber">{number}</span>'
        '<span class="Catchline">Fixture rule.</span></div>'
        for number in numbers
    )
    return page(
        EDITION + '<div class="Chapter"><div class="ChapterTitle">'
        f'<div class="ChapterNumber">CHAPTER {chapter_number}</div>'
        '<div class="ChapterName">FIXTURE CHAPTER</div></div>'
        f'<div class="CatchlineIndex">{index}</div>{sections}</div>'
    )


def test_explicit_edition_heading_not_navigation_or_another_year():
    assert florida.edition_page(chapter()) is not None
    for content in (
        "<nav>2026 Florida Statutes</nav><h2>2025 Florida Statutes</h2>",
        '<nav><a href="/Laws/Statutes/2026">2026 Florida Statutes</a></nav>',
        EDITION + EDITION,
        "<h2>2026 Florida Statutes - archive</h2>",
    ):
        with pytest.raises(ValueError, match="explicit 2026"):
            florida.edition_page(page(content))


def test_native_title_and_chapter_membership_is_not_a_numeric_range():
    title = '<li><a href="/Laws/Statutes/2026/Title6/#Title6"><span id="Title6">Title VI (Ch. 45-88)</span></a></li>'

    def titles(rows):
        return page(EDITION + '<div class="statutesTOC"><ol>' + rows + "</ol></div>")

    assert florida.title_inventory(titles(title)) == [
        {
            "number": 6,
            "url": florida.BASE + "/Laws/Statutes/2026/Title6/",
            "label": "Title VI (Ch. 45-88)",
        }
    ]
    listed = (
        '<li><a><span id="Title6">Title VI</span></a><ol class="chapter">'
        '<li><a href="/Laws/Statutes/2026/Chapter83">Chapter 83</a>'
        '<ol><li><a href="/Laws/Statutes/2026/Chapter83/Part_I">Part I</a></li></ol></li>'
        '<li><a href="/Laws/Statutes/2026/Chapter88">Chapter 88</a></li></ol></li>'
    )
    entries = florida.chapter_inventory(page(EDITION + listed), 6)
    assert [entry["number"] for entry in entries] == ["83", "88"]
    assert entries[0]["url"] == URL
    for value in (
        "",
        title + title,
        title.replace("#Title6", "#Title7"),
        title + title.replace("Title6", "Title7").replace("/#Title7", "/"),
        title + "<li>Missing native link</li>",
    ):
        with pytest.raises(ValueError):
            florida.title_inventory(titles(value))
    for value in (
        EDITION,
        EDITION + listed + listed,
        EDITION + listed.replace("Chapter88", "Chapter83"),
        EDITION + listed.replace("Chapter88", "Chapter88/Part_I"),
        EDITION + listed.replace('id="Title6"', 'id="Title7"'),
        EDITION + listed.replace(' href="/Laws/Statutes/2026/Chapter88"', ""),
        EDITION
        + listed.replace('<a href="/Laws/Statutes/2026/Chapter88">Chapter 88</a>', "Chapter 88"),
    ):
        with pytest.raises(ValueError):
            florida.chapter_inventory(page(value), 6)


def test_whole_section_keeps_numbering_history_notes_reversions_and_raw_footnote():
    body = (
        '<div class="Subsection"><span class="Number">(1)</span>Current condition.'
        '<div class="Paragraph"><span class="Number">(a)</span>Nested exception.'
        '<div class="SubParagraph"><span class="Number">1.</span>Further qualification.'
        '</div></div></div><sup><a href="#1">1</a></sup>'
    )
    native = section(wording=body).removesuffix("</div>") + (
        '<div class="History">History. Session-law citation.</div>'
        '<div class="Note"><sup><a name="1">1</a></sup>Note. Effective January 1, 2028, '
        'the subsection will read:<p class="Reversion Justify">Alternative replacement '
        "wording; independent amendments preserved.</p></div></div>"
    )
    data = chapter(native)
    unit, context = florida.chapter_units(data, "83", URL)
    node = html.fromstring(data).xpath('//div[@class="Section"]')[0]
    assert unit.markup == markup(node) and unit.text == readable(node)
    assert all(
        word in unit.text for word in ("(1)", "(a)", "1.", "History.", "2028", "Alternative")
    )
    assert unit.key == "fl:stat/83.01" and unit.parent_key == "fl:chapter/83"
    assert unit.url == URL and unit.metadata["source_anchor"] is None
    (ref,) = unit.references
    assert ref.target == URL + "#1" and ref.evidence == '<a href="#1">1</a>'
    assert context.key == "fl:chapter/83/_context" and context.unit_kind == "scope_notes"
    assert "83.01" in context.text and "Alternative" not in context.text
    assert "FIXTURE CHAPTER" in "\n".join(unit.metadata["hierarchy"])


def test_body_identity_missing_indexes_stubs_and_empty_sections_fail_closed():
    invalid = (
        chapter(numbers=()),
        chapter(numbers=("83.02",)),
        chapter(section("84.01"), numbers=("84.01",)),
        chapter(section(wording="")),
        chapter().replace(b"CHAPTER 83", b"CHAPTER 84"),
        page(EDITION + '<div class="Chapter"><div class="CatchlineIndex">83.01</div></div>'),
        chapter(section(wording="A permit is required unless")).split(b"unless")[0] + b"unless",
    )
    for data in invalid:
        with pytest.raises(ValueError):
            florida.chapter_units(data, "83", URL)


def test_duplicate_native_numbers_preserve_occurrences_and_index_multiplicity():
    data = chapter(
        section(wording="First source wording.") + section(wording="Second wording."),
        numbers=("83.01", "83.01"),
    )
    first, second, context = florida.chapter_units(data, "83", URL)
    assert [first.key, second.key] == ["fl:stat/83.01/occurrence/1", "fl:stat/83.01/occurrence/2"]
    assert (
        first.metadata["duplicate_number_count"] == second.metadata["duplicate_number_count"] == 2
    )
    assert "First source wording" in first.text and "Second wording" in second.text
    assert context.metadata["source_sections"] == 2
    # A set comparison would wrongly accept this missing second index occurrence.
    with pytest.raises(ValueError, match="membership"):
        florida.chapter_units(chapter(section() + section()), "83", URL)


def test_driver_limit_resume_and_idempotence_preserve_legacy_projection(store, monkeypatch):
    """All receipts are synthetic and already cached; even a mock request is a failure."""
    old_review = {"selected_title_numbers": [1], "meaning": "Original accepted scope"}
    store.collection(
        florida.COLLECTION,
        ("us-fl", "Florida", "state", "us"),
        name="Selected Florida chapters",
        authority="Fixture",
        kind="statute",
        homepage=florida.INDEX,
        source_status="Fixture",
        access="Offline fixture",
        metadata={"discovery_review": old_review},
    )

    def cached(url, data):
        receipt = retain(store, data)
        with store.db:
            store.db.execute(
                "UPDATE acquisitions SET url=?,final_url=? WHERE id=?", (url, url, receipt.id)
            )
        return receipt

    cached(florida.BASE + "/About/Privacy", b"Fixture privacy notice")
    entries = []
    for number in ("1", "2", "3"):
        url = florida.BASE + f"/Laws/Statutes/2026/Chapter{number}/All"
        receipt = cached(url, chapter(numbers=(number + ".01",), chapter_number=number))
        entries.append({"number": number, "url": url, "label": "Chapter " + number})
        if number == "1":
            legacy_receipt = receipt
    plan = {
        "edition_year": 2026,
        "chapter_count": 3,
        "receipts": [],
        "titles": [
            {
                "number": 1,
                "url": florida.BASE + "/Laws/Statutes/2026/Title1/",
                "chapters": entries,
                "inventory_acquisition": legacy_receipt.id,
            }
        ],
    }
    monkeypatch.setattr(florida, "inventory_edition", lambda *_: deepcopy(plan))
    document = "fl:chapter/1"
    version = stable_id(document, legacy_receipt.sha256, "", "", "fl-html-1/text-2")
    unit_id = stable_id(version, "fl:stat/1.01")
    # Hand-built historical-profile fixture, not current text labeled as an old replay.
    with store.db:
        store.db.execute(
            "INSERT INTO documents VALUES (?,?,?,?)",
            (document, florida.COLLECTION, "Legacy chapter", entries[0]["url"]),
        )
        store.db.execute(
            "INSERT INTO versions(id,document_id,artifact_sha,acquisition_id,snapshot_basis,"
            "parser,metadata,available_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                version,
                document,
                legacy_receipt.sha256,
                legacy_receipt.id,
                "Legacy fixture",
                "fl-html-1/text-2",
                '{"legacy":true}',
                legacy_receipt.observed_at,
            ),
        )
        store.db.execute(
            "INSERT INTO provisions(id,version_id,key,parent_key,ordinal,unit_kind,citation,"
            "heading,text,markup,url,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                unit_id,
                version,
                "fl:stat/1.01",
                document,
                0,
                "section",
                "Legacy fixture",
                "",
                "Legacy projection retained exactly.",
                "<p>Legacy fixture.</p>",
                entries[0]["url"],
                "{}",
            ),
        )
    before = tuple(store.db.execute("SELECT * FROM versions WHERE id=?", (version,)).fetchone())
    before_unit = tuple(
        store.db.execute("SELECT * FROM provisions WHERE id=?", (unit_id,)).fetchone()
    )

    def forbidden(request):
        pytest.fail("Offline driver fixture attempted request: " + str(request.url))

    acquirer = Acquirer(store)
    acquirer.client.close()
    acquirer.client = httpx.Client(transport=httpx.MockTransport(forbidden))
    try:
        first = florida.sync_florida(store, acquirer, 1, None)
        assert first["new_versions"] == 1 and first["reused_versions"] == 1
        assert first["scope"]["chapters_indexed"] == 2
        assert first["scope"]["missing_chapter_sample"] == [{"chapter": "3", "status": "pending"}]
        second = florida.sync_florida(store, acquirer, 1, None)
        assert second["new_versions"] == 1 and second["reused_versions"] == 2
        assert second["scope"]["chapters_indexed"] == 3
        third = florida.sync_florida(store, acquirer, 1, None)
        assert third["new_versions"] == 0 and third["reused_versions"] == 3
        assert third["scope"]["missing_chapter_count"] == 0
        assert acquirer.downloaded == 0
        assert len(list((store.root / "florida-edition").glob("run-*.json"))) == 3
    finally:
        acquirer.close()
    assert (
        tuple(store.db.execute("SELECT * FROM versions WHERE id=?", (version,)).fetchone())
        == before
    )
    assert (
        tuple(store.db.execute("SELECT * FROM provisions WHERE id=?", (unit_id,)).fetchone())
        == before_unit
    )
    assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 3
    meta = json.loads(
        store.db.execute(
            "SELECT metadata FROM collections WHERE id=?", (florida.COLLECTION,)
        ).fetchone()[0]
    )
    assert meta["historical_discovery_review"] == old_review
    assert meta["discovery_review"]["chapters_indexed"] == 3
