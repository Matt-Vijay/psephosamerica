"""Native inventory, page/source fidelity, labeled OCR, and bounded resumability."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from conftest import retain

from psephos import georgia_rules as ga
from psephos.acquire import AcquisitionError
from psephos.retrieve import Reader
from psephos.store import Provision, digest, json_text


def test_native_department_inventory():
    row = b'<a href="?st=GASOS&amp;year=2026&amp;dept=Departments&amp;pdf=Department 40 Agriculture">Department 40. Agriculture</a>'
    item = ga.inventory(row)[0]
    assert item["department"] == "40" and "Department%2040" in item["url"]
    with pytest.raises(ValueError, match="duplicate"):
        ga.inventory(row + row)
    with pytest.raises(ValueError, match="disagreement"):
        ga.inventory(row.replace(b"pdf=Department 40", b"pdf=Department 41"))
    with pytest.raises(ValueError, match="disagreement"):
        ga.inventory(row.replace(b'href="?', b'href="https://example.test/?'))


def test_page_projection_preserves_native_text_and_labels_image_ocr(store, monkeypatch):
    source = retain(store, b"%PDF-fixture")
    path = store.object_path(source.sha256)
    cover = "Department 40 Agriculture\nCurrent through Rules and Regulations filed through August 14, 2026\n"
    native = "Rule 40-1-1-.01. Except as provided below.\nA     B\n1     2\nAuthority: O.C.G.A.\n"
    reader = SimpleNamespace(pages=[{}, {}, {}], metadata={"/ModDate": "not a legal date"})
    monkeypatch.setattr(ga, "PdfReader", lambda p: reader)
    monkeypatch.setattr(ga, "layout_pages", lambda p: [cover, native, ""])
    monkeypatch.setattr(
        ga,
        "outlines",
        lambda r: [
            {"title": "Rule 40-1-1-.01. Definitions.", "page": 2, "parents": ["Chapter 40-1"]}
        ],
    )
    monkeypatch.setattr(ga, "page_details", lambda p, r: (["/Image1"], []))
    monkeypatch.setattr(
        ga,
        "ocr_page",
        lambda *args: (
            "Machine-recognized table, verify image.",
            "<div>OCR table</div>",
            {"source_sha256": source.sha256},
        ),
    )
    item = {"department": "40", "title": "Agriculture", "url": source.url}
    units, snapshot, metadata = ga.pdf_projection(path, item, store.root / "ocr")
    assert len(units) == 3 and snapshot == "2026-08-14"
    assert units[1].text.startswith(native)
    assert units[1].metadata["rule_identifiers_in_text"] == ["40-1-1-.01"]
    assert units[2].metadata["source_text_characters"] == 0
    assert units[2].metadata["text_quality"] == "machine_ocr_unverified"
    assert metadata["machine_ocr_pages"] == [3]
    assert [u.metadata["physical_page"] for u in units] == [1, 2, 3]
    args = dict(
        collection="test",
        document="ga-fixture",
        title="Agriculture",
        url=source.url,
        acquisition=source.id,
        snapshot_date=snapshot,
        snapshot_basis=ga.CLOCK_NOTE,
        parser=ga.PARSER,
        provisions=units,
        metadata=metadata,
    )
    version, count, added = store.ingest(**args)
    assert added and count == 3 and store.ingest(**args) == (version, 3, False)
    result = Reader(store).read(units[2].key)
    assert result["media"][0]["acquired_receipt"]["sha256"] == source.sha256
    assert result["text_completeness"] == "incomplete_without_source_media"
    assert result["effective_on"] is None and result["published_on"] is None
    assert not Reader(store).read(units[2].key, as_of="2026-08-13")["found"]
    assert not Reader(store).read(units[2].key, observation_cutoff="2025-12-31T23:59:59Z")["found"]
    monkeypatch.setattr(ga, "ocr_page", lambda *args: ("Figure A", "<div>Figure A</div>", {}))
    with pytest.raises(ValueError, match="manual visual"):
        ga.pdf_projection(path, item, store.root / "ocr")
    media, _, meta = ga.pdf_projection(
        path,
        item,
        store.root / "ocr",
        {f"{path.name}:3": {"caption": "Figure A", "limitation": "Diagram not transcribed"}},
    )
    assert media[2].unit_kind == "pdf_media_page"
    assert media[2].metadata["text_quality"] == "source_media_only"
    assert meta["media_only_pages"] == [3] and meta["machine_ocr_pages"] == []
    monkeypatch.setattr(ga, "page_details", lambda p, r: ([], []))
    with pytest.raises(ValueError, match="review required"):
        ga.pdf_projection(path, item, store.root / "ocr")


def driver():
    spec = importlib.util.spec_from_file_location(
        "ga_completion_test", Path("scripts/complete_georgia.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_page_read_bounds_department_outline_without_mutating_versions(store):
    receipt = retain(store, b"%PDF-original")
    outlines = [
        {"title": f"Rule {n}. Exact heading", "parents": ["Chapter 1"], "page": n + 1}
        for n in range(1700)
    ]
    units = [
        Provision(
            f"page-{n}",
            f"Page {n}",
            f"Page {n}",
            "Original page text",
            "",
            receipt.url,
            metadata={"physical_page": n, "publisher_outlines": [outlines[n - 1]]},
        )
        for n in (40, 41, 42)
    ]
    version, _, _ = store.ingest(
        collection="test",
        document="department",
        title="Department",
        url=receipt.url,
        acquisition=receipt.id,
        parser="ga-department-pdf-pages/3",
        snapshot_date="2026-08-21",
        snapshot_basis="Cover filing-through date",
        metadata={
            "publisher_outlines": outlines,
            "image_pages": list(range(1000)),
            "page_count": 1700,
            "fidelity": "Original PDF controls",
        },
        provisions=units,
    )
    original = store.db.execute("SELECT metadata FROM versions WHERE id=?", (version,)).fetchone()[
        0
    ]
    result = Reader(store).read("page-41", length=1000)
    assert len(json.dumps(result).encode()) < 10000
    assert result["metadata"]["preceding_publisher_bookmark"] == outlines[39]
    assert result["metadata"]["publisher_outlines"] == [outlines[40]]
    assert result["version_metadata"]["omitted_list_counts"] == {
        "publisher_outlines": 1700,
        "image_pages": 1000,
    }
    assert result["version_metadata"]["fidelity"] == "Original PDF controls"
    assert result["version_metadata"]["page_count"] == 1700
    assert [r["key"] for r in result["neighbors"]] == ["page-40", "page-42"]
    assert result["artifact_sha"] == receipt.sha256 and result["effective_on"] is None
    assert not Reader(store).read("page-41", as_of="2026-08-20")["found"]
    assert (
        store.db.execute("SELECT metadata FROM versions WHERE id=?", (version,)).fetchone()[0]
        == original
    )


def test_acquisition_lifetime_cap_failed_bytes_and_cache_resume(store, tmp_path, monkeypatch):
    module = driver()
    monkeypatch.setattr(module, "CAP", 256 * 1024)
    monkeypatch.setattr(module.GeorgiaAcquirer, "_pause", lambda *args: None)
    directory = tmp_path / "campaign"
    directory.mkdir()
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, stream=httpx.ByteStream(b"x" * 70000))

    def client():
        a = module.GeorgiaAcquirer(store, directory, 123)
        a.client.close()
        a.client = httpx.Client(
            transport=httpx.MockTransport(handler), event_hooks={"response": [a._bounded_response]}
        )
        return a

    a = client()
    try:
        r = a.fetch(ga.INDEX + "?fixture", check_robots=False)
        assert a.downloaded == 70123
        with pytest.raises(AcquisitionError, match="cap"):
            a.fetch(ga.INDEX + "?failed", check_robots=False, max_file_bytes=10)
        failed_spent = a.downloaded
        assert failed_spent > 70123
    finally:
        a.close()
    a = client()
    try:
        assert a.downloaded == failed_spent
        assert a.fetch(r.url, check_robots=False) == r and len(calls) == 2
        with pytest.raises(AcquisitionError, match="reviewed Georgia"):
            a._allowed("https://rules.sos.ga.gov/gac/40-1-1-.01")
        assert (
            json.loads((directory / "budget.json").read_bytes())["consumed_bytes"] == failed_spent
        )
    finally:
        a.close()


def test_annotation_values_do_not_embed_reader_identity():
    reader = SimpleNamespace(get_page_number=lambda p: 4)
    indirect = SimpleNamespace(idnum=9, get_object=lambda: {"/Type": "/Page"})
    result = ga.pdf_value([indirect, "/XYZ", 0, 42, None], reader)
    assert result == [{"physical_page": 5}, "/XYZ", 0, 42, None]
    assert "0x" not in json_text(result)


def test_legacy_media_is_bound_to_its_exact_pdf_and_ocr_search_warns(store):
    old = retain(store, b"old PDF")
    newer = retain(store, b"new PDF")
    with store.db:
        store.db.execute("UPDATE acquisitions SET url=? WHERE id=?", (old.url, newer.id))
    version, _, _ = store.ingest(
        collection="test",
        document="old",
        title="Old",
        url=old.url,
        acquisition=old.id,
        snapshot_basis="Unknown",
        parser="ga-department-pdf-pages/1",
        provisions=[
            Provision(
                "image",
                "Image",
                "Image",
                "image navigation",
                "",
                old.url,
                metadata={
                    "image_xobjects": ["/I1"],
                    "physical_page": 3,
                    "text_quality": "machine_ocr_unverified",
                },
            )
        ],
    )
    original = store.db.execute("SELECT metadata FROM versions WHERE id=?", (version,)).fetchone()[
        0
    ]
    result = Reader(store).read("image")
    assert result["media"][0]["acquired_receipt"]["sha256"] == old.sha256
    assert result["text_completeness"] == "incomplete_without_source_media"
    assert (
        Reader(store).search("navigation")["matches"][0]["text_quality"] == "machine_ocr_unverified"
    )
    assert (
        store.db.execute("SELECT metadata FROM versions WHERE id=?", (version,)).fetchone()[0]
        == original
    )


def test_abandoned_byte_reservation_remains_charged(store, tmp_path):
    module = driver()
    directory = tmp_path / "interrupted"
    directory.mkdir()
    module.save(
        directory / "budget.json",
        {
            "consumed_bytes": 5,
            "reserved_bytes": 65536,
            "last_request_unix": 0,
            "cap_bytes": module.CAP,
        },
    )
    a = module.GeorgiaAcquirer(store, directory, 0)
    try:
        assert a.downloaded == 65541 and a.budget["uncertain_reserved_bytes"] == 65536
    finally:
        a.close()


def test_ocr_canonical_receipt_is_cache_path_independent(tmp_path):
    source = tmp_path / ("a" * 64)
    results = []
    raw_hocr_hashes = []
    for name in ("one", "two"):
        cache = tmp_path / name
        directory = cache / source.name
        directory.mkdir(parents=True)
        stem = directory / "1"
        stem.with_suffix(".png").write_bytes(b"same rendered source image")
        stem.with_suffix(".txt").write_text("Same OCR text")
        stem.with_suffix(".hocr").write_text(
            f"<html><body><div title='image \"{stem}.png\"; bbox 0 0 100 100'>Same OCR text</div></body></html>"
        )
        hashes = {
            suffix: digest(stem.with_suffix(suffix).read_bytes())
            for suffix in (".png", ".txt", ".hocr")
        }
        receipt = {
            "source_sha256": source.name,
            "physical_page": 1,
            "projection": "machine OCR, unverified; source image controls",
            "commands": [],
            "tesseract": "fixture",
            "poppler": "fixture",
            "derived_sha256": hashes,
        }
        stem.with_suffix(".json").write_text(json_text(receipt))
        results.append(ga.ocr_page(source, 1, cache))
        raw_hocr_hashes.append(hashes[".hocr"])
    assert raw_hocr_hashes[0] != raw_hocr_hashes[1]
    assert results[0] == results[1]
    assert str(tmp_path) not in json_text(results)
