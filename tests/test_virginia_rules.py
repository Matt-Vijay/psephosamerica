"""Native identities, intact chapter body/hierarchy, and truthful source quirks."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from conftest import retain

from psephos.acquire import AcquisitionError
from psephos.campaign import CampaignAcquirer, save
from psephos.parse import readable
from psephos.retrieve import Reader
from psephos.store import Provision
from psephos.virginia_rules import chapter_index, chapter_units, expanded_inventory, preface_units

ITEM = {"title": "9", "agency": "25", "chapter": "875"}
INDEX = b"""<h2>Chapter 875</h2><a href="/admincodefull/title9/agency25/chapter875">Read Chapter</a>
<dl><dt><a href="/admincode/title9/agency25/chapter875/section10/">Section 10</a></dt><dd>General</dd>
<dt><a href="/admincode/title9/agency25/chapter875/section20/">Section 20</a></dt><dd>Definitions</dd></dl>"""
BODY = b"""<html><head><title>A misleading source title</title></head><body><article id="admincode">
<h2>Chapter 875. Source chapter</h2><p class="part">Part I<br>General Provisions</p>
<p class="vacno">9VAC25-875-10. General.</p><p>Except as provided in Part II.</p><p class="auth">Authority: source citation.</p>
<p class="part">Part II<br>Separate definitions</p><p class="vacno">9VAC25-875-20. Definitions.</p>
<p>For this part only.</p><table><tr><td rowspan="2">A</td><td>1/2</td></tr><tr><td>3</td></tr></table>
<img src="/source-image.png" alt="Source diagram"><p class="history">Effective date appears only in this source history.</p>
</article><p id="sidenote1">Source IBR warning.</p></body></html>"""


def test_native_chapter_order_scope_and_source_body_fidelity():
    from lxml import html

    index = chapter_index(INDEX, ITEM)
    units, meta = chapter_units(BODY, ITEM, index)
    assert [u.key for u in units if u.unit_kind == "section"] == [
        "va-vac:9VAC25-875-10",
        "va-vac:9VAC25-875-20",
    ]
    assert units[-1].metadata["hierarchy"] == ["Part II\nSeparate definitions"]
    assert 'rowspan="2"' in units[-1].markup and meta["tables"] == 1 and meta["images"] == 1
    assert "Effective date appears" in units[-1].text
    assert "\n".join(u.text for u in units) == readable(html.fromstring(BODY).xpath("//article")[0])
    assert meta["source_title"] == "A misleading source title"
    assert meta["source_notices"] == ["Source IBR warning."]
    with pytest.raises(ValueError, match="roster/order"):
        chapter_units(BODY, ITEM, {**index, "sections": list(reversed(index["sections"]))})
    with pytest.raises(ValueError, match="identity"):
        chapter_units(BODY.replace(b"9VAC25-875-20.", b"9VAC25-875-30."), ITEM, index)
    with pytest.raises(ValueError, match="Read Chapter"):
        chapter_index(
            INDEX.replace(b"/admincodefull/", b"https://example.test/admincodefull/"), ITEM
        )


def test_duplicate_publisher_heading_is_preserved_inside_one_native_section():
    index = chapter_index(INDEX, ITEM)
    duplicate = b'<p class="vacno">9VAC25-875-20. Definitions.</p>'
    body = BODY.replace(duplicate, duplicate + b'<p class="vacno"></p>' + duplicate)
    units, meta = chapter_units(body, ITEM, index)
    assert units[-1].text.count("9VAC25-875-20. Definitions.") == 2
    assert len([u for u in units if u.key == "va-vac:9VAC25-875-20"]) == 1
    assert meta["empty_source_section_headings"] == 1 and '<p class="vacno"/>' in units[-1].markup


def test_native_title_inventory_retains_labels_and_rejects_scope_mixing():
    source = b"""<dl><dt><a href="/admincodeexpand/title9/agency25/">Agency 25</a></dt><dd>Board</dd>
    <dt><a href="/admincode/title9/agency25/chapter875/">Chapter 875</a></dt><dd>A  source label [Repealed]</dd></dl>"""
    agencies, chapters = expanded_inventory(source, "9", "https://law.lis.virginia.gov/")
    assert (
        len(agencies) == len(chapters) == 1 and chapters[0]["label"] == "A  source label [Repealed]"
    )
    with pytest.raises(ValueError, match="another title"):
        expanded_inventory(source, "8", "https://law.lis.virginia.gov/")


def test_repealed_forms_heading_uses_native_forms_link_not_repeal_label():
    forms_index = b'<dl><dt><a href="/admincode/title9/agency25/chapter875/section9998/">FORMS</a></dt><dd>[Repealed]</dd></dl>'
    index = chapter_index(
        INDEX + forms_index + forms_index,
        ITEM,
    )
    body = BODY.replace(
        b"</article>",
        b'<p class="vacno">Forms (9VAC25-875)</p><p>Repealed forms source note.</p><p class="vacno">Forms (9VAC25-875)</p><p>Second source body retained.</p></article>',
    )
    units, meta = chapter_units(body, ITEM, index)
    assert units[-1].key == "va-vac:9VAC25-875-9998"
    assert units[-1].unit_kind == "source_reference_list"
    assert "Repealed forms source note." in units[-1].text
    assert "Second source body retained." in units[-1].text
    assert meta["repeated_native_index_entries"] == {"9998": 1}
    assert units[-1].metadata["native_index_occurrences"] == 2
    assert len([u for u in units if u.unit_kind == "source_reference_list"]) == 1
    with pytest.raises(ValueError, match="Duplicate section"):
        chapter_index(
            INDEX + forms_index + forms_index.replace(b"[Repealed]", b"Different label"), ITEM
        )


def test_identical_index_repetitions_preserve_both_bodies_without_choosing_a_version():
    entry = b'<dt><a href="/admincode/title9/agency25/chapter875/section20/">Section 20</a></dt><dd>Definitions</dd>'
    index = chapter_index(INDEX.replace(entry, entry + entry), ITEM)
    repeat = b'<p class="article"><br></p><p class="vacno">9VAC25-875-20. Definitions.</p><p>Different repeated source wording.</p>'
    units, meta = chapter_units(BODY.replace(b"</article>", repeat + b"</article>"), ITEM, index)
    assert units[-1].text.count("9VAC25-875-20. Definitions.") == 2
    assert "Different repeated source wording." in units[-1].text
    assert units[-1].metadata["native_index_occurrences"] == 2
    assert "not deduplicated or resolved" in units[-1].metadata["identity_note"]
    assert meta["empty_source_hierarchy_blocks"] == 1
    unnamed = BODY.replace(
        b"</article>",
        b'<p class="article"></p><p>Unnamed source context with actual content.</p></article>',
    )
    preserved, metadata = chapter_units(unnamed, ITEM, chapter_index(INDEX, ITEM))
    assert preserved[-1].unit_kind == "hierarchy_context" and preserved[-1].heading == ""
    assert preserved[-1].text == "Unnamed source context with actual content."
    assert "empty_source_hierarchy_blocks" not in metadata


def test_cross_part_duplicates_use_existing_ambiguous_occurrence_reading(store):
    entry = '<dt><input class="part{part}"><a href="/admincode/title9/agency25/chapter875/section20/">Section 20</a></dt><dd><div id="sectionID{id}">Definitions</div></dd>'
    index = chapter_index(
        (
            '<a href="/admincodefull/title9/agency25/chapter875">Read</a><dl>'
            + entry.format(part="II", id=1)
            + entry.format(part="III", id=2)
            + "</dl>"
        ).encode(),
        ITEM,
    )
    body = b'<article id="admincode"><h2>Chapter 875. Source</h2><p class="part">Part II</p><p class="vacno">9VAC25-875-20. Definitions.</p><p>First source body.</p><p class="part">Part III</p><p class="vacno">9VAC25-875-20. Definitions.</p><p>Second source body.</p></article>'
    units, _ = chapter_units(body, ITEM, index)
    sections = [u for u in units if u.unit_kind == "section"]
    assert [u.key for u in sections] == [
        "va-vac:9VAC25-875-20/_occurrence/1",
        "va-vac:9VAC25-875-20/_occurrence/2",
    ]
    assert sections[1].metadata["publisher_entry_ids"] == ["sectionID2"]
    source = retain(store, body)
    store.ingest(
        collection="test",
        document="occurrences",
        title="Source",
        url=source.url,
        acquisition=source.id,
        parser="fixture",
        snapshot_basis="Fixture",
        provisions=units,
    )
    result = Reader(store).read("va-vac:9VAC25-875-20")
    assert not result["found"] and len(result["matches"]) == 2
    assert Reader(store).read(sections[1].key)["text"].endswith("Second source body.")
    with pytest.raises(ValueError, match="identity|roster/order"):
        chapter_units(body.replace(b"Part III", b"Part IV"), ITEM, index)


def test_unclosed_zero_padding_wrappers_cannot_import_site_footer_as_law():
    malformed = BODY.replace(
        b'<p class="vacno">9VAC25-875-10.',
        b'<div style="padding: 0px;"><p class="vacno">9VAC25-875-10.',
    )
    malformed = malformed.replace(b"</body>", b"<script>NOT LEGAL TEXT</script></body>")
    units, meta = chapter_units(malformed, ITEM, chapter_index(INDEX, ITEM))
    assert meta["unclosed_layout_wrappers"] == 1
    assert not any("NOT LEGAL TEXT" in u.text for u in units)
    assert units[-1].key == "va-vac:9VAC25-875-20"
    assert meta["images"] == 1 and meta["tables"] == 1
    with pytest.raises(ValueError, match="Attributed wrapper"):
        chapter_units(
            malformed.replace(b"padding: 0px;", b"counter-reset: item;"),
            ITEM,
            chapter_index(INDEX, ITEM),
        )
    attributed = malformed.replace(b'style="padding: 0px;"', b'data-show-promulgator="1"')
    preserved, context = chapter_units(attributed, ITEM, chapter_index(INDEX, ITEM))
    assert context["preserved_cross_section_wrappers"] == [{"data-show-promulgator": "1"}]
    assert all(
        'data-show-promulgator="1"' in u.markup for u in preserved if u.unit_kind == "section"
    )
    assert [u.text for u in preserved] == [u.text for u in units]


def test_preflight_ceiling_does_not_reset_campaign_allowance(store, tmp_path):
    d = tmp_path / "campaign"
    d.mkdir()
    save(
        d / "budget.json",
        {"consumed_bytes": 1048576, "last_request_unix": 0, "cap_bytes": 536870912},
    )
    a = CampaignAcquirer(store, d, ceiling=16 * 1024**2)
    assert a.downloaded == 1048576 and a.max_bytes == 16 * 1024**2
    a.close()
    b = CampaignAcquirer(store, d)
    assert b.downloaded == 1048576 and b.max_bytes == 536870912
    assert json.loads((d / "budget.json").read_bytes())["consumed_bytes"] == 1048576
    b.close()
    with pytest.raises(AcquisitionError, match="cap differs"):
        CampaignAcquirer(store, d, cap=1024)


def test_completed_campaign_excludes_later_independent_source_receipts(
    store, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    from verify_virginia import audit_campaign

    source = retain(store, b"VAC fixture")
    with store.db:
        store.db.execute(
            "UPDATE acquisitions SET url=?,headers=? WHERE id=?",
            (
                "https://law.lis.virginia.gov/admincode/",
                json.dumps({"psephos_downloaded_bytes": source.size}),
                source.id,
            ),
        )
    retain(store, b"Unrelated later source")
    boundary = {
        "first_acquisition_id": source.id,
        "last_acquisition_id": source.id,
        "initial_consumed_bytes": 0,
    }
    save(tmp_path / "campaign-start.json", boundary)
    save(tmp_path / "budget.json", {"consumed_bytes": source.size, "cap_bytes": 536870912})
    result = audit_campaign(store, tmp_path)
    assert result["receipted_payload_bytes"] == source.size
    assert result["last_acquisition_id"] == source.id
    boundary.pop("last_acquisition_id")
    save(tmp_path / "campaign-start.json", boundary)
    with pytest.raises(AssertionError, match="Another source writer"):
        audit_campaign(store, tmp_path)


def test_missing_anchor_separator_keeps_exact_literal_reference_and_text():
    link = b'<p><ahref="http://source.test/document?x=1&amp;y=2">SourceWord</a></p>'
    units, meta = chapter_units(
        BODY.replace(b"</article>", link + b"</article>"), ITEM, chapter_index(INDEX, ITEM)
    )
    assert meta["malformed_anchor_whitespace_repairs"] == 1
    assert units[-1].references[-1].target == "http://source.test/document?x=1&y=2"
    assert units[-1].references[-1].label == "SourceWord"
    assert units[-1].text.endswith("SourceWord")


def test_preface_api_body_and_absence_are_not_confused_with_rules():
    record = {
        "TitleNumber": "9",
        "AgencyNumber": "25",
        "AgencyName": "Water Board",
        "PrefaceSummary": "<p>Publisher agency summary.</p>",
    }
    item = {**ITEM, "label": "Water Board"}
    units = preface_units(
        json.dumps(record).encode(),
        item,
        "https://law.lis.virginia.gov/api/AdministrativeCodePrefaceXml/9/25/",
    )
    assert units[0].unit_kind == "agency_summary"
    assert units[0].text == "Publisher agency summary."
    assert (
        preface_units(
            json.dumps({**record, "PrefaceSummary": None}).encode(), item, "https://example.test"
        )
        == []
    )
    with pytest.raises(ValueError, match="identity"):
        preface_units(
            json.dumps({**record, "TitleNumber": "8"}).encode(), item, "https://example.test"
        )


def test_image_receipts_and_missing_resources_resume_without_refetch(store, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "va_completion_test", Path("scripts/complete_virginia.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "COLLECTION", "test")
    urls = [
        "https://law.lis.virginia.gov/RISImages/fixture.png",
        "https://law.lis.virginia.gov/RISImages/missing.png",
        "https://ris.dls.virginia.gov/uploads/fixture.png",
        "http://law.lis.virginia.gov/RISImages/not-upgraded.png",
    ]
    source = retain(store, b"Fixture chapter")
    store.ingest(
        collection="test",
        document="chapter",
        title="Fixture",
        url=source.url,
        acquisition=source.id,
        parser="fixture",
        snapshot_basis="Fixture observation only",
        provisions=[
            Provision(
                "va-image-fixture",
                "Fixture",
                "Fixture",
                "Body",
                "",
                source.url,
                metadata={"media": [{"url": url} for url in urls]},
            )
        ],
    )
    (store.root / "virginia-completion").mkdir()
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, stream=httpx.ByteStream(b"User-agent: *\nAllow: /"))
        assert str(request.url) in urls[:3]
        if request.url.path.endswith("missing.png"):
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            stream=httpx.ByteStream(b"image-response-fixture"),
        )

    cls = module.VirginiaAcquirer
    monkeypatch.setattr(cls, "_pause", lambda *args: None)

    def client(s, directory, **kwargs):
        a = cls(s, directory, delay=0)
        a.client.close()
        a.client = httpx.Client(
            transport=httpx.MockTransport(handler), event_hooks={"response": [a._bounded_response]}
        )
        return a

    monkeypatch.setattr(module, "VirginiaAcquirer", client)
    result = module.media(store.root)
    assert result["retained_image_responses"] == 2 and result["image_urls"] == 4
    before = len(calls)
    assert module.media(store.root) == result and len(calls) == before
    manifest = json.loads((store.root / "virginia-completion/media.json").read_bytes())
    by_url = {m["url"]: m for m in manifest}
    assert by_url[urls[1]]["http_status"] == 404
    assert by_url[urls[-1]]["status"] == "not_acquired_outside_reviewed_image_routes"
    read = Reader(store).read("va-image-fixture")
    assert read["media"][0]["acquired_receipt"] is not None
    assert read["media"][1]["acquired_receipt"] is None
    historical = Reader(store).read("va-image-fixture", observation_cutoff="2026-01-02T00:00:00Z")
    assert all(m["acquired_receipt"] is None for m in historical["media"])
