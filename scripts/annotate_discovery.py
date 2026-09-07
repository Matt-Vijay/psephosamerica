"""Apply two reviewed discovery notes to current collection metadata, never source history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from psephos.store import Store, digest, json_text, utc_now


def annotate(store: Store, patches: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    with store.db:
        for patch in patches:
            collection = patch["collection_id"]
            before = patch["before_metadata_json"]
            if digest(before.encode()) != patch["before_metadata_sha256"]:
                raise ValueError("Before-metadata receipt mismatch")
            additions = patch["metadata_addition"]
            if set(additions) != {"discovery_review"} or "discovery_review" in json.loads(before):
                raise ValueError("Only a new discovery_review annotation is allowed")
            after = json_text({**json.loads(before), **additions})
            for expected in patch["evidence"]["receipts"]:
                actual = store.db.execute(
                    "SELECT id,url,observed_at,sha256,status FROM acquisitions WHERE id=?",
                    (expected["id"],),
                ).fetchone()
                if actual is None or dict(actual) != expected:
                    raise ValueError(f"Source receipt changed for {collection}")
            current = store.db.execute(
                "SELECT metadata FROM collections WHERE id=?", (collection,)
            ).fetchone()
            if current is None or current[0] not in (before, after):
                raise ValueError(f"Current metadata differs; review before annotating {collection}")
            changed = current[0] == before
            if changed:
                cursor = store.db.execute(
                    "UPDATE collections SET metadata=? WHERE id=? AND metadata=?",
                    (after, collection, before),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Concurrent metadata change")
            results.append(
                {
                    "collection": collection,
                    "changed": changed,
                    "before_metadata_sha256": digest(before.encode()),
                    "after_metadata_sha256": digest(after.encode()),
                }
            )
    return {
        "status": "PASS",
        "recorded_at": utc_now(),
        "annotations": results,
        "scope": "Only current collections.metadata; no version/provision/inventory/artifact/acquisition/publication history rewritten",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "docs/discovery-annotations.json"
    patches = json.loads(path.read_bytes())
    store = Store(args.data)
    try:
        result = annotate(store, patches["annotations"])
        result["review_sha256"] = digest(path.read_bytes())
        print(json.dumps(result, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
