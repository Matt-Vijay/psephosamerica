"""Source discovery stays bounded, scoped, honest and independent of text-table scans."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from conftest import retain

from psephos.retrieve import Reader
from psephos.store import Provision


def test_directory_scope_and_notices_without_text_table_access(store):
    metadata = {
        "scope": "Two selected chapters only",
        "currency_notice": "Body 2025; TOC 2026",
        "publisher_notice": "No complete-current-law claim",
        "warning": "Not local law",
    }
    with store.db:
        store.db.execute(
            "UPDATE collections SET metadata=? WHERE id='test'", (json.dumps(metadata),)
        )
        store.db.execute(
            "INSERT INTO jurisdictions VALUES ('empty','Empty registered scope','state','us')"
        )
    reads = []

    def authorize(action, table, column, database, origin):
        if action == sqlite3.SQLITE_READ:
            reads.append(table)
            if table in {"provisions", "features", "versions", "acquisitions", "artifacts"}:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    store.db.set_authorizer(authorize)
    r = Reader(store)
    directory = r.coverage(limit=1)
    assert directory["view"] == "jurisdictions" and len(directory["jurisdictions"]) == 1
    assert directory["next_offset"] == 1
    assert r.coverage(offset=100)["status"] == "page_exhausted"
    card = r.coverage(collection="test")["collections"][0]
    assert card["metadata"] == metadata and card["documents"] == 0
    assert "completeness" in card
    assert r.coverage(jurisdiction="empty")["status"] == "empty"
    assert r.coverage(jurisdiction="not-registered")["status"] == "unknown_jurisdiction"
    assert r.coverage(collection="not-registered")["status"] == "unknown_collection"
    assert r.coverage(jurisdiction="empty", collection="test")["status"] == "scope_mismatch"
    assert "provisions" not in reads and "versions" not in reads
    store.db.set_authorizer(None)


def test_document_drilldown_and_mixed_inventory_are_separate(store):
    source = retain(store, b"two documents")
    for i in range(2):
        store.ingest(
            collection="test",
            document=f"chapter:{i}",
            title=f"Chapter {i}",
            url=source.url,
            acquisition=source.id,
            snapshot_basis="Unknown exact date",
            parser="fixture",
            provisions=[
                Provision(
                    f"section:{i}",
                    f"Section {i}",
                    "Heading",
                    "Complete fixture text",
                    "",
                    source.url,
                )
            ],
        )
    store.inventory("test", "title-index", source.url, "indexed")
    store.inventory("test", "title-uncollected", source.url, "pending")
    store.inventory("test", "chapter-body", source.url, "indexed")
    r = Reader(store)
    page = r.coverage(view="documents", collection="test", limit=1)
    assert page["total"] == 2 and page["next_offset"] == 1
    doc = page["documents"][0]
    assert r.read(doc["first_key"])["id"] == doc["first_provision_id"]
    assert doc["snapshot_date"] is None
    assert (
        r.coverage(view="documents", collection="test", offset=1)["documents"][0]["first_key"]
        == "section:1"
    )
    inventory = r.coverage(view="inventory", collection="test", status="pending")
    assert inventory["total"] == 1 and inventory["inventory"][0]["item"] == "title-uncollected"
    assert inventory["source"]["documents"] == 2
    assert "document_id" not in inventory["inventory"][0]
    assert (
        r.coverage(view="inventory", collection="test", status="nonexistent")["status"]
        == "no_inventory_items_with_status"
    )


def test_invalid_filters_and_oversized_metadata_fail_explicitly(store):
    r = Reader(store)
    for kwargs in (
        {"collection": ""},
        {"jurisdiction": " "},
        {"view": ""},
        {"limit": 0},
        {"limit": 21},
        {"offset": -1},
        {"view": "documents"},
        {"view": "inventory"},
        {"status": "pending"},
        {"view": "jurisdictions", "collection": "test"},
    ):
        with pytest.raises(ValueError):
            r.coverage(**kwargs)
    with store.db:
        store.db.execute(
            "UPDATE collections SET metadata=? WHERE id='test'",
            (json.dumps({"scope": "x" * 30000}),),
        )
    response = r.coverage(collection="test")
    assert len(json.dumps(response).encode()) < 24576
    assert response["collections"][0]["scope_status"] == "metadata_exceeds_directory_budget"
    assert response["collections"][0]["metadata_warning"]


def test_oversized_inventory_item_does_not_hide_later_entries(store):
    store.inventory("test", "a-large", "https://example.test/large", "failed", "x" * 30000)
    store.inventory("test", "b-small", "https://example.test/small", "indexed")
    reader = Reader(store)
    page = reader.coverage(view="inventory", collection="test")
    assert page["status"] == "oversized_source_metadata" and not page["inventory"]
    assert page["skipped_entry_offset"] == 0 and page["next_offset"] == 1
    assert len(json.dumps(page).encode()) <= 24576
    next_page = reader.coverage(view="inventory", collection="test", offset=page["next_offset"])
    assert next_page["inventory"][0]["item"] == "b-small" and next_page["next_offset"] is None


def test_scope_annotation_is_additive_guarded_atomic_and_idempotent(store):
    path = Path(__file__).resolve().parents[1] / "scripts/annotate_discovery.py"
    spec = importlib.util.spec_from_file_location("annotate_discovery", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = store.db.execute("SELECT metadata FROM collections WHERE id='test'").fetchone()[0]
    patch = {
        "collection_id": "test",
        "before_metadata_json": before,
        "before_metadata_sha256": module.digest(before.encode()),
        "metadata_addition": {"discovery_review": {"scope": "fixture only"}},
        "evidence": {"receipts": []},
    }
    assert module.annotate(store, [patch])["annotations"][0]["changed"]
    assert not module.annotate(store, [patch])["annotations"][0]["changed"]
    with store.db:
        store.db.execute("UPDATE collections SET metadata=? WHERE id='test'", (before,))
    with pytest.raises(ValueError, match="Current metadata differs"):
        module.annotate(store, [patch, {**patch, "collection_id": "missing"}])
    assert (
        store.db.execute("SELECT metadata FROM collections WHERE id='test'").fetchone()[0] == before
    )
    with pytest.raises(ValueError, match="Source receipt changed"):
        module.annotate(store, [{**patch, "evidence": {"receipts": [{"id": 999}]}}])
