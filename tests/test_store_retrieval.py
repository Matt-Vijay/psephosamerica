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
