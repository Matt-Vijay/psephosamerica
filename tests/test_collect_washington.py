import json

import httpx
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
            f'<div id="contentWrapper"><a href="#{cite}.010">Contents</a></div>'
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
