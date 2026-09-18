import json
from unittest.mock import Mock

import httpx
import pytest

from psephos import new_hampshire as nh
from psephos.acquire import Acquirer, AcquisitionError


def chapter(number="1"):
    return f"""<html><body><center><h1>Title I</h1></center>
    <center><h2>Chapter {number}</h2></center><center><h3>Section {number}:1</h3></center>
    <codesect>Requirement, except for the stated exception.</codesect>
    <sourcenote>Source. Effective January 1, 2030.</sourcenote>
    <table><tr><td>Column retained</td></tr></table></body></html>""".encode()


def test_chapter_partition_preserves_history_tables_variants_and_identity():
    units = nh.parse_chapter(chapter(), "https://gc.nh.gov/test", "I", "1")
    section = units[-1]
    assert section.key == "nh-rsa:1:1"
    assert "except for the stated exception" in section.text
    assert "2030" in section.metadata["source_notes"][0]
    assert "<table>" in section.markup and section.metadata["tables"] == 1
    with pytest.raises(ValueError, match="requested RSA chapter"):
        nh.parse_chapter(chapter("1-A"), "https://gc.nh.gov/test", "I", "1")
    duplicate = chapter().replace(
        b"</body>", b"<center><h3>Section 1:1</h3></center><codesect>Future text.</codesect></body>"
    )
    units = nh.parse_chapter(duplicate, "https://gc.nh.gov/test", "I", "1")
    assert [u.key for u in units if u.unit_kind == "section"] == [
        "nh-rsa:1:1/_occurrence/1",
        "nh-rsa:1:1/_occurrence/2",
    ]
    notice = b"<html><body><center><h2>Chapter 1</h2></center><p>Repealed.</p></body></html>"
    assert all(u.unit_kind == "chapter_notice" for u in nh.parse_chapter(notice, "url", "I", "1"))


def test_native_inventory_rejects_foreign_hosts_and_wrong_labels():
    source = b'<a href="NHTOC/NHTOC-XIX-A.htm">TITLE XIX-A: FORESTRY</a>'
    assert nh.inventory(source)[0][0] == "XIX-A"
    assert (
        nh.inventory(b'<a href="NHTOC-X-126-AA.htm">CHAPTER 126-AA: HEALTH CARE</a>', "X")[0][0]
        == "126-AA"
    )
    with pytest.raises(ValueError, match="differs"):
        nh.inventory(source.replace(b"TITLE XIX-A:", b"TITLE XIX:"))
    with pytest.raises(ValueError, match="Unexpected"):
        nh.inventory(source.replace(b"NHTOC/NHTOC", b"https://other.test/rsa/html/NHTOC/NHTOC"))
    with pytest.raises(ValueError, match="duplicate"):
        nh.inventory(source + source)
    with pytest.raises(ValueError, match="Unrecognized"):
        nh.inventory(source + b'<a href="unknown.htm">TITLE FUTURE: REVIEW</a>')


def test_entire_title_notice_does_not_invent_chapters_from_hidden_metadata():
    data = b"""<html><head><!-- <chapter>CHAPTER 196 SCHOOL DISTRICT BONDS</chapter> --></head>
    <body><center><h1>TITLE IV<br>ELECTIONS</h1></center><b>Title IV Repealed</b>
    <codesect>Entire Title was repealed</codesect></body></html>"""
    unit = nh.title_notice(data, "https://gc.nh.gov/rsa/html/NHTOC/NHTOC-IV.htm", "IV")
    assert unit.key == "nh-rsa:title-IV/_notice" and unit.unit_kind == "title_notice"
    assert "196" not in unit.text and "Entire Title was repealed" in unit.text
    with pytest.raises(ValueError, match="identity"):
        nh.title_notice(data, "url", "V")
    assert (
        nh.title_notice(data.replace(b"Entire Title was repealed", b"Review"), "url", "IV") is None
    )


def test_original_budget_pacing_denials_and_restart_reservation(store, tmp_path):
    path = store.root / "collectors/nh-northeast/budget.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "cap_bytes": 188743680,
                "newly_decoded_bytes": 1023645,
                "other_http_bytes": 0,
                "reserved_bytes": 65536,
            }
        )
    )
    a = nh.NewHampshireAcquirer(store, tmp_path)
    try:
        assert a.downloaded == 1023645 + 65536 and a.delay >= 11
        assert a.budget["uncertain_reserved_bytes"] == 65536
        assert a.budget_path == path
        with pytest.raises(AcquisitionError, match="reviewed host"):
            a.fetch("https://example.test/")
        a._record(nh.HOME, nh.HOME, "2026-01-01T00:00:00Z", 403, {}, None, "Denied")
        with pytest.raises(AcquisitionError, match="Retained access denial"):
            a.fetch(nh.HOME)
    finally:
        a.close()


def test_resume_skips_retained_chapters_and_limit_counts_new_work(store):
    pages = {
        nh.BASE + "/robots.txt": b"User-agent: *\nAllow: /",
        nh.HOME: ("<html><body>" + nh.CURRENCY + "</body></html>").encode(),
        nh.TOC: b'<a href="NHTOC/NHTOC-I.htm">TITLE I: GOVERNMENT</a>',
        nh.BASE
        + "/rsa/html/NHTOC/NHTOC-I.htm": b'<a href="NHTOC-I-1.htm">CHAPTER 1: First</a><a href="NHTOC-I-2.htm">CHAPTER 2: Second</a>',
        nh.BASE + "/rsa/html/I/1/1-mrg.htm": chapter(),
        nh.BASE + "/rsa/html/I/2/2-mrg.htm": chapter("2"),
    }
    requests = []

    def response(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=pages[str(request.url)])

    a = Acquirer(store, delay=0)
    a.client.close()
    a.client = httpx.Client(transport=httpx.MockTransport(response))
    a._pause = Mock()
    try:
        assert nh.sync_new_hampshire(store, a, 1, None)["accepted"] == 1
        assert nh.sync_new_hampshire(store, a, 1, None)["accepted"] == 1
        assert nh.sync_new_hampshire(store, a, None, None)["attempted"] == 0
        assert len(requests) == len(pages)
        assert (
            store.db.execute("SELECT status FROM inventories WHERE item='title:I'").fetchone()[0]
            == "complete"
        )
        assert not store.db.execute(
            "SELECT 1 FROM versions WHERE snapshot_date IS NOT NULL OR effective_on IS NOT NULL"
        ).fetchall()
    finally:
        a.close()
