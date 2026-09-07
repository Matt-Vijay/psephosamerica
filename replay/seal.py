"""Maintainer-only recipe sealing from the already accepted, read-only source stores.

Replay itself needs neither these databases nor this command. Recipes contain
receipts, parser inputs and expected field hashes, never provision text/markup.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from replay import CORE_REVISION, HERE, REPO, digest, encoded, file_hash, require, unit_fingerprint

sys.path.insert(0, str(REPO / "scripts"))
from import_collector import (  # type: ignore[import-not-found]  # noqa: E402
    inspect_store,
    remap_json,
)

from psephos.store import Provision, Reference, Store  # noqa: E402

FAMILIES = {
    "fl": "south-fl-20260907-01",
    "nj": "northeast-nj-20260907-01",
    "ms": "south-ms-20260907-01",
}
MEMBERS = {
    "nj-statutes": [
        {
            "name": "STATUTES.TXT",
            "bytes": 83882972,
            "sha256": "f08f83cba812ee3389faada1649e4204f88c91242a141dd14e1e79ad6d4a1e6c",
        },
        {
            "name": "STATUTES.RTF",
            "bytes": 94415499,
            "sha256": "5fbc18d5b9e41b5144bd4aebf023abd4c472c638d6a950af362d8450103c9ccf",
        },
    ],
    "nj-constitution": [
        {
            "name": "NJCONST.TXT",
            "bytes": 177316,
            "sha256": "11da0e66a7691306c17a2835256346a4e7a60856efb8d5f62069bc4edcd29491",
        },
    ],
}


def seal(family: str, output: Path, profiles: dict[str, Any]) -> dict[str, Any]:
    catalog = json.loads((HERE / "catalog.json").read_bytes())
    batch = next(b for b in catalog["batches"] if b["batch_id"] == FAMILIES[family])
    acceptance = REPO / batch["acceptance_manifest"]["path"]
    require(file_hash(acceptance) == batch["acceptance_manifest"]["sha256"], "Acceptance changed")
    accepted = json.loads(acceptance.read_bytes())
    ledger = REPO / "data/collectors/leads/publish" / f"{batch['batch_id']}.json"
    published = json.loads(ledger.read_bytes())
    require(file_hash(ledger) == batch["publication_ledger_sha256"], "Publication ledger changed")
    require(
        published["batch_id"] == batch["batch_id"] and published["status"] == "published",
        "Not a completed publication ledger",
    )
    require(
        published["source"]["logical_sha256"] == accepted["logical_sha256"],
        "Publication source mismatch",
    )
    mapping = {int(k): v for k, v in published["acquisition_id_map"].items()}
    store = Store(REPO / batch["source_store"], readonly=True)
    try:
        source = inspect_store(store)
        require(
            source["logical_sha256"] == accepted["logical_sha256"], "Accepted source rows changed"
        )
        require(source["documents"] == sorted(accepted["documents"]), "Unaccepted document scope")
        require(source["parsers"] == sorted(accepted["parsers"]), "Unaccepted parser scope")

        def metadata(raw: str) -> str:
            return cast(str, remap_json(raw, mapping))

        recipe: dict[str, Any] = {
            "schema_version": 1,
            "family": family,
            "core_revision": CORE_REVISION,
            "batch_id": batch["batch_id"],
            "acceptance_manifest_sha256": file_hash(acceptance),
            "publication_ledger_sha256": file_hash(ledger),
            "accepted_source_logical_sha256": source["logical_sha256"],
            "receipt_ids": "Original publication-ledger canonical IDs, including nested metadata references",
            "header_redactions": [],
            "documents": [],
        }
        for table in ("artifacts", "acquisitions", "jurisdictions", "collections"):
            order = "sha256" if table == "artifacts" else "id"
            rows = [dict(r) for r in store.db.execute(f"SELECT * FROM {table} ORDER BY {order}")]
            for row in rows:
                if table == "acquisitions":
                    row["id"] = mapping[row["id"]]
                    headers = json.loads(row["headers"])
                    private = [
                        k for k in headers if "cookie" in k.lower() or "authorization" in k.lower()
                    ]
                    if private:
                        recipe["header_redactions"].append(
                            {
                                "acquisition_id": row["id"],
                                "keys": private,
                                "original_headers_sha256": digest(row["headers"].encode()),
                            }
                        )
                        row["headers"] = encoded(
                            {k: v for k, v in headers.items() if k not in private}
                        ).decode()
                if table == "collections":
                    row["metadata"] = metadata(row["metadata"])
            recipe[table] = rows
        # Parent jurisdictions must precede children for SQLite foreign keys.
        recipe["jurisdictions"].sort(key=lambda row: (row["parent_id"] is not None, row["id"]))
        if family == "ms":
            review = REPO / "data/collectors/mississippi/checks/sparse_review.json"
            recipe["review"] = {
                "sha256": file_hash(review),
                "bytes": review.stat().st_size,
                "role": "Accepted sparse-page visual review; data only, never executable",
            }
            require(
                recipe["review"]["sha256"]
                == "a70000966331e4e2d9af15fb6de14f457e958f2393050631f590ed5807f5f249",
                "Accepted sparse-page review changed",
            )
        for version_row in store.db.execute("SELECT * FROM versions ORDER BY document_id,id"):
            version = dict(version_row)
            doc = dict(
                store.db.execute(
                    "SELECT * FROM documents WHERE id=?", (version["document_id"],)
                ).fetchone()
            )
            units = []
            for row in store.db.execute(
                "SELECT * FROM provisions WHERE version_id=? ORDER BY ordinal", (version["id"],)
            ):
                refs = tuple(
                    Reference(*r)
                    for r in store.db.execute(
                        "SELECT target,relation,label,evidence FROM legal_references WHERE provision_id=? ORDER BY target,relation,label",
                        (row["id"],),
                    )
                )
                units.append(
                    Provision(
                        **{
                            key: row[key]
                            for key in (
                                "key",
                                "citation",
                                "heading",
                                "text",
                                "markup",
                                "url",
                                "parent_key",
                                "unit_kind",
                            )
                        },
                        metadata=json.loads(metadata(row["metadata"])),
                        references=refs,
                    )
                )
            original_metadata = json.loads(version.pop("metadata"))
            version_metadata = json.loads(metadata(encoded(original_metadata).decode()))
            version["acquisition_id"] = mapping[version["acquisition_id"]]
            item = {
                "document": doc,
                "version": version,
                "expected_units": list(unit_fingerprint(units)),
                "version_metadata_sha256": digest(encoded(version_metadata)),
            }
            if family == "ms":
                item.update(
                    inventory_receipt=version_metadata["inventory_receipt"],
                    snapshot_profile=profiles[version["artifact_sha"]],
                )
            else:
                item["version_metadata"] = version_metadata
            if family == "nj":
                item["members"] = MEMBERS[doc["collection_id"]]
            require(
                version["repealed_on"] is None, "Historical ingest cannot restore a repeal override"
            )
            recipe["documents"].append(item)
        # No source text, native PDF annotation dumps, local paths or credential headers.
        raw = encoded(recipe) + b"\n"
        require(
            b"/Users/" not in raw and b"/private/tmp/" not in raw,
            "Local path leaked into portable recipe",
        )
        output.mkdir(parents=True, exist_ok=True)
        destination = output / f"{family}.json"
        with destination.open("xb") as stream:
            stream.write(raw)
        return {
            "family": family,
            "documents": len(recipe["documents"]),
            "bytes": len(raw),
            "sha256": digest(raw),
        }
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="New recipe directory; existing recipes are never overwritten",
    )
    parser.add_argument(
        "--ms-profiles",
        type=Path,
        help="Optional reviewed replacement profiles; defaults to the existing sealed MS recipe",
    )
    args = parser.parse_args()
    profiles = (
        json.loads(args.ms_profiles.read_bytes())
        if args.ms_profiles
        else {
            item["version"]["artifact_sha"]: item["snapshot_profile"]
            for item in json.loads((HERE / "recipes/ms.json").read_bytes())["documents"]
        }
    )
    print(json.dumps([seal(family, args.out, profiles) for family in FAMILIES], indent=2))


if __name__ == "__main__":
    main()
