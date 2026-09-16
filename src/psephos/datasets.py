"""Portable source snapshots: fixed-schema rows and verified, content-addressed bytes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import IO, Any

from .acquire import SAFE_HEADERS
from .store import SCHEMA, Store, json_text, utc_now

FORMAT = "psephos-dataset-1"
TABLES = (
    "jurisdictions",
    "collections",
    "inventories",
    "documents",
    "versions",
    "provisions",
    "legal_references",
    "features",
    "feature_bounds",
    "acquisitions",
    "artifacts",
)
MAX_BYTES = 32 * 1024**3
MAX_ROW_BYTES = 32 * 1024**2


def _columns() -> dict[str, list[str]]:
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(SCHEMA)
        return {
            table: [r[1] for r in db.execute(f"PRAGMA table_info({table})")] for table in TABLES
        }
    finally:
        db.close()


def _copy(source: IO[bytes], destination: IO[bytes]) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while block := source.read(1024 * 1024):
        destination.write(block)
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def export_dataset(root: Path, output: Path, collections: list[str]) -> dict[str, Any]:
    """Export explicit collections without rewriting their source IDs or clocks.

    JSON metadata can refer to additional receipts and source/media hashes. Follow
    those dependencies, but never pull in unrelated collections or execute metadata.
    """
    if not collections:
        raise ValueError("Choose at least one --collection; export never defaults to all data")
    output = output.absolute()
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    store = Store(root, readonly=True)
    columns = _columns()
    selected = sorted(set(collections))
    params = tuple(selected)
    placeholders = ",".join("?" for _ in selected)
    docs = f"SELECT id FROM documents WHERE collection_id IN ({placeholders})"
    versions = f"SELECT id FROM versions WHERE document_id IN ({docs})"
    units = f"SELECT id FROM provisions WHERE version_id IN ({versions})"
    where = {
        "collections": f"id IN ({placeholders})",
        "inventories": f"collection_id IN ({placeholders})",
        "documents": f"id IN ({docs})",
        "versions": f"id IN ({versions})",
        "provisions": f"version_id IN ({versions})",
        "legal_references": f"provision_id IN ({units})",
        "features": f"version_id IN ({versions})",
        "feature_bounds": f"rowid IN (SELECT rowid FROM features WHERE version_id IN ({versions}))",
    }
    try:
        store.db.execute("BEGIN")
        available = {r[0] for r in store.db.execute("SELECT id FROM collections")}
        if missing := set(selected) - available:
            raise ValueError("Unknown collections: " + ", ".join(sorted(missing)))
        known_hashes = {r[0]: r[1] for r in store.db.execute("SELECT sha256,bytes FROM artifacts")}
        receipts: set[int] = set()
        hashes: set[str] = set()

        def dependencies(value: Any, key: str = "", receipt_context: bool = False) -> None:
            low = key.lower()
            relevant = "receipt" in low or "acquisition" in low
            if isinstance(value, dict):
                if low == "membership_acquisitions":
                    if not all(type(item) is int for item in value.values()):
                        raise ValueError("Invalid membership receipt map")
                    receipts.update(value.values())
                for name, item in value.items():
                    dependencies(item, name, relevant or receipt_context)
            elif isinstance(value, list):
                for item in value:
                    dependencies(item, key, receipt_context)
            elif isinstance(value, str) and value in known_hashes:
                hashes.add(value)
            elif isinstance(value, int) and not isinstance(value, bool):
                if (
                    low in {"receipt", "receipt_id", "acquisition", "acquisition_id"}
                    or low.endswith(
                        ("_receipt", "_receipt_id", "_acquisition", "_acquisition_id", "_receipts")
                    )
                    or (receipt_context and low == "id")
                ):
                    receipts.add(value)

        with tempfile.TemporaryDirectory(prefix=".psephos-export-", dir=output.parent) as scratch:
            directory = Path(scratch)
            catalog = directory / "catalog.jsonl"
            counts = dict.fromkeys(TABLES, 0)
            omitted_headers: set[str] = set()
            with catalog.open("wb") as stream:

                def write(table: str, row: sqlite3.Row) -> None:
                    record = dict(row)
                    for field in ("metadata", "properties"):
                        if field in record:
                            dependencies(json.loads(record[field]))
                    if table in {"versions", "features"}:
                        receipts.add(record["acquisition_id"])
                    if table == "versions":
                        hashes.add(record["artifact_sha"])
                    if table == "acquisitions":
                        if record["sha256"]:
                            hashes.add(record["sha256"])
                        headers = json.loads(record["headers"])
                        dependencies(headers)
                        safe = SAFE_HEADERS | {
                            "psephos_request_started_at",
                            "psephos_observed_at_basis",
                        }
                        omitted_headers.update(k.lower() for k in headers if k.lower() not in safe)
                        record["headers"] = json_text(
                            {k.lower(): v for k, v in headers.items() if k.lower() in safe}
                        )
                    line = (json_text([table, [record[k] for k in columns[table]]]) + "\n").encode()
                    if len(line) > MAX_ROW_BYTES:
                        raise ValueError(f"Catalog row exceeds portable format limit: {table}")
                    stream.write(line)
                    counts[table] += 1

                jurisdiction_sql = (
                    "WITH RECURSIVE scope(id) AS ("
                    f"SELECT jurisdiction_id FROM collections WHERE id IN ({placeholders}) UNION "
                    "SELECT j.parent_id FROM jurisdictions j JOIN scope s ON j.id=s.id WHERE j.parent_id IS NOT NULL) "
                    "SELECT * FROM jurisdictions WHERE id IN (SELECT id FROM scope) ORDER BY id"
                )
                for row in store.db.execute(jurisdiction_sql, params):
                    write("jurisdictions", row)
                for table, clause in where.items():
                    print(f"export: {table}", file=sys.stderr)
                    for row in store.db.execute(f"SELECT * FROM {table} WHERE {clause}", params):
                        write(table, row)

                # Receipt IDs remain stable, including references embedded in metadata.
                included: set[int] = set()
                processed_hashes: set[str] = set()
                while receipts - included or hashes - processed_hashes:
                    for sha in hashes - processed_hashes:
                        receipts.update(
                            r[0]
                            for r in store.db.execute(
                                "SELECT id FROM acquisitions WHERE sha256=?", (sha,)
                            )
                        )
                        processed_hashes.add(sha)
                    for receipt_id in sorted(receipts - included):
                        row = store.db.execute(
                            "SELECT * FROM acquisitions WHERE id=?", (receipt_id,)
                        ).fetchone()
                        if row is None:
                            raise ValueError(f"Missing dependent source receipt: {receipt_id}")
                        write("acquisitions", row)
                        included.add(receipt_id)
                for sha in sorted(hashes):
                    row = store.db.execute(
                        "SELECT * FROM artifacts WHERE sha256=?", (sha,)
                    ).fetchone()
                    if row is None:
                        raise ValueError(f"Missing dependent source artifact: {sha}")
                    write("artifacts", row)

            with catalog.open("rb") as catalog_stream:
                catalog_hash = hashlib.file_digest(catalog_stream, "sha256").hexdigest()
            manifest: dict[str, Any] = {
                "format": FORMAT,
                "created_at": utc_now(),
                "collections": selected,
                "counts": counts,
                "catalog_sha256": catalog_hash,
                "catalog_bytes": catalog.stat().st_size,
                "objects": {sha: known_hashes[sha] for sha in sorted(hashes)},
                "omitted_receipt_headers": sorted(omitted_headers),
                "notice": "Source IDs and dates are preserved; exported receipt headers are filtered. Snapshot completeness, currency and redistribution rights remain source-specific. Import is not publisher authentication.",
            }
            packed = directory / "snapshot.zip"
            print("export: compressing catalog and verifying source files", file=sys.stderr)
            with zipfile.ZipFile(
                packed, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                archive.writestr("manifest.json", json_text(manifest))
                archive.write(catalog, "catalog.jsonl")
                for sha, size in manifest["objects"].items():
                    name = f"objects/{sha[:2]}/{sha}"
                    with (
                        store.object_path(sha).open("rb") as source,
                        archive.open(name, "w", force_zip64=True) as destination,
                    ):
                        actual = _copy(source, destination)
                    if actual != (sha, size):
                        raise ValueError(f"Source object changed or is corrupt: {sha}")
            os.link(packed, output)
            return {"path": str(output), "bytes": output.stat().st_size, **manifest}
    finally:
        store.close()


def import_dataset(bundle: Path, root: Path, *, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    """Validate a bounded ZIP, build trusted schema locally, then publish a new store."""
    root = root.absolute()
    if root.exists() or root.is_symlink():
        raise ValueError(f"Import needs a new data directory; will not replace {root}")
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    root.parent.mkdir(parents=True, exist_ok=True)
    columns = _columns()
    with zipfile.ZipFile(bundle) as archive:
        entries = archive.infolist()
        names = {item.filename for item in entries}
        if len(entries) > 100_000 or len(names) != len(entries):
            raise ValueError("Too many or duplicate ZIP members")
        if sum(item.file_size for item in entries) > max_bytes:
            raise ValueError("Dataset exceeds expanded byte limit")
        if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > 8 * 1024**2:
            raise ValueError("Missing or oversized dataset manifest")
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise ValueError("Unsupported dataset format")
        objects = manifest.get("objects")
        if not isinstance(objects, dict):
            raise ValueError("Invalid object inventory")
        expected = {"manifest.json", "catalog.jsonl"}
        for sha, size in objects.items():
            if (
                not isinstance(sha, str)
                or not re.fullmatch(r"[a-f0-9]{64}", sha)
                or type(size) is not int
                or size < 0
            ):
                raise ValueError("Invalid object hash or size")
            expected.add(f"objects/{sha[:2]}/{sha}")
        if names != expected:
            raise ValueError("Unexpected or missing dataset member")
        for item in entries:
            if item.is_dir() or (item.external_attr >> 16) & 0o170000 not in (0, 0o100000):
                raise ValueError("Dataset members must be regular files")
        with tempfile.TemporaryDirectory(prefix=".psephos-import-", dir=root.parent) as scratch:
            temporary = Path(scratch)
            staged = temporary / "data"
            store = Store(staged)
            try:
                print(f"import: verifying {len(objects)} source files", file=sys.stderr)
                for sha, size in objects.items():
                    path = store.object_path(sha)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with (
                        archive.open(f"objects/{sha[:2]}/{sha}") as source,
                        path.open("xb") as destination,
                    ):
                        actual = _copy(source, destination)
                    if actual != (sha, size):
                        raise ValueError(f"Object hash/size mismatch: {sha}")
                counts = dict.fromkeys(TABLES, 0)
                digest = hashlib.sha256()
                catalog_size = 0
                with store.db, archive.open("catalog.jsonl") as stream:
                    store.db.execute("BEGIN")
                    store.db.execute("PRAGMA defer_foreign_keys=ON")
                    while line := stream.readline(MAX_ROW_BYTES + 1):
                        if len(line) > MAX_ROW_BYTES:
                            raise ValueError("Catalog row exceeds byte limit")
                        digest.update(line)
                        catalog_size += len(line)
                        entry = json.loads(line)
                        if not isinstance(entry, list) or len(entry) != 2:
                            raise ValueError("Invalid catalog row")
                        table, values = entry
                        if (
                            not isinstance(table, str)
                            or table not in columns
                            or not isinstance(values, list)
                            or len(values) != len(columns[table])
                        ):
                            raise ValueError("Invalid catalog table or columns")
                        if not counts[table]:
                            print(f"import: {table}", file=sys.stderr)
                        store.db.execute(
                            f"INSERT INTO {table} VALUES ({','.join('?' for _ in values)})", values
                        )
                        counts[table] += 1
                    if digest.hexdigest() != manifest.get(
                        "catalog_sha256"
                    ) or catalog_size != manifest.get("catalog_bytes"):
                        raise ValueError("Catalog hash/size mismatch")
                    if counts != manifest.get("counts"):
                        raise ValueError("Catalog count mismatch")
                    if dict(store.db.execute("SELECT sha256,bytes FROM artifacts")) != objects:
                        raise ValueError("Catalog artifacts differ from object inventory")
                    selected = [
                        r[0] for r in store.db.execute("SELECT id FROM collections ORDER BY id")
                    ]
                    if selected != manifest.get("collections"):
                        raise ValueError("Catalog collections differ from manifest")
                    if store.db.execute("PRAGMA foreign_key_check").fetchone():
                        raise ValueError("Catalog has dangling foreign keys")
                    if store.db.execute(
                        "SELECT 1 FROM versions v JOIN acquisitions a ON a.id=v.acquisition_id "
                        "WHERE v.artifact_sha != a.sha256 OR a.sha256 IS NULL "
                        "OR a.status NOT BETWEEN 200 AND 299 OR a.error IS NOT NULL "
                        "OR julianday(v.available_at) IS NULL OR julianday(a.observed_at) IS NULL "
                        "OR julianday(v.available_at)<julianday(a.observed_at) LIMIT 1"
                    ).fetchone():
                        raise ValueError("Catalog has inconsistent source provenance")
                    if store.db.execute(
                        "SELECT 1 FROM features f JOIN acquisitions a ON a.id=f.acquisition_id "
                        "WHERE a.sha256 IS NULL OR a.status NOT BETWEEN 200 AND 299 "
                        "OR a.error IS NOT NULL LIMIT 1"
                    ).fetchone():
                        raise ValueError("Catalog has inconsistent geometry provenance")
                    if store.db.execute(
                        "SELECT 1 FROM features f LEFT JOIN feature_bounds b ON b.rowid=f.rowid "
                        "WHERE b.rowid IS NULL UNION ALL "
                        "SELECT 1 FROM feature_bounds b LEFT JOIN features f ON f.rowid=b.rowid "
                        "WHERE f.rowid IS NULL LIMIT 1"
                    ).fetchone():
                        raise ValueError("Catalog has missing or orphaned spatial bounds")
                store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                store.db.execute("PRAGMA journal_mode=DELETE")
            finally:
                store.close()
            (staged / "dataset.json").write_text(json_text(manifest) + "\n")
            if root.exists() or root.is_symlink():
                raise ValueError(f"Data directory appeared during import: {root}")
            staged.rename(root)
    return {"path": str(root), "collections": selected, "counts": counts, "status": "imported"}
