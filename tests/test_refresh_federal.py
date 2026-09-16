"""Federal refresh regressions against a temporary real store and offline HTTP."""

import io
import json
import zipfile

import httpx
import pytest

from psephos import sources
from psephos.acquire import Acquirer
from psephos.store import digest, utc_now

USC_ALL = "https://uscode.house.gov/download/xml_uscAll@119-42.zip"
USC_INDEX = b"""<html><p>Current Release Point Public Law 119-42 (09/15/2026)</p>
<a href="/download/xml_uscAll@119-42.zip">All titles</a>
<a href="/download/xml_usc01@119-42.zip">Title 1</a>
<div class="uscitem"><div class="usctitle">Title 53 [Reserved]</div>
<a href="/download/xml_usc53@119-42.zip">XML</a></div></html>"""
USC_ITEMS = {"inventory_items": {"uscode": ["usc01", "usc53"]}}
ECFR_XML = b"""<DIV1 TYPE="TITLE" N="1"><DIV5 TYPE="PART" N="1">
<DIV8 TYPE="SECTION" N="1.1"><HEAD>Fixture rule</HEAD><P>Publisher body.</P>
</DIV8></DIV5></DIV1>"""


def usc_zip(body):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "usc01.xml",
            '<uscDoc identifier="/us/usc/t1"><meta><docNumber>1</docNumber>'
            '<title>General Provisions</title></meta><section identifier="/us/usc/t1/s1">'
            f'<num value="1">1</num><heading>Fixture</heading><content>{body}</content>'
            "</section></uscDoc>",
        )
    return buffer.getvalue()


@pytest.fixture
def publisher(store):
    payloads = {sources.USC_INDEX: USC_INDEX, USC_ALL: usc_zip("Original body.")}
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        body = payloads[str(request.url)]  # Unexpected/reserved downloads fail the test.
        etag = '"' + digest(body) + '"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"ETag": etag})
        return httpx.Response(200, content=body, headers={"ETag": etag})

    acquirer = Acquirer(store, refresh=True, delay=0)
    acquirer.client.close()
    acquirer.client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
    try:
        yield acquirer, payloads, requests
    finally:
        acquirer.close()


def test_uscode_same_release_changed_payload_creates_version(store, publisher):
    acquirer, payloads, requests = publisher
    assert sources.sync_uscode(store, acquirer, None, None) == USC_ITEMS
    payloads[USC_ALL] = usc_zip("Corrected body.")
    assert sources.sync_uscode(store, acquirer, None, None) == USC_ITEMS
    downloads = [r for r in requests if str(r.url) == USC_ALL]
    assert len(downloads) == 2 and downloads[1].headers.get("if-none-match")
    rows = store.db.execute(
        "SELECT artifact_sha,snapshot_date,metadata FROM versions "
        "WHERE document_id='usc:/us/usc/t1' ORDER BY rowid"
    ).fetchall()
    assert len(rows) == 2 and rows[0]["artifact_sha"] != rows[1]["artifact_sha"]
    assert {r["snapshot_date"] for r in rows} == {"2026-09-15"}
    assert {json.loads(r["metadata"])["release_point"] for r in rows} == {"119-42"}
    texts = [r[0] for r in store.db.execute("SELECT text FROM provisions ORDER BY rowid")]
    assert "Original body." in texts[0] and "Corrected body." in texts[1]


def test_uscode_unchanged_artifact_skips_xml_and_keeps_reserved_inventory(
    store, publisher, monkeypatch
):
    acquirer, _, requests = publisher
    sources.sync_uscode(store, acquirer, None, None)
    monkeypatch.setattr(sources, "xml_root", lambda *_: pytest.fail("Reparsed accepted XML"))
    assert sources.sync_uscode(store, acquirer, None, None) == USC_ITEMS
    assert sum(str(r.url) == USC_ALL for r in requests) == 2
    assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 1
    assert dict(
        store.db.execute("SELECT item,status FROM inventories WHERE collection_id='uscode'")
    ) == {"usc01": "indexed", "usc53": "reserved"}
    assert (
        store.db.execute(
            "SELECT status FROM acquisitions WHERE url=? ORDER BY id DESC LIMIT 1", (USC_ALL,)
        ).fetchone()[0]
        == 304
    )


def test_ecfr_reserved_and_date_rollover_preserve_exact_current_inventory(
    store, publisher, monkeypatch
):
    acquirer, payloads, requests = publisher
    dates = ("2026-09-14", "2026-09-15")
    index = {
        "meta": {"import_in_progress": False},
        "titles": [
            {"number": 1, "name": "Fixture", "up_to_date_as_of": dates[0]},
            {"number": 35, "name": "Reserved", "reserved": True, "up_to_date_as_of": None},
        ],
    }
    for day in dates:
        index["titles"][0]["up_to_date_as_of"] = day
        payloads[sources.ECFR_INDEX] = json.dumps(index).encode()
        url = f"https://www.ecfr.gov/api/versioner/v1/full/{day}/title-1.xml"
        payloads[url] = ECFR_XML
        expected = {"inventory_items": {"ecfr": ["1@" + day, "35@reserved"]}}
        assert sources.sync_ecfr(store, acquirer, None, None) == expected
        with monkeypatch.context() as patch:
            patch.setattr(sources, "xml_root", lambda *_: pytest.fail("Reparsed accepted XML"))
            assert sources.sync_ecfr(store, acquirer, None, None) == expected
        assert sum(str(r.url) == url for r in requests) == 2
    rows = store.db.execute(
        "SELECT snapshot_date,artifact_sha FROM versions WHERE document_id='ecfr:title-1' "
        "ORDER BY snapshot_date"
    ).fetchall()
    assert [r["snapshot_date"] for r in rows] == list(dates)
    assert len({r["artifact_sha"] for r in rows}) == 1
    assert dict(
        store.db.execute("SELECT item,status FROM inventories WHERE collection_id='ecfr'")
    ) == {"1@2026-09-14": "indexed", "1@2026-09-15": "indexed", "35@reserved": "reserved"}


def test_ecfr_cycle_resume_reuses_bodies_but_still_checks_inventory(store, publisher):
    acquirer, payloads, requests = publisher
    acquirer.resume_after = utc_now()
    day = "2026-09-15"
    payloads[sources.ECFR_INDEX] = json.dumps(
        {
            "meta": {},
            "titles": [{"number": 1, "name": "Fixture", "up_to_date_as_of": day}],
        }
    ).encode()
    url = f"https://www.ecfr.gov/api/versioner/v1/full/{day}/title-1.xml"
    payloads[url] = ECFR_XML
    sources.sync_ecfr(store, acquirer, None, None)
    sources.sync_ecfr(store, acquirer, None, None)
    assert sum(str(r.url) == url for r in requests) == 1
    assert sum(str(r.url) == sources.ECFR_INDEX for r in requests) == 2
    acquirer.resume_after = None
    sources.sync_ecfr(store, acquirer, None, None)
    assert sum(str(r.url) == url for r in requests) == 2
