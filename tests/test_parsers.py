import pytest
from conftest import retain

from psephos.dc import law_unit
from psephos.municipal import nyc_units
from psephos.parse import ecfr_units, readable, uscode_units, xml_root
from psephos.retrieve import Reader


def test_safe_xml_inline_punctuation_and_table_structure():
    with pytest.raises(ValueError, match="DTD"):
        xml_root(b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>')
    node = xml_root(
        b"<section><p>the <em>term</em>, except:</p><table><tr><td>A</td><td>B</td></tr></table></section>"
    )
    assert readable(node) == "the term, except:\nA\tB"


def test_uslm_real_duplicate_shape_and_native_citations():
    root = xml_root(b"""<uscDoc identifier="/us/usc/t5"><section identifier="/us/usc/t5/s3598">
      <num value="3598">3598.</num><heading>First</heading><content>A</content></section>
      <section identifier="/us/usc/t5/s3598"><num value="3598">3598.</num>
      <heading>Second</heading><content>B<ref href="/us/usc/t1/s1">1 USC 1</ref></content></section></uscDoc>""")
    units = list(uscode_units(root, "5"))
    assert [u.key.rsplit("/", 1)[1] for u in units] == ["1", "2"]
    assert all(u.metadata["duplicate_identifier_count"] == 2 for u in units)
    assert units[1].references[0].target == "/us/usc/t1/s1"
    assert "req=granuleid%3AUSC-prelim-title5-section3598" in units[0].url


def test_ecfr_source_images_are_visible_in_read_contract(store):
    # Publisher structure/locators from 21 CFR 10.31; not a fabricated transcription.
    raw = b"""<DIV1 TYPE="TITLE" N="21"><DIV5 TYPE="PART" N="10">
      <DIV8 TYPE="SECTION" N="10.31"><HEAD>Certification</HEAD><P>Include the following:</P>
      <img src="/graphics/er08no16.000.gif"/><P>Verification follows.</P>
      <img src="/graphics/er08no16.001.gif"/></DIV8></DIV5></DIV1>"""
    source = retain(store, raw)
    units = list(ecfr_units(xml_root(raw), "21", "2026-09-03"))
    assert units[0].text.count("not transcribed") == 2
    store.ingest(
        collection="test",
        document="ecfr",
        title="Regulation",
        url=source.url,
        acquisition=source.id,
        snapshot_basis="fixture",
        parser="ecfr",
        provisions=units,
    )
    read = Reader(store).read(units[0].key)
    assert read["text_completeness"] == "incomplete_without_source_media"
    assert len(read["media"]) == 2
    assert read["media"][0]["url"] == "https://www.ecfr.gov/graphics/er08no16.000.gif"
    assert read["media"][0]["acquired_receipt"] is None


def test_dc_ocr_stub_is_not_full_text():
    root = xml_root(
        b'<law id="D.C. Law 1-1"><meta><stub/></meta><num>1-1</num><heading>Act</heading><search-text>UNVERIFIED OCR</search-text></law>'
    )
    unit = law_unit(root)
    assert unit.unit_kind == "law_metadata"
    assert "UNVERIFIED OCR" not in unit.text
    assert "UNVERIFIED OCR" in unit.markup


def test_nyc_embedded_phantom_article_is_not_another_section():
    raw = b"""<html><main><article class="node--type-section" data-section="37-01" about="/37-01">
      <div class="section-header-wrapper"><h3>Rules</h3><time datetime="2025-01-01"/></div>
      <div class="sec-body">Text<article data-section="malformed">Preserved words</article></div>
      </article></main></html>"""
    units = list(nyc_units(raw, "https://zoningresolution.planning.nyc.gov/article-iii/chapter-7"))
    assert len(units) == 1 and units[0].key == "nyc-zr:37-01"
    assert "Preserved words" in units[0].text
