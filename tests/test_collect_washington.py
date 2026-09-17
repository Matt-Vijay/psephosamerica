import json

import httpx
import pytest

from psephos.collect_washington import chapter_units, inventory_links


def test_centered_subchapter_heading_is_retained_but_not_invented_as_section():
    raw = b"""<div id="contentWrapper"><table><tr><td>HTML</td>
    <td><a href="#47.26.010">47.26.010</a></td><td>Section</td></tr></table></div>
    <span id="ContentPlaceHolder1_dlSectionContent">
      <span><a name="47.26.010"/><div><h3>RCW 47.26.010</h3></div>
        <div><h3>Native section</h3></div><p>Exact source wording.</p></span>
      <span><br/><hr/><a name="47.26.4999999"/>
        <div style="text-align: center;">UNNUMBERED CHAPTER HEADING</div></span>
    </span>"""
    units = chapter_units(raw, "47.26", "https://app.leg.wa.gov/RCW/?cite=47.26&full=true")
    assert [p.unit_kind for p in units] == ["section", "subchapter_heading", "chapter_notes"]
    assert units[1].key == "wa-rcw:47.26/heading/47.26.4999999"
    assert units[1].metadata["not_a_numbered_section"]
    assert units[1].text == "UNNUMBERED CHAPTER HEADING"
    multiline = raw.replace(
        b"</div></span>", b'</div><div style="text-align:center;">Second heading line</div></span>'
    )
    assert chapter_units(multiline, "47.26", "https://app.leg.wa.gov/")[1].text == (
        "UNNUMBERED CHAPTER HEADING\nSecond heading line"
    )
    with pytest.raises(ValueError, match="do not match"):
        chapter_units(
            raw.replace(b"UNNUMBERED CHAPTER HEADING", b"<p>Unlisted law body</p>"),
            "47.26",
            "https://app.leg.wa.gov/",
        )
    with pytest.raises(ValueError, match="do not match"):
        chapter_units(
            raw.replace(
                b"</table>",
                b'<tr><td>HTML</td><td><a href="#47.26.020">47.26.020</a></td><td>Missing</td></tr></table>',
                1,
            ),
            "47.26",
            "https://app.leg.wa.gov/",
        )


def test_toc_heading_citations_are_not_inventory_and_absent_toc_is_rejected():
    raw = b"""<div id="contentWrapper"><table><tr><td>HTML</td>
    <td><a href="#85.05.550">85.05.550</a></td>
    <td>Application of <a href="#85.05.510">85.05.510</a>.</td></tr></table></div>
    <span id="ContentPlaceHolder1_dlSectionContent"><span><a name="85.05.550"/>
    <div><h3>RCW 85.05.550</h3></div><div>Exact body.</div></span></span>"""
    units = chapter_units(raw, "85.05", "https://app.leg.wa.gov/RCW/")
    assert [u.key for u in units] == ["wa-rcw:85.05.550", "wa-rcw:85.05/contents-notes"]
    assert "85.05.510" in units[-1].text
    with pytest.raises(ValueError, match="No publisher chapter contents"):
        chapter_units(
            raw.replace(b'id="contentWrapper"', b'id="changed"'), "85.05", "https://app.leg.wa.gov/"
        )


def test_washington_campaign_preserves_original_cap_and_retained_denials(store, tmp_path):
    from psephos.acquire import AcquisitionError
    from psephos.collect_washington import WashingtonAcquirer

    a = WashingtonAcquirer(store, tmp_path)
    try:
        assert a.budget["cap_bytes"] == 250 * 1024**2
        url = "https://app.leg.wa.gov/RCW/default.aspx?cite=85.05&full=true"
        a._record(url, url, "2026-09-17T00:00:00Z", 403, {}, None, "Forbidden")
        with pytest.raises(AcquisitionError, match="Retained access denial"):
            a.fetch(url)
        with pytest.raises(AcquisitionError, match="reviewed publisher"):
            a.fetch("https://example.test/source")
    finally:
        a.close()


def test_washington_native_cites_notes_tables_media_and_utf8():
    raw = """<div id="contentWrapper"><table><tr><td>HTML</td><td><a href="#29A.04.010">29A.04.010</a></td><td>Heading</td></tr></table><div>Chapter notes</div></div>
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


@pytest.mark.parametrize(
    "chapter,body",
    [
        ("14.30", "<div>PDF</div><h3>See chapter 81.96 RCW</h3>"),
        (
            "18.09",
            '<h3>Notes:</h3><div>See chapter <a href="?cite=2.44">2.44</a> RCW, attorneys-at-law.</div>',
        ),
    ],
)
def test_retained_pointer_only_chapters_are_not_counted_as_sections(chapter, body):
    raw = f'<div id="contentWrapper">{body}</div>'.encode()
    units = chapter_units(raw, chapter, "https://app.leg.wa.gov/RCW/default.aspx")
    assert len(units) == 1 and units[0].unit_kind == "chapter_notes"
    assert units[0].metadata["publisher_note_only_chapter"]
    with pytest.raises(ValueError, match="refusing digest-only"):
        chapter_units(raw.replace(b"RCW", b"other material"), chapter, "https://app.leg.wa.gov/")


def test_washington_cli_limited_resume_skips_accepted_chapter_preserving_ids(
    store, monkeypatch, capsys
):
    from psephos.acquire import Acquirer
    from psephos.cli import main
    from psephos.collect_washington import ARCHIVES, AUTHORITY, DISCLAIMER, HOME

    def rows(cites):
        return (
            '<div id="contentWrapper"><table>'
            + "".join(
                f'<tr><td><a href="?Cite={cite}">{cite}</a></td><td>Source heading</td></tr>'
                for cite in cites
            )
            + "</table></div>"
        ).encode()

    def chapter(cite):
        return (
            f'<div id="contentWrapper"><table><tr><td>HTML</td><td><a href="#{cite}.010">{cite}.010</a></td><td>Contents</td></tr></table></div>'
            '<span id="ContentPlaceHolder1_dlSectionContent"><span>'
            f'<a name="{cite}.010"></a><div><h3>RCW {cite}.010</h3></div>'
            "<div><h3>Native heading</h3></div><p>Exact source law.</p></span></span>"
        ).encode()

    first_url = HOME + "?cite=1.01&full=true"
    second_url = HOME + "?cite=1.02&full=true"
    bodies = {
        AUTHORITY: b"Publisher authority",
        DISCLAIMER: b"Publisher disclaimer",
        ARCHIVES: b"Publisher archive inventory",
        HOME: rows(["1"]),
        HOME + "?Cite=1": rows(["1.01", "1.02"]),
        first_url: chapter("1.01"),
        second_url: chapter("1.02"),
        **{
            f"https://wslwebservices.leg.wa.gov/{service}.asmx?WSDL": b"Source service metadata"
            for service in (
                "RcwCiteAffectedService",
                "SessionLawService",
                "LegislativeDocumentService",
            )
        },
    }
    requested = []

    def respond(request):
        url = str(request.url)
        requested.append(url)
        raw = b"User-agent: *\nAllow: /" if request.url.path == "/robots.txt" else bodies[url]
        return httpx.Response(200, stream=httpx.ByteStream(raw))

    original_init = Acquirer.__init__

    def offline_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.client.close()
        self.client = httpx.Client(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(Acquirer, "__init__", offline_init)
    monkeypatch.setattr(Acquirer, "_pause", lambda *args: None)
    monkeypatch.setattr(
        "sys.argv", ["psephos", "--data", str(store.root), "sync", "washington", "--limit", "1"]
    )
    main()
    first_output = capsys.readouterr()
    assert json.loads(first_output.out)["requested_sources"] == ["washington"]
    assert "Washington 1.01" in first_output.err
    prior_versions = [tuple(row) for row in store.db.execute("SELECT * FROM versions")]
    prior_provisions = {
        row["id"]: tuple(row) for row in store.db.execute("SELECT * FROM provisions")
    }
    assert len(prior_versions) == 1 and requested.count(first_url) == 1
    assert second_url not in requested

    main()
    resumed_output = capsys.readouterr()
    result = json.loads(resumed_output.out)  # Progress on stdout would make this fail.
    assert result["downloaded_this_run"] == len(bodies[second_url])
    assert "Washington 1.02" in resumed_output.err and "Washington 1.01" not in resumed_output.err
    assert requested.count(first_url) == requested.count(second_url) == 1
    assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 2
    assert all(
        tuple(store.db.execute("SELECT * FROM versions WHERE id=?", (row[0],)).fetchone()) == row
        for row in prior_versions
    )
    assert all(
        tuple(store.db.execute("SELECT * FROM provisions WHERE id=?", (key,)).fetchone()) == row
        for key, row in prior_provisions.items()
    )
