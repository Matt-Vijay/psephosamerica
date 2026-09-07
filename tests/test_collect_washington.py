import pytest

from psephos.collect_washington import chapter_units, inventory_links


def test_washington_native_cites_notes_tables_media_and_utf8():
    raw = """<div id="contentWrapper"><a href="#29A.04.010">29A.04.010</a><div>Chapter notes</div></div>
    <span id="ContentPlaceHolder1_dlSectionContent"><span><a name="29A.04.010"></a>
    <div><h3><a class="hidden-print">PDF</a>RCW 29A.04.010</h3></div>
    <div><h3>Election—Definition. (Effective January 1, 2027.)</h3></div>
    <div><p>(1) Voters <b>must</b> register.</p><table><tr><td>Class</td><td>10</td></tr></table>
    <img src="figure.png" alt="district boundaries"></div>
    <div>[ <a href="/law.pdf">2026 c 100 s 2</a>.]</div><div>NOTES: Effective date—2026 c 100.</div>
    </span></span>""".encode()
    units = chapter_units(
        raw, "29A.04", "https://app.leg.wa.gov/RCW/default.aspx?cite=29A.04&full=true"
    )
    section, notes = units
    assert section.key == "wa-rcw:29A.04.010"
    assert "Election—Definition" in section.heading
    assert "PDF" not in section.text
    assert "Class\t10" in section.text
    assert "Effective January 1, 2027" in section.text
    assert section.metadata["source_notes"] == ["[ 2026 c 100 s 2.]"]
    assert section.metadata["tables"] == 1
    assert section.metadata["media"][0]["url"] == "https://app.leg.wa.gov/RCW/figure.png"
    assert '<img src="figure.png"' in section.markup
    assert section.references[0].target == "https://app.leg.wa.gov/law.pdf"
    assert notes.unit_kind == "chapter_notes"


def test_washington_digest_not_legal_text_and_inventory_scope():
    raw = b'<div id="contentWrapper"><table><tr><td><a href="http://app.leg.wa.gov/RCW/default.aspx?cite=29A.04">29A.04</a></td><td>General provisions.</td></tr></table></div>'
    assert inventory_links(raw, 1) == [
        ("29A.04", "https://app.leg.wa.gov/RCW/default.aspx?Cite=29A.04", "General provisions.")
    ]
    assert inventory_links(raw, 0) == []
    with pytest.raises(ValueError, match="digest-only"):
        chapter_units(raw, "29A.04", "https://app.leg.wa.gov/RCW/default.aspx")
    plan = b'<div id="contentWrapper"><div>Reviser\'s note: source</div><div>RESOLUTION OF REDISTRICTING</div><div>District 1: Census Tract 100.</div></div>'
    unit = chapter_units(plan, "29A.76C", "https://app.leg.wa.gov/RCW/")[0]
    assert unit.unit_kind == "redistricting_plan"
    assert unit.key == "wa-rcw:29A.76C"
    assert "Census Tract 100" in unit.text
