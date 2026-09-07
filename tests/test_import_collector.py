"""Isolated merge receipt/index preservation, idempotence, and rollback."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from conftest import retain

from psephos.store import Feature, Provision, Reference, Store

spec = importlib.util.spec_from_file_location(
    "import_collector", Path(__file__).parents[1] / "scripts" / "import_collector.py"
)
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def fixture_store(root):
    store = Store(root)
    receipt = retain(store, b"Official fixture passage: ballot registration.")
    retain(store, b"Official fixture passage: ballot registration.")
    store.collection(
        "xx-code",
        ("us-xx", "Fixture state", "state", "us"),
        name="Fixture code",
        authority="Fixture legislature",
        kind="code",
        homepage="https://example.test/",
        source_status="Fixture",
        access="Public",
        metadata={
            "authority_acquisition": receipt.id,
            "native_service_receipts": [receipt.id],
            "last_chapter_toc_receipt": receipt.id,
        },
    )
    store.ingest(
        collection="xx-code",
        document="xx-code:1",
        title="Fixture chapter",
        url=receipt.url,
        acquisition=receipt.id,
        snapshot_basis="Unknown exact publisher day",
        parser="fixture/1",
        metadata={
            "page_receipts": [{"acquisition_id": receipt.id, "artifact_sha": receipt.sha256}],
            "acquisition": {"id": receipt.id, "url": receipt.url},
        },
        provisions=[
            Provision(
                key="xx:1",
                citation="XX § 1",
                heading="Registration",
                text="Official fixture ballot registration passage.",
                markup="<p>ballot</p>",
                url=receipt.url,
                references=(Reference("xx:2", "publisher_link", "Section 2", "<a/>"),),
            )
        ],
        features=[
            Feature(
                key="polygon",
                geometry={"type": "Point", "coordinates": [1, 2]},
                properties={"source": "fixture"},
                bounds=(1, 2, 1, 2),
                acquisition_id=receipt.id,
            )
        ],
    )
    report = importer.inspect_store(store)
    manifest = {
        "batch_id": "fixture-1",
        **{k: report[k] for k in ("logical_sha256", "collections", "documents", "parsers")},
        "source_hosts": ["example.test"],
    }
    for key in (
        "parser_review",
        "public_source_review",
        "completeness_review",
        "clock_review",
        "fidelity_review",
        "representative_passage_review",
    ):
        manifest[key] = "Reviewed fixture"
    return store, receipt, manifest


def test_merge_remaps_preserves_indexes_and_is_idempotent(tmp_path):
    source, receipt, manifest = fixture_store(tmp_path / "source")
    (tmp_path / "target").mkdir()
    legacy = sqlite3.connect(tmp_path / "target/legal.sqlite3")
    legacy.executescript(importer.LEGACY_BASELINE_SCHEMA)
    legacy.close()
    target = Store(tmp_path / "target")
    baseline = retain(target, b"Preexisting baseline receipt")
    # Force feature row IDs to differ as well as acquisition IDs.
    target.collection(
        "baseline",
        ("us", "United States", "federal", None),
        name="Baseline",
        authority="Fixture",
        kind="code",
        homepage=baseline.url,
        source_status="Existing",
        access="Public",
    )
    target.ingest(
        collection="baseline",
        document="baseline:1",
        title="Existing",
        url=baseline.url,
        acquisition=baseline.id,
        snapshot_basis="Unknown",
        parser="fixture",
        provisions=[
            Provision(
                key="baseline:1",
                citation="Baseline 1",
                heading="Existing",
                text="Existing retained provision.",
                markup="",
                url=baseline.url,
            )
        ],
        features=[
            Feature(
                key="original",
                geometry={"type": "Point", "coordinates": [0, 0]},
                properties={},
                bounds=(0, 0, 0, 0),
            )
        ],
    )
    first = importer.publish(
        source.root, target.root, manifest, tmp_path / "ledger", min_free_bytes=0
    )
    assert first["status"] == "published"
    mapped = first["acquisition_id_map"][receipt.id]
    assert mapped != receipt.id
    assert len(set(first["acquisition_id_map"].values())) == 2
    assert (
        target.db.execute(
            "SELECT acquisition_id FROM versions WHERE document_id='xx-code:1'"
        ).fetchone()[0]
        == mapped
    )
    assert (
        target.db.execute(
            "SELECT acquisition_id FROM features WHERE source_key='polygon'"
        ).fetchone()[0]
        == mapped
    )
    metadata = json.loads(
        target.db.execute("SELECT metadata FROM versions WHERE document_id='xx-code:1'").fetchone()[
            0
        ]
    )
    assert metadata["page_receipts"][0]["acquisition_id"] == mapped
    assert metadata["acquisition"]["id"] == mapped
    metadata = json.loads(
        target.db.execute("SELECT metadata FROM collections WHERE id='xx-code'").fetchone()[0]
    )
    assert metadata["native_service_receipts"] == [mapped]
    assert metadata["authority_acquisition"] == mapped
    assert metadata["last_chapter_toc_receipt"] == mapped
    assert target.db.execute("SELECT count(*) FROM legal_references").fetchone()[0] == 1
    assert (
        target.db.execute(
            "SELECT count(*) FROM provision_search WHERE provision_search MATCH 'ballot'"
        ).fetchone()[0]
        == 1
    )
    assert (
        target.db.execute(
            "SELECT count(*) FROM features f JOIN feature_bounds b ON b.rowid=f.rowid WHERE b.minx=1"
        ).fetchone()[0]
        == 1
    )
    assert (
        source.object_path(receipt.sha256).stat().st_ino
        == target.object_path(receipt.sha256).stat().st_ino
    )
    assert not target.db.execute("PRAGMA foreign_key_check").fetchall()
    second = importer.publish(
        source.root, target.root, manifest, tmp_path / "ledger", min_free_bytes=0
    )
    assert second["canonical_before"] == second["canonical_after"] == first["canonical_after"]
    assert second["acquisition_id_map"] == first["acquisition_id_map"]
    source.close()
    target.close()


def test_conflict_rolls_back_all_rows_and_bad_evidence_is_rejected(tmp_path):
    source, receipt, manifest = fixture_store(tmp_path / "source")
    target = Store(tmp_path / "target")
    # Conflict occurs after artifact and receipt insertion, proving transaction rollback.
    target.collection(
        "xx-code",
        ("us-xx", "Conflicting state", "state", "us"),
        name="Unrelated existing code",
        authority="Other",
        kind="code",
        homepage="https://example.test/",
        source_status="existing",
        access="Public",
    )
    before = importer.counts(target.db)
    with pytest.raises(importer.Rejected, match="Conflicting jurisdictions"):
        importer.publish(source.root, target.root, manifest, tmp_path / "ledger", min_free_bytes=0)
    assert importer.counts(target.db) == before
    assert source.object_path(receipt.sha256).exists()
    assert (
        json.loads((tmp_path / "ledger/fixture-1.rejected.json").read_text())["status"]
        == "rejected"
    )
    with pytest.raises(importer.Rejected, match="Unrecognized logical receipt"):
        importer.remap_json('{"mystery_receipt_number": 1}', {1: 8})
    source.object_path(receipt.sha256).write_bytes(b"corruption")
    with pytest.raises(importer.Rejected, match="Corrupt source"):
        importer.publish(source.root, target.root, manifest, tmp_path / "ledger", min_free_bytes=0)
    assert importer.counts(target.db) == before
    source.close()
    target.close()
