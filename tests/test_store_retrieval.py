from dataclasses import replace

import pytest
from conftest import retain

from psephos.audit import audit
from psephos.retrieve import Reader
from psephos.store import Provision, Reference, Store


def ingest(store, text, *, day="2025-01-01", observed="2026-01-01T00:00:00Z", refs=()):
    source = retain(store, text.encode(), observed)
    return store.ingest(
        collection="test",
        document="document",
        title="Document",
        url=source.url,
        acquisition=source.id,
        snapshot_date=day,
        snapshot_basis="Fixture date",
        parser="test-1",
        provisions=[
            Provision("key", "Fixture § 1", "Words denoting", text, "", source.url, references=refs)
        ],
    )


def test_idempotence_exact_id_and_independent_cutoffs(store):
    version, _, new = ingest(store, "old content")
    reader = Reader(store)
    old = reader.read("key")
    assert new and old["text"] == "old content"
    assert ingest(store, "old content") == (version, 1, False)
    ingest(store, "new content", day="2025-06-01", observed="2026-02-01T00:00:00Z")
    assert reader.read("key")["text"] == "new content"
    assert reader.read(old["id"])["text"] == "old content"
    assert reader.coverage()["collections"][0]["units_in_latest_documents"] == [
        {"unit_kind": "section", "count": 1}
    ]
    assert reader.read("key", as_of="2025-03-01")["text"] == "old content"
    assert (
        reader.read("key", observation_cutoff="2026-01-15T00:00:00-05:00")["text"] == "old content"
    )
    assert not reader.read(old["id"], as_of="2024-01-01")["found"]
    assert not reader.read("key", observation_cutoff="2025-12-31T23:59:59Z")["found"]
    with pytest.raises(ValueError, match="timezone"):
        reader.read("key", observation_cutoff="2026-01-01")


def test_unknown_snapshot_is_not_invented(store):
    ingest(store, "undated", day=None)
    assert Reader(store).read("key")["found"]
    assert not Reader(store).read("key", as_of="2099-01-01")["found"]


def test_atomic_parse_failure_and_duplicate_key(store):
    ingest(store, "known good")
    source = retain(store, b"invalid next document")
    unit = Provision("duplicate", "Citation", "Heading", "Content", "", source.url)
    with pytest.raises(Exception, match="UNIQUE"):
        store.ingest(
            collection="test",
            document="bad",
            title="Bad",
            url=source.url,
            acquisition=source.id,
            snapshot_basis="unknown",
            parser="test",
            provisions=[unit, replace(unit, text="different")],
        )
    assert store.db.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    assert not Reader(store).search("different")["matches"]
    assert Reader(store).read("key")["text"] == "known good"


def test_references_cannot_bypass_source_cutoff(store):
    ingest(store, "Content", refs=(Reference("key", "publisher_citation", "label", "<ref/>"),))
    r = Reader(store)
    source = r.read("key")
    assert r.references(source["id"])["references"][0]["acquired_targets"]
    assert not r.references(source["id"], as_of="2024-01-01")["references"]
    assert not r.references(source["id"], observation_cutoff="2025-01-01T00:00:00Z")["references"]


def test_bounded_literal_search_and_readonly_connection(store):
    ingest(store, "A definition is not an instruction.")
    r = Reader(store)
    assert r.search("definition")["matches"]
    assert not r.search('" OR 1=1; DROP TABLE provisions --')["matches"]
    with pytest.raises(ValueError):
        r.search("word", limit=1000)
    with pytest.raises(ValueError):
        store.object_path("../secret")
    readonly = Store(store.root, readonly=True)
    try:
        with pytest.raises(Exception, match="readonly"):
            readonly.db.execute("DELETE FROM documents")
    finally:
        readonly.close()


def test_audit_detects_corrupt_retained_bytes(store):
    ingest(store, "known content")
    assert audit(store)["status"] == "PASS"
    sha = store.db.execute("SELECT sha256 FROM artifacts").fetchone()[0]
    store.object_path(sha).write_bytes(b"damaged")
    report = audit(store)
    assert report["status"] == "FAIL"
    assert report["artifact_failures"][0]["sha256"] == sha


def test_literal_find_offsets_pagination_and_cutoffs(store):
    text = "§ 🏛 Café\nQualifying\n  residential site\nException. qualifying residential site"
    ingest(store, text)
    r = Reader(store)
    first = r.find("key", "qualifying residential site", limit=1)
    match = first["matches"][0]
    assert match["match_offset"] == text.index("Qualifying")
    assert first["next_start"] == text.index("qualifying")
    assert r.read(first["id"], offset=match["match_offset"], length=10)["text"] == "Qualifying"
    second = r.find(first["id"], "qualifying residential site", start=first["next_start"])
    assert len(second["matches"]) == 1 and second["next_start"] is None
    assert not r.find(first["id"], ".*")["matches"]
    ingest(store, "new version", day="2025-06-01", observed="2026-02-01T00:00:00Z")
    assert not r.find("key", "qualifying")["matches"]
    assert r.find(first["id"], "qualifying")["matches"]
    assert not r.find(first["id"], "qualifying", as_of="2024-01-01")["found"]
    assert not r.find(first["id"], "qualifying", observation_cutoff="2025-01-01T00:00:00Z")["found"]
    with pytest.raises(ValueError):
        r.find("key", " ")
    ingest(store, "first" + "\n" * 2000 + "last", day="2025-07-01")
    assert len(r.find("key", "first last")["matches"][0]["excerpt"]) <= 1000


def test_search_ranks_definition_phrase_without_hiding_scoped_modification(store):
    source = retain(store, b"three distinct legal contexts")
    texts = [
        ("dispersed", "Qualifying lots, residential use, and site conditions."),
        ("scoped", "Within this district, qualifying residential site is modified."),
        (
            "general",
            "Definitions\nQualifying residential site\nGeneral definition; exceptions follow.",
        ),
    ]
    store.ingest(
        collection="test",
        document="definitions",
        title="Definitions",
        url=source.url,
        acquisition=source.id,
        snapshot_basis="fixture",
        parser="test",
        provisions=[Provision(key, key, "", text, "", source.url) for key, text in texts],
    )
    matches = Reader(store).search("qualifying residential site")["matches"]
    assert [m["key"] for m in matches] == ["general", "scoped", "dispersed"]
    assert [m["match_kind"] for m in matches] == ["standalone_phrase", "phrase", "dispersed_terms"]
    assert all("_text" not in m for m in matches)


def test_source_media_placeholders_survive_reader_projection(store):
    source = retain(store, b"source with unavailable embedded media")
    store.ingest(
        collection="test",
        document="media",
        title="Media",
        url=source.url,
        acquisition=source.id,
        snapshot_basis="fixture",
        parser="test",
        provisions=[
            Provision(
                "media",
                "Media",
                "",
                "[Source image not transcribed]",
                "<p>Legal text</p>",
                source.url,
                metadata={"media": [{"kind": "TipIn", "status": "not_transcribed"}]},
            )
        ],
    )
    result = Reader(store).read("media")
    assert result["text_completeness"] == "incomplete_without_source_media"
    assert result["media"] == [
        {"kind": "TipIn", "status": "not_transcribed", "acquired_receipt": None}
    ]
    assert audit(store)["media_bearing_units"] == [{"collection_id": "test", "units": 1}]


def test_pdf_running_header_does_not_outrank_substantive_page(store):
    source = retain(store, b"two source pages")
    text = "33.420 Design Overlay Zone\nPurpose\n" + "Actual development standards. " * 40
    store.ingest(
        collection="test",
        document="pages",
        title="Code",
        url=source.url,
        acquisition=source.id,
        snapshot_basis="fixture",
        parser="test",
        provisions=[
            Provision("page-1", "Page 1", "", text, "", source.url, unit_kind="pdf_page"),
            Provision(
                "page-2",
                "Page 2",
                "",
                "Chapter 33.420\nDesign Overlay Zone\n420-38",
                "",
                source.url,
                unit_kind="pdf_page",
            ),
        ],
    )
    assert [m["key"] for m in Reader(store).search("33.420")["matches"]] == ["page-1", "page-2"]
