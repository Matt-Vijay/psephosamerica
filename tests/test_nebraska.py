"""Literal native membership, parser compatibility and bounded resumable sync."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from psephos.acquire import Acquirer, AcquisitionError
from psephos.campaign import save
from psephos.nebraska import (
    BASE,
    CAP,
    FAMILIES,
    LANDING,
    NebraskaAcquirer,
    ancillary_units,
    chapter_index,
    chapter_units,
    native_inventory,
    sync_nebraska,
)
from psephos.store import TEXT_PROJECTION, Store, stable_id


def native_row(path, parameter, identifier, *, chapter=False):
    path = "/laws/" + path
    printer = "/laws/display-chapters.php" if chapter else path
    suffix = "" if chapter else "&print=true"
    return (
        f'<tr><td class="row"><span><a href="{path}?{parameter}={identifier}">{identifier}</a>'
        f'</span><span>Publisher label</span><span><a href="{printer}?{parameter}={identifier}'
        f'{suffix}">Print</a></span></td></tr>'
    )


def section_index(*identifiers):
    return (
        "<table>"
        + "".join(native_row("statutes.php", "statute", n) for n in identifiers)
        + "</table>"
    ).encode()


def test_chapters_preserve_occurrences_future_repeal_bodies_tables_and_links():
    index = chapter_index(section_index("51-101", "51-102", "51-102"), "51", BASE)
    data = b"""<html><head><title>Navigation</title></head><body>
    <div class="printwidth"><strong>51-101. Repealed. Laws 2020.</strong></div>
    <div class="printwidth"><strong>51-102. Repealed, effective January 1, 2027.</strong>
    <p>Supplied legal body remains intact.</p><table><tr><td rowspan="2">A</td><td>B</td></tr></table>
    <a href="/laws/image.png">Native diagram</a></div>
    <div class="printwidth"><strong>51-102. Future counterpart.</strong><p>Second body.</p></div>
    <p>Publisher context.</p><script>Not a legal unit.</script></body></html>"""
    units = chapter_units(data, "51", BASE, index)
    assert [unit.key for unit in units] == [
        "ne:statute:51-101",
        "ne:statute:51-102/_occurrence/1",
        "ne:statute:51-102/_occurrence/2",
        "ne:chapter:51/_context",
    ]
    assert units[0].unit_kind == "editorial_notice" and units[1].unit_kind == "section"
    assert units[1].url == BASE + "/laws/statutes.php?statute=51-102"
    assert 'rowspan="2"' in units[1].markup and "January 1, 2027" in units[1].text
    assert units[1].metadata["media"][0]["url"] == BASE + "/laws/image.png"
    assert units[-1].text == "Publisher context."
    with pytest.raises(ValueError, match="order/occurrences"):
        chapter_units(data, "51", BASE, tuple(reversed(index)))
    with pytest.raises(ValueError, match="another chapter"):
        chapter_index(section_index("52-101"), "51", BASE)


def test_native_inventories_require_exact_paired_publisher_links():
    row = native_row("browse-chapters.php", "chapter", "51", chapter=True)
    data = ("<table>" + row + "</table>").encode()
    inventory = native_inventory(data, "chapter", BASE)
    assert inventory.documents() == (("51", BASE + "/laws/display-chapters.php?chapter=51"),)
    for changed, message in [
        (
            data.replace(b"display-chapters.php?chapter=51", b"display-chapters.php?chapter=52"),
            "pairs",
        ),
        (("<table>" + row + row + "</table>").encode(), "Duplicate"),
        (
            data.replace(b'href="/laws/display-', b'href="https://mirror.invalid/laws/display-'),
            "official HTTPS",
        ),
    ]:
        with pytest.raises((ValueError, AcquisitionError), match=message):
            native_inventory(changed, "chapter", BASE)


def test_ancillary_dispositions_context_and_native_order():
    rows = "".join(native_row("articles.php", "article", number) for number in ("Preamble", "I-24"))
    data = (
        '<a href="/laws/display-fullconst.php?print=false">Full</a><table>' + rows + "</table>"
    ).encode()
    inventory = native_inventory(data, "constitution", BASE)
    body = b"""<body><div class="main-content"><h1>Nebraska Constitution</h1>
      <div class="statute"><h2>Preamble.</h2><h3>Preamble</h3><p>We the people.</p></div>
      <div class="statute"><h2>I-24.</h2><h3>Repealed.</h3><p> </p></div>
      </div><footer>Not in legal region.</footer></body>"""
    units = ancillary_units(body, "constitution", BASE, inventory)
    assert [unit.unit_kind for unit in units] == ["preamble", "editorial_notice", "scope_notes"]
    assert [unit.key for unit in units] == [
        "ne:constitution:Preamble",
        "ne:constitution:I-24",
        "ne:constitution:full/_context",
    ]
    assert units[-1].text == "Nebraska Constitution"
    with pytest.raises(ValueError, match="native identifier order"):
        ancillary_units(
            body,
            "constitution",
            BASE,
            type(inventory)(tuple(reversed(inventory.entries)), inventory.full_export),
        )
    with pytest.raises(ValueError, match="absent"):
        ancillary_units(body.replace(b"I-24.", b"I-25."), "constitution", BASE, inventory)


def sources():
    payloads = {
        "/robots.txt": b"User-agent: *\nAllow: /\n",
        "/contact/disclaimer.php": b"Publisher disclaimer, no warranty.",
        "/contact/privacy.php": b"Publisher privacy statement.",
    }
    payloads["/laws/laws.php"] = "".join(
        f'<a href="/laws/{spec.browse}">{name}</a>' for name, spec in FAMILIES.items()
    ).encode()
    for family, spec in FAMILIES.items():
        ids = (
            ("51", "52")
            if family == "chapter"
            else ("I-1",)
            if family == "constitution"
            else ("1-101",)
        )
        rows = "".join(
            native_row(spec.entry, spec.parameter, n, chapter=family == "chapter") for n in ids
        )
        full = (
            f'<a href="/laws/{spec.full_export}?print=false">Full</a>' if spec.full_export else ""
        )
        payloads["/laws/" + spec.browse] = (full + "<table>" + rows + "</table>").encode()
    for chapter in ("51", "52"):
        payloads["/laws/browse-chapters.php?chapter=" + chapter] = section_index(chapter + "-101")
        payloads["/laws/display-chapters.php?chapter=" + chapter] = (
            f'<html><body><div class="printwidth"><strong>{chapter}-101. Heading.</strong><p>Law {chapter}.</p></div></body></html>'.encode()
        )
    payloads["/laws/display-fullucc.php?print=false"] = (
        b'<div class="main-content"><div class="printwidth"><strong>1-101. UCC heading.</strong><p>UCC body.</p></div></div>'
    )
    payloads["/laws/display-fullconst.php?print=false"] = (
        b'<div class="main-content"><div class="statute"><h2>I-1.</h2><h3>Rights.</h3><p>Constitutional body.</p></div></div>'
    )
    payloads["/laws/appendix.php?section=1-101&print=true"] = (
        b"<html><body><strong>1-101. State flower.</strong><p>Appendix body.</p></body></html>"
    )
    return payloads


def mocked_acquirer(store, monkeypatch, handler):
    # Disable wall-clock delay only. Keep source boundaries, receipts and charges live.
    monkeypatch.setattr(Acquirer, "_pause", lambda *args: None)
    acquirer = NebraskaAcquirer(store)
    acquirer.client.close()
    acquirer.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        event_hooks={"response": [acquirer._bounded_response]},
    )
    return acquirer


def test_sync_inventory_limit_resume_and_unchanged_versions(store, monkeypatch):
    payloads, requests = sources(), []
    ticks = iter(range(2000))
    monkeypatch.setattr(
        "psephos.acquire.utc_now",
        lambda: (
            (datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=next(ticks)))
            .isoformat()
            .replace("+00:00", "Z")
        ),
    )

    def handler(request):
        route = str(request.url).removeprefix(BASE)
        requests.append(route)
        assert request.url.host == "nebraskalegislature.gov" and route in payloads
        return httpx.Response(200, stream=httpx.ByteStream(payloads[route]))

    acquirer = mocked_acquirer(store, monkeypatch, handler)
    try:
        with pytest.raises(ValueError, match="historical"):
            sync_nebraska(store, acquirer, None, "2020-01-01")
        assert not requests
        sync_nebraska(store, acquirer, 1, None)
        assert dict(
            store.db.execute("SELECT status,count(*) FROM inventories GROUP BY status")
        ) == {"acquired": 1, "pending": 4}
        first = dict(store.db.execute("SELECT * FROM versions").fetchone())
        source_clock = store.db.execute(
            "SELECT observed_at FROM acquisitions WHERE id=?", (first["acquisition_id"],)
        ).fetchone()[0]
        assert first["available_at"] > source_clock  # Native chapter roster was acquired later.
        sync_nebraska(store, acquirer, 1, None)
        sync_nebraska(store, acquirer, None, None)
        assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 5
        assert (
            store.db.execute("SELECT count(*) FROM inventories WHERE status='pending'").fetchone()[
                0
            ]
            == 0
        )
        before = [dict(row) for row in store.db.execute("SELECT * FROM versions ORDER BY id")]
        sync_nebraska(store, acquirer, None, None)
        assert before == [
            dict(row) for row in store.db.execute("SELECT * FROM versions ORDER BY id")
        ]
        assert all(
            all(
                v[name] is None
                for name in (
                    "snapshot_date",
                    "published_on",
                    "effective_on",
                    "amended_on",
                    "repealed_on",
                )
            )
            for v in before
        )
        assert all(
            requests.count(route) == 1
            for route in payloads
            if "display-" in route or "&print=true" in route
        )
        route = "/laws/display-chapters.php?chapter=51"
        payloads[route] = payloads[route].replace(b"Law 51.", b"Updated law 51.")

        def interrupted(*args):
            raise ValueError("Fixture parser interruption after source retention")

        monkeypatch.setattr("psephos.nebraska.chapter_units", interrupted)
        acquirer.refresh = True
        with pytest.raises(ValueError, match="Fixture parser interruption"):
            sync_nebraska(store, acquirer, 1, None)
        body_requests = requests.count(route)
        monkeypatch.setattr("psephos.nebraska.chapter_units", chapter_units)
        acquirer.refresh = False
        sync_nebraska(store, acquirer, None, None)
        assert requests.count(route) == body_requests  # New cached bytes, no body redownload.
        assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 6
        for original in before:
            assert (
                dict(
                    store.db.execute(
                        "SELECT * FROM versions WHERE id=?", (original["id"],)
                    ).fetchone()
                )
                == original
            )
    finally:
        acquirer.close()


def test_campaign_requires_original_budget_and_preserves_charges(store, tmp_path):
    with store.db:
        store.db.execute(
            "INSERT INTO acquisitions(url,final_url,observed_at,status,headers) VALUES (?,?,?,0,'{}')",
            (LANDING, LANDING, "2026-09-10T00:00:00Z"),
        )
    with pytest.raises(AcquisitionError, match="original Nebraska campaign budget"):
        NebraskaAcquirer(store)
    directory = tmp_path / "migrated-campaign"
    directory.mkdir()
    save(
        directory / "budget.json",
        {
            "cap_bytes": CAP,
            "consumed_bytes": 67389824,
            "last_request_unix": 0,
            "request_overhead_bytes": 10485760,
            "reserved_bytes": 65536,
        },
    )
    acquirer = NebraskaAcquirer(store, directory)
    assert acquirer.downloaded == 67389824 + 65536
    acquirer.close()
    reopened = NebraskaAcquirer(store, directory)
    assert reopened.downloaded == 67389824 + 65536
    assert reopened.budget["uncertain_reserved_bytes"] == 65536
    assert reopened.budget["request_overhead_bytes"] == 10485760
    reopened.close()


def test_cross_host_redirect_and_denial_stop_before_another_request(store, monkeypatch):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://other.invalid/body"})

    acquirer = mocked_acquirer(store, monkeypatch, handler)
    try:
        with pytest.raises(AcquisitionError, match="official HTTPS"):
            acquirer.fetch(BASE + "/redirect", check_robots=False)
        assert requests == [BASE + "/redirect"]
        with store.db:
            store.db.execute(
                "INSERT INTO acquisitions(url,final_url,observed_at,status,headers) VALUES (?,?,?,403,'{}')",
                (LANDING, LANDING, "2026-09-10T00:00:00Z"),
            )
        with pytest.raises(AcquisitionError, match="Retained publisher/proxy denial"):
            acquirer.fetch(LANDING, check_robots=False)
        assert len(requests) == 1
    finally:
        acquirer.close()


def test_real_completed_store_replays_without_task_code_or_writes():
    path = os.environ.get("NEBRASKA_REPLAY_STORE")
    if not path:
        pytest.skip("Set NEBRASKA_REPLAY_STORE to the retained 66-document source store")
    store = Store(Path(path), readonly=True)
    try:
        assert store.db.execute("PRAGMA query_only").fetchone()[0] == 1

        def source(url):
            row = store.db.execute(
                "SELECT sha256 FROM acquisitions WHERE url=? AND status=200 ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()
            assert row, url
            return store.artifact(row[0])

        inventories = {
            name: native_inventory(
                source(BASE + "/laws/" + spec.browse), name, BASE + "/laws/" + spec.browse
            )
            for name, spec in FAMILIES.items()
        }
        versions = store.db.execute(
            "SELECT v.*,d.url FROM versions v JOIN documents d ON d.id=v.document_id ORDER BY v.document_id"
        ).fetchall()
        assert len(versions) == 66
        total = 0
        for version in versions:
            _, family, item = version["document_id"].split(":")
            inventory = inventories[family]
            data = store.artifact(version["artifact_sha"])
            if family == "chapter":
                entry = next(entry for entry in inventory.entries if entry.identifier == item)
                units = chapter_units(
                    data, item, version["url"], chapter_index(source(entry.url), item, entry.url)
                )
            else:
                units = ancillary_units(data, family, version["url"], inventory, item)
            parser = (
                ("ne-full-chapter/1" if family == "chapter" else f"ne-{family}-native/1")
                + "/"
                + TEXT_PROJECTION
            )
            assert version["parser"] == parser
            assert version["id"] == stable_id(
                version["document_id"], version["artifact_sha"], "", "", parser
            )
            rows = store.db.execute(
                "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (version["id"],)
            ).fetchall()
            assert len(rows) == len(units)
            for row, unit in zip(rows, units, strict=True):
                for key in (
                    "key",
                    "citation",
                    "heading",
                    "text",
                    "markup",
                    "url",
                    "parent_key",
                    "unit_kind",
                ):
                    assert row[key] == getattr(unit, key), (unit.key, key)
                assert json.loads(row["metadata"]) == unit.metadata
                references = {
                    tuple(r)
                    for r in store.db.execute(
                        "SELECT target,relation,label,evidence FROM legal_references WHERE provision_id=?",
                        (row["id"],),
                    )
                }
                assert references == {
                    (r.target, r.relation, r.label, r.evidence) for r in unit.references
                }
            total += len(units)
        assert total == 27642
    finally:
        store.close()
