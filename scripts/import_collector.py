"""Offline, fail-closed publication of a reviewed, quiescent collector Store.

No fetching, parser execution, schema migration, overwrites, or database copies.
Use inspect_store() to freeze a logical digest, then supply a publisher-owned
acceptance JSON to --manifest. Receipts/artifacts are retained even for failed
attempts; every document and parser must be explicitly accepted by the reviewer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from psephos.store import SCHEMA, Store, json_text, stable_id, utc_now

TABLES = (
    "artifacts",
    "acquisitions",
    "jurisdictions",
    "collections",
    "inventories",
    "documents",
    "versions",
    "provisions",
    "legal_references",
    "features",
    "feature_bounds",
)
KEYS = {
    "artifacts": ("sha256",),
    "jurisdictions": ("id",),
    "collections": ("id",),
    "inventories": ("collection_id", "item"),
    "documents": ("id",),
    "versions": ("id",),
    "provisions": ("id",),
    "features": ("id",),
    "legal_references": ("provision_id", "target", "relation", "label"),
    "feature_bounds": ("rowid",),
}
BASELINE = {
    "uscode",
    "ecfr",
    "dc-code",
    "dc-law",
    "dc-laws",
    "texas",
    "nyc-zoning",
    "portland-zoning",
    "nyc-zoning-gis",
    "portland-zoning-gis",
}
# The retained baseline predates stricter NOT NULL declarations. Its columns,
# keys and indexes are identical; do not migrate it or relax source validation.
LEGACY_BASELINE_SCHEMA = SCHEMA.replace("available_at TEXT NOT NULL", "available_at TEXT").replace(
    "geometry TEXT NOT NULL, properties TEXT NOT NULL,\n"
    " layer TEXT NOT NULL, acquisition_id INTEGER NOT NULL REFERENCES acquisitions,",
    "geometry TEXT NOT NULL, properties TEXT NOT NULL, layer TEXT NOT NULL DEFAULT '', "
    "acquisition_id INTEGER REFERENCES acquisitions(id),",
)


class Rejected(ValueError):
    """Evidence or ownership does not match the reviewed batch."""


def require(condition, message):
    if not condition:
        raise Rejected(message)


def schema(db):
    return sorted(
        tuple(r)
        for r in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL"
        )
    )


def validate_schema(db, *, legacy_baseline=False):
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript(SCHEMA)
        require(db.execute("PRAGMA user_version").fetchone()[0] == 1, "Schema version conflict")
        if schema(db) != schema(expected) and legacy_baseline:
            expected.close()
            expected = sqlite3.connect(":memory:")
            expected.executescript(LEGACY_BASELINE_SCHEMA)
        require(schema(db) == schema(expected), "Schema/index/trigger conflict")
    finally:
        expected.close()


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def clock(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, f"Unknown acquisition/availability clock: {value}")
    return result


def inspect_store(store):
    """Logical fingerprint includes source-local row IDs and all receipt/index rows."""
    validate_schema(store.db)
    fingerprint = hashlib.sha256()
    counts = {}
    for table in TABLES:
        counts[table] = 0
        columns = [r[1] for r in store.db.execute(f"PRAGMA table_info({table})")]
        order = ",".join('"' + c + '"' for c in columns)
        for row in store.db.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            fingerprint.update((json_text([table, list(row)]) + "\n").encode())
            counts[table] += 1
    qualities = dict(
        store.db.execute(
            "SELECT coalesce(json_extract(metadata,'$.text_quality'),'publisher_text_projection'),"
            "count(*) FROM provisions GROUP BY 1"
        )
    )
    metadata = sum(n for q, n in qualities.items() if "metadata" in q.lower())
    return {
        "logical_sha256": fingerprint.hexdigest(),
        "counts": counts,
        "text_quality": qualities,
        "unit_kinds": dict(
            store.db.execute("SELECT unit_kind,count(*) FROM provisions GROUP BY unit_kind")
        ),
        "metadata_only_units": metadata,
        "source_text_units": counts["provisions"] - metadata,
        "unknown_snapshot_versions": store.db.execute(
            "SELECT count(*) FROM versions WHERE snapshot_date IS NULL"
        ).fetchone()[0],
        "collections": [r[0] for r in store.db.execute("SELECT id FROM collections ORDER BY id")],
        "documents": [r[0] for r in store.db.execute("SELECT id FROM documents ORDER BY id")],
        "parsers": [
            r[0] for r in store.db.execute("SELECT DISTINCT parser FROM versions ORDER BY parser")
        ],
    }


def remap_json(raw, mapping):
    """Recognize model receipt fields; reject unknown integer-bearing receipt fields."""

    def visit(value, key="", receipt_context=False):
        low = key.lower()
        relevant = "acquisition" in low or "receipt" in low
        direct = low in {"acquisition", "acquisition_id", "receipt", "receipt_id"} or low.endswith(
            ("_acquisition", "_acquisition_id", "_receipt", "_receipt_id", "_receipts")
        )
        if isinstance(value, dict):
            return {k: visit(v, k, relevant) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v, key, receipt_context) for v in value]
        if relevant and isinstance(value, str) and value.isdigit():
            require(False, f"String-valued logical receipt needs explicit review: {key}")
        if isinstance(value, int) and not isinstance(value, bool):
            if direct or (receipt_context and low == "id"):
                require(value in mapping, f"Missing logical receipt: {key}={value}")
                return mapping[value]
            require(not relevant, f"Unrecognized logical receipt field: {key}")
        return value

    before = json.loads(raw)
    after = visit(before)
    return raw if before == after else json_text(after)


def insert_exact(db, table, row):
    keys = KEYS[table]
    existing = db.execute(
        f"SELECT * FROM {table} WHERE " + " AND ".join(f"{k} IS ?" for k in keys),
        tuple(row[k] for k in keys),
    ).fetchone()
    if existing:
        require(
            all(existing[k] == v for k, v in row.items()),
            f"Conflicting {table}: " + str(tuple(row[k] for k in keys)),
        )
        return existing["rowid"] if "rowid" in existing.keys() else None
    cols = ",".join(row)
    return db.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' for _ in row)})", tuple(row.values())
    ).lastrowid


def validate_source(source, manifest, report):
    db = source.db
    for path, sha in manifest.get("evidence_sha256", {}).items():
        require(file_hash(Path(path)) == sha, f"Reviewed evidence changed: {path}")
    require(report["logical_sha256"] == manifest["logical_sha256"], "Source changed since review")
    for name in ("collections", "documents", "parsers"):
        require(report[name] == sorted(manifest[name]), f"Unreviewed {name}")
    require(bool(report["documents"]), "No accepted source documents")
    require(not BASELINE.intersection(report["collections"]), "Read-only baseline namespace")
    for field in (
        "parser_review",
        "public_source_review",
        "completeness_review",
        "clock_review",
        "fidelity_review",
        "representative_passage_review",
    ):
        require(bool(manifest.get(field)), f"Missing review: {field}")
    require(
        [r[0] for r in db.execute("PRAGMA quick_check")] == ["ok"],
        "Source SQLite failure",
    )
    require(not db.execute("PRAGMA foreign_key_check").fetchall(), "Source foreign key failure")
    require(
        not db.execute(
            "SELECT 1 FROM documents d WHERE NOT EXISTS "
            "(SELECT 1 FROM versions v WHERE v.document_id=d.id)"
        ).fetchone(),
        "Incomplete source document",
    )
    allowed = set(manifest["source_hosts"])
    for a in db.execute("SELECT * FROM acquisitions"):
        clock(a["observed_at"])
        for field in ("url", "final_url"):
            url = urlsplit(a[field])
            require(
                url.scheme == "https"
                and url.hostname in allowed
                and not url.username
                and not url.password,
                f"Unreviewed source boundary: {a[field]}",
            )
    for v in db.execute("SELECT * FROM versions"):
        a = db.execute("SELECT * FROM acquisitions WHERE id=?", (v["acquisition_id"],)).fetchone()
        require(
            200 <= a["status"] < 300 and not a["error"] and a["sha256"] == v["artifact_sha"],
            f"Invalid source receipt: {v['id']}",
        )
        require(clock(v["available_at"]) >= clock(a["observed_at"]), "Invalid availability clock")
        require(
            v["id"]
            == stable_id(
                v["document_id"],
                v["artifact_sha"],
                v["member"] or "",
                v["snapshot_date"] or "",
                v["parser"],
            ),
            "Version identity conflict",
        )
        require(bool(v["snapshot_basis"]), "Missing snapshot basis")
        require(
            db.execute("SELECT 1 FROM provisions WHERE version_id=?", (v["id"],)).fetchone(),
            "Empty source version",
        )
    for p in db.execute("SELECT id,version_id,key,citation,text,url FROM provisions"):
        require(p["id"] == stable_id(p["version_id"], p["key"]), "Provision identity conflict")
        require(all(p[k].strip() for k in ("key", "citation", "text", "url")), "Empty source unit")
    for f in db.execute("SELECT * FROM features"):
        require(f["id"] == stable_id(f["version_id"], f["source_key"]), "Feature identity conflict")
        a = db.execute("SELECT * FROM acquisitions WHERE id=?", (f["acquisition_id"],)).fetchone()
        require(
            200 <= a["status"] < 300 and a["sha256"] and not a["error"], "Invalid geometry receipt"
        )
    require(
        not db.execute(
            "SELECT 1 FROM features f LEFT JOIN feature_bounds b ON b.rowid=f.rowid "
            "WHERE b.rowid IS NULL"
        ).fetchone(),
        "Missing geometry bounds",
    )
    require(
        not db.execute(
            "SELECT 1 FROM feature_bounds b LEFT JOIN features f ON f.rowid=b.rowid "
            "WHERE f.rowid IS NULL"
        ).fetchone(),
        "Orphan geometry bounds",
    )


def counts(db):
    return {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}


def write_receipt(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def publish(source_root, target_root, manifest, ledger_root, *, min_free_bytes=100 * 1024**3):
    require(re.fullmatch(r"[a-zA-Z0-9_.-]+", manifest["batch_id"]), "Invalid batch ID")
    require(Path(source_root).resolve() != Path(target_root).resolve(), "Cannot import self")
    require(shutil.disk_usage(target_root).free >= min_free_bytes, "Free disk safety floor")
    ledger_root = Path(ledger_root)
    ledger_root.mkdir(parents=True, exist_ok=True)
    receipt_path = ledger_root / (manifest["batch_id"] + ".json")
    manifest_hash = hashlib.sha256(json_text(manifest).encode()).hexdigest()
    previous = None
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text())
        require(
            previous["manifest_sha256"] == manifest_hash, "Batch ID already bound to other manifest"
        )
    source = Store(Path(source_root), readonly=True)
    target = None
    receipt = {
        "batch_id": manifest["batch_id"],
        "manifest_sha256": manifest_hash,
        "source_store": str(source.root),
        "target_store": str(Path(target_root).resolve()),
        "started_at": utc_now(),
        "status": "validating",
        "reviewed_accounting": manifest.get("reviewed_accounting", {}),
        "publication_scope": manifest.get("public_source_review"),
    }
    try:
        source.db.execute("BEGIN")  # Stable snapshot; collectors must stop before review.
        report = inspect_store(source)
        receipt["source"] = report
        validate_source(source, manifest, report)
        # Opening readonly first avoids any schema initialization on the canonical catalog.
        target = Store(Path(target_root), readonly=True)
        validate_schema(target.db, legacy_baseline=True)
        receipt["target_schema_sha256"] = hashlib.sha256(
            json_text(schema(target.db)).encode()
        ).hexdigest()
        target.close()
        target = None
        db = sqlite3.connect(Path(target_root).resolve().joinpath("legal.sqlite3"))
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            db.execute("BEGIN IMMEDIATE")
            receipt["canonical_before"] = counts(db)
            for artifact in source.db.execute("SELECT * FROM artifacts"):
                sha, size = artifact
                src = source.object_path(sha)
                require(
                    src.stat().st_size == size and file_hash(src) == sha, f"Corrupt source {sha}"
                )
                dst = Path(target_root).resolve() / "objects" / sha[:2] / sha
                if dst.exists():
                    require(
                        dst.stat().st_size == size and file_hash(dst) == sha,
                        f"Corrupt target {sha}",
                    )
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.link(src, dst)  # Fail on cross-device; never copy large artifacts.
                    require(file_hash(dst) == sha, f"Linked artifact changed: {sha}")
                insert_exact(db, "artifacts", dict(artifact))
            mapping = {}
            used_receipts = set()
            for acquisition in source.db.execute("SELECT * FROM acquisitions ORDER BY id"):
                row = dict(acquisition)
                local_id = row.pop("id")
                matches = db.execute(
                    "SELECT id FROM acquisitions WHERE "
                    + " AND ".join(f"{k} IS ?" for k in row)
                    + " ORDER BY id",
                    tuple(row.values()),
                )
                same = next((r for r in matches if r[0] not in used_receipts), None)
                mapping[local_id] = (
                    same[0]
                    if same
                    else db.execute(
                        f"INSERT INTO acquisitions ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                        tuple(row.values()),
                    ).lastrowid
                )
                used_receipts.add(mapping[local_id])
            receipt["acquisition_id_map"] = mapping
            for table in (
                "jurisdictions",
                "collections",
                "inventories",
                "documents",
                "versions",
                "provisions",
                "legal_references",
                "features",
            ):
                feature_rows = {}
                # Jurisdiction parents may sort after children; defer their FKs until commit.
                db.execute("PRAGMA defer_foreign_keys=ON")
                for original in source.db.execute(f"SELECT * FROM {table}"):
                    row = dict(original)
                    local_rowid = row.pop("rowid", None)
                    if "acquisition_id" in row:
                        row["acquisition_id"] = mapping[row["acquisition_id"]]
                    for field in ("metadata",):
                        if field in row:
                            row[field] = remap_json(row[field], mapping)
                    new_rowid = insert_exact(db, table, row)
                    if table == "features":
                        feature_rows[local_rowid] = new_rowid
                if table == "features":
                    for original in source.db.execute("SELECT * FROM feature_bounds"):
                        row = dict(original)
                        row["rowid"] = feature_rows[row["rowid"]]
                        insert_exact(db, "feature_bounds", row)
            # Validate changed rows only; the baseline is deliberately not rescanned/rehashed.
            fts_samples = 0
            for version in source.db.execute("SELECT id FROM versions"):
                sample = db.execute(
                    "SELECT rowid,text FROM provisions WHERE version_id=? ORDER BY ordinal LIMIT 1",
                    (version[0],),
                ).fetchone()
                require(sample is not None, "Published version has no provision")
                word = re.search(r"[A-Za-z]{3,}", sample["text"])
                require(word is not None, "No searchable sample token; source review required")
                require(
                    db.execute(
                        "SELECT 1 FROM provision_search WHERE rowid=? AND provision_search MATCH ?",
                        (sample["rowid"], '"' + word[0] + '"'),
                    ).fetchone(),
                    "Published FTS sample missing",
                )
                fts_samples += 1
            receipt["canonical_after"] = counts(db)
            receipt["status"] = "prepared"
            receipt["checks"] = {
                "source_schema": "pass",
                "source_fk": "pass",
                "artifact_hashes": "pass",
                "exact_rows": "pass",
                "fts_versions_sampled": fts_samples,
                "geometry": "remapped bounds",
            }
            unchanged = receipt["canonical_before"] == receipt["canonical_after"]
            if previous and previous["status"] == "published":
                require(unchanged, "Previously published batch would change canonical rows")
            if previous and unchanged and previous["status"] in {"published", "prepared"}:
                require(
                    {str(k): v for k, v in mapping.items()} == previous["acquisition_id_map"],
                    "Previously published receipt mapping changed",
                )
                db.commit()
                # Preserve original deltas even if the process stopped just after COMMIT.
                if previous["status"] == "prepared":
                    previous.update(status="published", published_at=utc_now(), recovered=True)
                    write_receipt(receipt_path, previous)
                receipt.update(status="revalidated", original_publication=str(receipt_path))
                write_receipt(
                    ledger_root / f"{manifest['batch_id']}.revalidated-{uuid4().hex}.json", receipt
                )
            else:
                write_receipt(receipt_path, receipt)  # Prepared receipt supports crash recovery.
                db.commit()  # Deferred foreign keys are checked atomically here.
                receipt["status"] = "published"
                receipt["published_at"] = utc_now()
                write_receipt(receipt_path, receipt)
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
        return receipt
    except Exception as exc:
        receipt["status"] = "rejected"
        receipt["error"] = str(exc)
        write_receipt(ledger_root / (manifest["batch_id"] + ".rejected.json"), receipt)
        raise
    finally:
        source.close()
        if target is not None:
            target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--ledger", type=Path, default=Path("data/collectors/leads/publish"))
    args = parser.parse_args()
    if args.manifest:
        require(args.target is not None, "--target required for publication")
        result = publish(
            args.source, args.target, json.loads(args.manifest.read_text()), args.ledger
        )
    else:
        source = Store(args.source, readonly=True)
        try:
            source.db.execute("BEGIN")
            result = inspect_store(source)
        finally:
            source.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
