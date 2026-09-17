"""Operational summaries must not reuse an unscoped discovery-directory page."""

import json
import sys

import pytest
from conftest import retain

from psephos import cli, dc, geography, municipal, sources, texas
from psephos.audit import audit
from psephos.retrieve import Reader
from psephos.store import Provision, Store, writer_lock


def test_cli_audit_failure_has_nonzero_exit_and_keeps_report(store, monkeypatch, capsys):
    source = retain(store, b"fixture bytes")
    monkeypatch.setattr(sys, "argv", ["psephos", "--data", str(store.root), "audit"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"
    store.object_path(source.sha256).write_bytes(b"corrupted")
    with pytest.raises(SystemExit) as failed:
        cli.main()
    assert failed.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "FAIL"


def test_cli_reindex_respects_active_writer(store, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["psephos", "--data", str(store.root), "reindex", "nyc"])
    monkeypatch.setattr(municipal, "reindex_nyc", lambda *_: pytest.fail("Concurrent reindex"))
    with writer_lock(store.root), pytest.raises(SystemExit) as failed:
        cli.main()
    assert failed.value.code == 1
    assert "Another Psephos writer" in capsys.readouterr().err


def test_source_catalog_and_imported_store_budget_boundary(store, monkeypatch):
    catalog = sources.available_sources()
    entries = {row["alias"]: row for row in catalog["sources"]}
    assert {
        "georgia",
        "virginia",
        "oregon",
        "washington",
        "nebraska",
        "south-carolina-code",
        "minnesota-statutes",
    } <= entries.keys()
    assert entries["south-carolina-code"]["collection_ids"] == ["sc-code"]
    assert entries["minnesota-statutes"]["collection_ids"] == ["mn-statutes"]
    assert entries["nebraska"]["collection_ids"] == [
        "ne-revised-statutes",
        "ne-uniform-commercial-code",
        "ne-constitution",
        "ne-revised-statutes-appendix",
    ]
    assert entries["ecfr"]["historical_sync"] is True
    assert not entries["nebraska"]["historical_sync"]
    assert json.loads(json.dumps(catalog)) == catalog

    class NeverAcquire(sources.CampaignAcquirer):
        def __init__(self, *args, **kwargs):
            pytest.fail("Existing data without the original budget must fail before HTTP")

    retain(store, b"Preflight metadata, before registering a source collection")
    for collection, prefixes in (("test", ()), ("not-yet-discovered", ("https://example.test/",))):
        source = sources._Source(
            lambda *args: None,
            (collection,),
            "Fixture",
            campaign=NeverAcquire,
            receipt_prefixes=prefixes,
        )
        monkeypatch.setattr(sources, "_sources", lambda source=source: {"fixture": source})
        with pytest.raises(sources.AcquisitionError, match="Restore the original fixture campaign"):
            sources.sync_collections(store.root, ["fixture"])


def test_sync_summarizes_only_requested_source_collections(store, monkeypatch):
    targets = {
        "uscode": (sources, "sync_uscode", ("uscode",)),
        "ecfr": (sources, "sync_ecfr", ("ecfr",)),
        "dc": (dc, "sync_dc", ("dc-code", "dc-laws")),
        "texas": (texas, "sync_texas", ("texas",)),
        "nyc": (municipal, "sync_nyc", ("nyc-zoning",)),
        "portland": (municipal, "sync_portland", ("portland-zoning",)),
        "portland-guides": (municipal, "sync_portland_guides", ("portland-zoning-guides",)),
        "nyc-gis": (geography, "sync_nyc_geo", ("nyc-zoning-gis",)),
        "portland-gis": (geography, "sync_portland_geo", ("portland-zoning-gis",)),
    }
    statements = []
    closed = []

    class TracedStore(Store):
        def __init__(self, root):
            super().__init__(root)
            self.db.set_trace_callback(statements.append)

        def close(self):
            closed.append("store")
            super().close()

    class OfflineAcquirer:
        downloaded = 0

        def __init__(self, store, *, refresh):
            assert refresh is True

        def close(self):
            closed.append("acquirer")

    def adapter(collection_ids):
        def run(s, a, limit, as_of):
            assert limit == 2 and as_of == "2026-01-01"
            a.downloaded += 7
            for collection_id in collection_ids:
                s.collection(
                    collection_id,
                    ("us", "United States", "federal", None),
                    name="Fixture source",
                    authority="Fixture",
                    kind="code",
                    homepage="https://example.test/",
                    source_status="Fixture",
                    access="Offline fixture",
                )
                s.inventory(collection_id, "one", "https://example.test/one", "pending")

        return run

    for module, name, collection_ids in targets.values():
        monkeypatch.setattr(module, name, adapter(collection_ids))
    monkeypatch.setattr(sources, "Store", TracedStore)
    monkeypatch.setattr(sources, "Acquirer", OfflineAcquirer)
    actual_coverage = Reader.coverage
    scoped_calls = []

    def scoped_coverage(self, **kwargs):
        assert kwargs.get("collection") is not None
        scoped_calls.append(kwargs["collection"])
        return actual_coverage(self, **kwargs)

    monkeypatch.setattr(Reader, "coverage", scoped_coverage)
    result = sources.sync_collections(
        store.root, list(targets), refresh=True, limit=2, as_of="2026-01-01"
    )
    expected = [cid for _, _, ids in targets.values() for cid in ids]
    assert result["requested_sources"] == list(targets)
    assert [row["id"] for row in result["collections"]] == expected
    assert scoped_calls == expected
    assert result["collection_summary_status"] == dict.fromkeys(expected, "ok")
    assert result["downloaded_this_run"] == 63
    assert "test" not in expected  # The pre-existing fixture collection is not requested.
    assert closed == ["acquirer", "store"]
    assert not any(
        table in statement.upper()
        for statement in statements
        for table in ("FROM PROVISIONS", "JOIN PROVISIONS", "FROM FEATURES", "JOIN FEATURES")
    )


def test_sync_reports_unregistered_requested_collection_and_closes_on_failure(store, monkeypatch):
    closed = []

    class OfflineAcquirer:
        downloaded = 0

        def __init__(self, store, **kwargs):
            pass

        def close(self):
            closed.append(True)

    monkeypatch.setattr(sources, "Acquirer", OfflineAcquirer)
    monkeypatch.setattr(sources, "sync_uscode", lambda *args: None)
    result = sources.sync_collections(store.root, ["uscode"])
    assert result["collections"] == []
    assert result["collection_summary_status"] == {"uscode": "unknown_collection"}

    def fail(*args):
        raise ValueError("fixture failure")

    monkeypatch.setattr(sources, "sync_uscode", fail)
    with pytest.raises(ValueError, match="fixture failure"):
        sources.sync_collections(store.root, ["uscode"])
    assert closed == [True, True]


def test_audit_corpus_counts_are_not_a_discovery_page(store, monkeypatch):
    source = retain(store, b"fixture bytes")
    for snapshot, keys in [("2026-01-01", ["one", "two"]), ("2026-02-01", ["one"])]:
        store.ingest(
            collection="test",
            document="fixture-document",
            title="Fixture document",
            url=source.url,
            acquisition=source.id,
            snapshot_date=snapshot,
            snapshot_basis="Fixture snapshot",
            parser="fixture",
            provisions=[
                Provision(key, key, "Fixture", "Fixture body", "", source.url) for key in keys
            ],
        )
    with store.db:
        store.db.executemany(
            "INSERT INTO jurisdictions VALUES (?,?,?,?)",
            [(f"fixture-{i}", f"Fixture {i}", "state", "us") for i in range(12)],
        )

    def no_directory(*args, **kwargs):
        pytest.fail("An audit must not return a discovery-directory page")

    monkeypatch.setattr(Reader, "coverage", no_directory)
    report = audit(store)
    assert report["status"] == "PASS"
    assert "coverage" not in report
    assert report["corpus"] == {
        "registered_jurisdictions": 13,
        "registered_collections": 1,
        "documents": 1,
        "retained_versions": 2,
        "retained_artifacts": 1,
        "retained_artifact_bytes": len(b"fixture bytes"),
        "acquisition_receipts": 1,
        "latest_projected_units": 1,
        "latest_geometry_features": 0,
    }
    assert report["version_clocks"][0]["versions"] == 2
    assert report["text_quality"] == [
        {"collection_id": "test", "quality": "publisher_text_projection", "units": 1}
    ]
