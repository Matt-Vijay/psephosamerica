"""Small format/refusal checks; actual accepted bytes are checked by the offline workflow."""

import json

import pytest
from conftest import retain

from psephos.retrieve import Reader
from psephos.store import Provision
from psephos.washington_rules import (
    action_legend,
    filing_links,
    filing_unit,
    sync_washington_rules,
    table_unit,
    wac_chapter,
    wac_inventory,
)

WAC = "https://app.leg.wa.gov/WAC/default.aspx?cite=197-11&full=true"
CHAPTER = b"""<div id="contentWrapper"><table>
<tr><td colspan="3">PART TWO - QUALIFICATIONS</td></tr>
<tr><td><a href="#197-11-060">HTML</a></td><td>197-11-060</td><td>Content.</td></tr>
</table><h3>Disposition of former sections</h3><p>197-11-840 Repealed by source filing.</p></div>
<span id="ContentPlaceHolder1_dlSectionContent"><span><a name="197-11-060"></a>
<div><h3><a href="?pdf=true">PDF</a>197-11-060</h3></div><div><h3>Content of review.</h3></div>
<div>(1) Review is qualified by <a href="http://app.leg.wa.gov/RCW/default.aspx?cite=43.21C.110">body condition</a>.<div>(a) Not appropriate if fragmented.</div><img src="figure.png" alt="boundary diagram"></div>
<div>[Statutory Authority: RCW <a href="http://app.leg.wa.gov/RCW/default.aspx?cite=43.21C.110">43.21C.110</a>.
WSR 97-21-030, filed 10/10/97, effective 11/10/97.]</div></span></span>"""


def test_native_wac_bodies_notes_hierarchy_and_no_digest_substitution(store):
    section, notes = wac_chapter(CHAPTER, "197-11", WAC)
    assert section.key == "wa-wac:197-11-060" and "PDF" not in section.text
    assert "Not appropriate" in section.text and "10/10/97" in section.text
    assert section.metadata["source_part_heading"] == "PART TWO - QUALIFICATIONS"
    assert section.metadata["media"][0]["url"].endswith("/WAC/figure.png")
    assert "Repealed" in notes.text and notes.unit_kind == "chapter_notes"
    assert {r.relation for r in section.references} >= {
        "publisher_statutory_authority",
        "publisher_filing_citation",
    }
    body = next(r for r in section.references if r.label == "body condition")
    assert body.relation != "publisher_statutory_authority" and "body condition" in body.evidence
    assert any(
        r.target == body.target and r.relation == "publisher_statutory_authority"
        for r in section.references
    )
    assert next(
        r for r in section.references if r.target == "wa-wsr:97-21-030"
    ).evidence.startswith("<div>")
    for raw in [
        CHAPTER.split(b"<span id=")[0],
        CHAPTER.replace(b'href="#197-11-060"', b'href="#197-11-999"'),
    ]:
        with pytest.raises(ValueError):
            wac_chapter(raw, "197-11", WAC)
    with pytest.raises(ValueError, match="outside"):
        wac_chapter(CHAPTER, "365-196", WAC)


def test_inventory_does_not_promote_note_links_to_current_chapters():
    data = b"""<div id="contentWrapper"><table><tr><td><a href="http://app.leg.wa.gov/WAC/default.aspx?cite=365-196">365-196</a></td><td>GMA</td></tr></table>
<p>Formerly <a href="?cite=365-200">365-200</a></p></div>"""
    assert wac_inventory(data, "365") == [
        ("365-196", "https://app.leg.wa.gov/WAC/default.aspx?cite=365-196", "GMA")
    ]
    with pytest.raises(ValueError):
        wac_inventory(data.replace(b"app.leg.wa.gov", b"app.leg.wa.gov.evil.test"), "365")


def test_filing_is_not_current_law_and_redlines_remain_labeled():
    raw = b"""<html><body><div>WSR 26-01-181</div><div>PERMANENT RULES</div><div>DEPARTMENT OF COMMERCE</div>
<div>[Filed December 23, 2025, 9:09 a.m., effective January 23, 2026]</div>
<div>Adopted under notice filed as WSR 25-13-090.</div><div>Old <span style="text-decoration:line-through;">deleted words</span><span style="text-decoration:underline;">new words</span>.</div><!-- TextEnd --></body></html>"""
    unit = filing_unit(
        raw, "26-01-181", "https://lawfilesext.leg.wa.gov/law/wsr/2026/01/26-01-181.htm"
    )
    assert unit.metadata["filed_on"] == "2025-12-23"  # Issue year is not filing year.
    assert "[DELETED]deleted words[/DELETED]" in unit.text
    assert "[UNDERLINED]new words[/UNDERLINED]" in unit.text
    assert unit.unit_kind == "rulemaking_filing" and unit.metadata["deletion_spans"] == 1
    assert unit.references[0].target == "wa-wsr:25-13-090"
    for changed in [
        raw[:-8],
        raw.replace(b"PERMANENT RULES", b"EMERGENCY RULES"),
        raw.replace(b"TextEnd", b"missing"),
    ]:
        with pytest.raises(ValueError):
            filing_unit(changed, "26-01-181", unit.url)
    with pytest.raises(ValueError):
        filing_unit(raw, "26-01-182", unit.url)


def test_action_legend_and_table_keep_proposals_continuances_and_empty_years():
    meanings = {
        "AMD": "Amendment",
        "NEW": "New section",
        "-P": "Proposed action",
        "-C": "Continuance",
        "-E": "Emergency",
        "-W": "Withdrawal",
    }
    data = (
        "<table>"
        + "".join(f"<tr><td>{k}</td><td>=</td><td>{v}</td></tr>" for k, v in meanings.items())
        + "</table>"
    ).encode()
    legend = action_legend(data)
    table = b"""<h1>2025 WAC-to-Register Table</h1><h2>Title 365 WAC</h2><table id="table"><tr><td>365-196-443</td><td>NEW-P</td><td><a href="http://lawfilesext.leg.wa.gov/law/wsr/2025/13/25-13-090.htm">25-13-090</a></td><td>PDF</td></tr></table>"""
    unit = table_unit(
        table, "365", "https://lawfilesext.leg.wa.gov/law/wsr/2025/tbl25-365.htm", legend
    )
    assert (
        unit.metadata["printed_year"] == "2025"
        and "Proposed" in unit.metadata["rows"][0]["meaning"]
    )
    assert "NEW-P" in unit.references[0].evidence and unit.unit_kind == "filing_index"
    for action in (b"AMD-C", b"AMD-E", b"AMD-W"):
        assert (
            table_unit(table.replace(b"NEW-P", action), "365", unit.url, legend).metadata["rows"][
                0
            ]["action"]
            == action.decode()
        )
    with pytest.raises(ValueError, match="Unknown action"):
        table_unit(table.replace(b"NEW-P", b"AMD-Z"), "365", unit.url, legend)
    assert filing_links(table, unit.url)["25-13-090"].startswith("https://lawfilesext.leg.wa.gov/")
    with pytest.raises(ValueError):
        filing_links(table.replace(b"/2025/13/", b"/2026/13/"), unit.url)
    empty = b'<h1>2025 WAC-to-Register Table</h1><h2>Title 197 WAC</h2><table id="table"><tr><td>NO FILINGS FOR THIS TITLE</td></tr></table>'
    assert table_unit(empty, "197", unit.url, legend).metadata["rows"] == []


def test_refusal_is_before_any_acquisition():
    for limit, as_of in [(None, "2020-01-01"), (0, None)]:
        with pytest.raises(ValueError):
            sync_washington_rules(None, None, limit, as_of)


def test_bulk_action_rows_are_disclosed_not_repeated_in_read_or_versions(store):
    source = retain(store, b"native action table")
    metadata = {
        "rows": [
            {"wac": "365-195-900", "action": "AMD-P", "evidence": "x" * 500} for _ in range(163)
        ],
        "printed_year": "2025",
    }
    key = "wa-wsr:table:2025:365"
    store.ingest(
        collection="test",
        document=key,
        title="Table",
        url=source.url,
        acquisition=source.id,
        snapshot_basis="Fixture",
        parser="fixture",
        metadata=metadata,
        provisions=[
            Provision(
                key,
                "Table",
                "Actions",
                "365-195-900\tAMD-P\t25-13-090",
                "<table><tr><td>AMD-P</td></tr></table>",
                source.url,
                unit_kind="filing_index",
                metadata=metadata,
            )
        ],
    )
    reader = Reader(store)
    original = store.db.execute(
        "SELECT p.metadata,v.metadata FROM provisions p JOIN versions v ON v.id=p.version_id WHERE p.key=?",
        (key,),
    ).fetchone()
    result = reader.read(key, length=500, include_markup=True)
    for field in ("metadata", "version_metadata"):
        assert result[field]["rows_count"] == 163 and "rows" not in result[field]
        assert result[field]["rows_projection"] and result[field]["printed_year"] == "2025"
    assert (
        len(json.dumps(result)) < 10000
        and "AMD-P" in result["text"]
        and "AMD-P" in result["markup"]
    )
    assert reader.find(key, "AMD-P")["matches"]
    assert reader.versions(key)["versions"][0]["metadata"]["rows_count"] == 163
    assert tuple(original) == tuple(
        store.db.execute(
            "SELECT p.metadata,v.metadata FROM provisions p JOIN versions v ON v.id=p.version_id WHERE p.key=?",
            (key,),
        ).fetchone()
    )
