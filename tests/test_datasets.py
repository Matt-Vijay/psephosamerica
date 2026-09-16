import hashlib
import json
import zipfile

import pytest
from conftest import retain

from psephos.datasets import export_dataset, import_dataset
from psephos.retrieve import Reader
from psephos.store import Feature, Provision, Reference, Store


def populate(store):
    source = retain(store, b"original publisher bytes")
    context = retain(store, b"inventory and image bytes")
    membership = retain(store, b"publisher membership evidence")
    with store.db:
        store.db.execute(
            "UPDATE acquisitions SET headers=? WHERE id=?",
            (
                json.dumps({"Content-Type": "text/plain", "Set-Cookie": "private-fixture"}),
                source.id,
            ),
        )
    store.collection(
        "other",
        ("us", "United States", "federal", None),
        name="Other",
        authority="Other",
        kind="code",
        homepage="https://example.test/other",
        source_status="fixture",
        access="fixture",
    )
    store.ingest(
        collection="test",
        document="doc",
        title="A document",
        url=source.url,
        acquisition=source.id,
        snapshot_date="2025-01-01",
        snapshot_basis="test",
        parser="test",
        metadata={
            "inventory_artifact": context.sha256,
            "image_receipt": context.id,
            "membership_acquisitions": {"https://example.test/inventory": membership.id},
        },
        provisions=[
            Provision(
                "key",
                "Test 1",
                "Heading",
                "Useful source words",
                "",
                source.url,
                references=(Reference("missing", "citation", "Missing", "source"),),
            )
        ],
        features=[
            Feature(
                "point",
                {"type": "Point", "coordinates": [0, 0]},
                {},
                (0, 0, 0, 0),
                acquisition_id=context.id,
            )
        ],
    )
    return source, context


def test_roundtrip_preserves_identity_clocks_dependencies_and_search(store, tmp_path):
    source, context = populate(store)
    original = Reader(store).read("key")
    output = tmp_path / "snapshot.zip"
    report = export_dataset(store.root, output, ["test"])
    assert report["collections"] == ["test"]
    assert report["omitted_receipt_headers"] == ["set-cookie"]
    assert {source.sha256, context.sha256} < set(report["objects"])
    assert len(report["objects"]) == 3
    destination = tmp_path / "restored"
    import_dataset(output, destination)
    restored = Store(destination, readonly=True)
    try:
        reader = Reader(restored)
        read = reader.read("key")
        for field in ("id", "text", "source", "version"):
            if field in original:
                assert read[field] == original[field]
        assert reader.search("Useful source")["matches"][0]["id"] == original["id"]
        assert restored.artifact(context.sha256) == b"inventory and image bytes"
        assert restored.db.execute("SELECT count(*) FROM collections").fetchone()[0] == 1
        assert restored.db.execute("SELECT count(*) FROM features").fetchone()[0] == 1
        assert restored.db.execute("SELECT count(*) FROM feature_bounds").fetchone()[0] == 1
        assert restored.db.execute("SELECT count(*) FROM legal_references").fetchone()[0] == 1
        assert (
            "private-fixture"
            not in restored.db.execute(
                "SELECT headers FROM acquisitions WHERE id=?", (source.id,)
            ).fetchone()[0]
        )
    finally:
        restored.close()
    assert (
        "private-fixture"
        in store.db.execute("SELECT headers FROM acquisitions WHERE id=?", (source.id,)).fetchone()[
            0
        ]
    )
    with pytest.raises(ValueError, match="will not replace"):
        import_dataset(output, destination)


@pytest.mark.parametrize("corruption", ["object", "catalog", "path", "duplicate", "limit"])
def test_untrusted_bundle_is_rejected_without_publishing(store, tmp_path, corruption):
    populate(store)
    original = tmp_path / "original.zip"
    export_dataset(store.root, original, ["test"])
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(bad, "w") as target:
        for item in source.infolist():
            raw = source.read(item)
            if corruption == "object" and item.filename.startswith("objects/"):
                raw = b"tampered"
            if corruption == "catalog" and item.filename == "catalog.jsonl":
                raw = raw.replace(b"Useful", b"Wrong!")
            target.writestr(item.filename, raw)
        if corruption == "path":
            target.writestr("../outside", b"bad")
        if corruption == "duplicate":
            with pytest.warns(UserWarning):
                target.writestr("catalog.jsonl", source.read("catalog.jsonl"))
    destination = tmp_path / "not-published"
    with pytest.raises(ValueError):
        import_dataset(bad, destination, max_bytes=1 if corruption == "limit" else 1024**3)
    assert not destination.exists()
    assert not (tmp_path / "outside").exists()


def test_export_requires_explicit_existing_scope_and_valid_objects(store, tmp_path):
    source, _ = populate(store)
    output = tmp_path / "snapshot.zip"
    for selected in ([], ["missing"]):
        with pytest.raises(ValueError):
            export_dataset(store.root, output, selected)
    store.object_path(source.sha256).write_bytes(b"broken")
    with pytest.raises(ValueError, match="corrupt"):
        export_dataset(store.root, output, ["test"])
    assert not output.exists()


@pytest.mark.parametrize(
    "damage", ["source_hash", "source_error", "source_clock", "missing_bounds", "orphan_bounds"]
)
def test_consistent_checksums_do_not_hide_invalid_relations(store, tmp_path, damage):
    _, context = populate(store)
    original = tmp_path / "original.zip"
    export_dataset(store.root, original, ["test"])
    bad = tmp_path / "invalid-relations.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(bad, "w") as destination:
        manifest = json.loads(source.read("manifest.json"))
        rows = [json.loads(line) for line in source.read("catalog.jsonl").splitlines()]
        for table, row in rows:
            if table == "versions":
                if damage == "source_hash":
                    row[2] = context.sha256
                if damage == "source_clock":
                    row[-1] = "1900-01-01T00:00:00Z"
            if table == "acquisitions" and damage == "source_error":
                row[-1] = "failed source"
        if damage == "missing_bounds":
            rows = [row for row in rows if row[0] != "feature_bounds"]
            manifest["counts"]["feature_bounds"] = 0
        if damage == "orphan_bounds":
            rows.append(["feature_bounds", [999, 0, 0, 0, 0]])
            manifest["counts"]["feature_bounds"] += 1
        catalog = b"".join((json.dumps(row) + "\n").encode() for row in rows)
        manifest["catalog_sha256"] = hashlib.sha256(catalog).hexdigest()
        manifest["catalog_bytes"] = len(catalog)
        for item in source.infolist():
            if item.filename not in {"catalog.jsonl", "manifest.json"}:
                destination.writestr(item, source.read(item))
        destination.writestr("catalog.jsonl", catalog)
        destination.writestr("manifest.json", json.dumps(manifest))
    target = tmp_path / "refused"
    with pytest.raises(ValueError, match="provenance|bounds"):
        import_dataset(bad, target)
    assert not target.exists()
