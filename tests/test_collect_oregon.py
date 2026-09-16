from pathlib import Path

import pytest
from conftest import retain
from lxml import html

from psephos import collect_oregon
from psephos.collect_oregon import CHAPTERS, PRIORITY, chapter_units, chapter_url
from psephos.parse import readable
from psephos.store import Provision, Store


def test_completing_gaps_does_not_reindex_accepted_projection(store, monkeypatch, capsys):
    monkeypatch.setattr(collect_oregon, "CHAPTERS", ("001", "002"))
    monkeypatch.setattr(collect_oregon, "PRIORITY", {"001"})
    store.collection(
        "oregon-ors",
        ("us-or", "Oregon", "state", "us"),
        name="ORS",
        authority="Fixture",
        kind="code",
        homepage=collect_oregon.INDEX,
        source_status="Fixture",
        access="Fixture",
    )
    source = retain(store, b"accepted old source")
    original = store.ingest(
        collection="oregon-ors",
        document="ors:chapter:1",
        title="Chapter 1",
        url=chapter_url("001"),
        acquisition=source.id,
        snapshot_basis="Unknown",
        parser="old-accepted",
        provisions=[
            Provision(
                "ors:1.001", "ORS 1.001", "Old", "Old accepted projection", "", chapter_url("001")
            )
        ],
    )
    store.inventory("oregon-ors", "001", chapter_url("001"), "indexed")
    preflight = retain(
        store,
        b'<html><p>2025 Edition does not include later changes</p><table><tbody groupstring="Volume"><tr><td>Volume 1 (2)</td></tr></tbody></table></html>',
    )
    body = retain(
        store,
        b'<div class="WordSection1"><p>Chapter 2 - Courts</p><p><b>2.001 New section.</b> Body.</p></div>',
    )
    fetched = []

    class CachedPreflightOnly:
        delay = 2.1
        refresh = False

        def fetch(self, url, **kwargs):
            if url == chapter_url("002"):
                fetched.append(url)
                return body
            assert url in {collect_oregon.INDEX, collect_oregon.DISCLAIMER, collect_oregon.UPDATE}
            return preflight

    collect_oregon.sync_oregon(store, CachedPreflightOnly(), limit=1)
    assert fetched == [chapter_url("002")]
    assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 2
    assert (
        store.db.execute(
            "SELECT text FROM provisions WHERE version_id=?", (original[0],)
        ).fetchone()[0]
        == "Old accepted projection"
    )
    output = capsys.readouterr()
    assert output.out == "" and "oregon-ors 002" in output.err

    class Denied(CachedPreflightOnly):
        refresh = True

        def fetch(self, url, **kwargs):
            if url == chapter_url("001"):
                raise collect_oregon.AcquisitionError("Publisher refusal")
            return super().fetch(url, **kwargs)

    with pytest.raises(collect_oregon.AcquisitionError, match="Publisher refusal"):
        collect_oregon.sync_oregon(store, Denied(), limit=1)
    assert store.db.execute("SELECT count(*) FROM versions").fetchone()[0] == 2


def test_inventory_is_actual_fixed_689_link_seed():
    assert len(CHAPTERS) == len(set(CHAPTERS)) == 689
    assert len(PRIORITY) == 66
    assert PRIORITY <= set(CHAPTERS)
    assert chapter_url("197A").endswith("/ors197A.html")


def test_preserves_notes_repealed_duplicates_tables_and_media():
    source = b"""<html><div class="WordSection1">
    <p>Chapter 197 - Comprehensive Land Use Planning</p>
    <p>2025 EDITION</p><p>2026 amendment warning <a href="changes.pdf">changes</a></p>
    <p>197.005 Legislative findings</p>
    <p><b>197.005 Legislative findings.</b> Actual text.</p>
    <p>Note: Temporary law. Sec. 2. More legal text.</p>
    <table><tr><td>A</td><td>B</td></tr></table><p><img src="map.png" alt="map"/></p>
    <p>197.010 [Renumbered 197.011]</p>
    <p><b>197.015 Definitions.</b> First version [2025 c.1 s.2]</p>
    <p><b>197.015 Definitions.</b> Future version</p>
    </div></html>"""
    title, units, metadata = chapter_units(source, "197", chapter_url("197"))
    assert "Comprehensive Land Use Planning" in title
    assert metadata["page_edition_labels"] == ["2025"]
    assert [u.key for u in units] == [
        "ors:197:front-matter",
        "ors:197.005",
        "ors:197.010",
        "ors:197.015:occurrence-1",
        "ors:197.015:occurrence-2",
    ]
    assert "2026 amendment warning" in units[0].text
    assert units[0].references[0].target.endswith("/changes.pdf")
    assert "Sec. 2. More legal text." in units[1].text
    assert "<table>" in units[1].markup
    assert units[1].metadata["tables"] == 1
    assert units[1].metadata["media"][0]["url"].endswith("/map.png")
    assert "Renumbered" in units[2].text
    assert "2025 c.1 s.2" in units[3].text
    assert units[3].metadata["duplicate_identifier_count"] == 2


def test_wrong_chapter_fails_closed():
    with pytest.raises(ValueError, match="identity"):
        chapter_units(
            b'<div class="WordSection1"><p>Chapter 1 - Courts</p></div>',
            "197",
            "https://example.test",
        )


def test_real_197_fidelity_when_payload_present():
    root = Path("data/collectors/oregon")
    if not (root / "legal.sqlite3").exists():
        pytest.skip("optional collected-payload fidelity check")
    store = Store(root, readonly=True)
    try:
        row = store.db.execute(
            "SELECT sha256 FROM acquisitions WHERE url=? AND status=200 ORDER BY id DESC LIMIT 1",
            (chapter_url("197"),),
        ).fetchone()
        if not row:
            pytest.skip("ORS197 not yet collected")
        data = store.artifact(row[0])
        _, units, meta = chapter_units(data, "197", chapter_url("197"))
        page = html.fromstring(data)
        original = " ".join(
            " ".join(
                readable(n) for n in page.xpath('//div[starts-with(@class,"WordSection")]')
            ).split()
        )
        projected = " ".join(" ".join(u.text for u in units).split())
        assert projected == original
        assert "2026" in units[0].text
        assert any("2026orLaw0089.pdf" in r.target for r in units[0].references)
        assert any("wildfires" in u.text.lower() and u.key.startswith("ors:197.022") for u in units)
        assert any("renumbered" in u.text.lower() for u in units)
        assert meta["sections"] > 100
        assert "\ufffd" not in projected
    finally:
        store.close()
