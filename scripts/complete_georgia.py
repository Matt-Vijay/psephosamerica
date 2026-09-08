"""Resume the retained Georgia inventory with one writer and a 512 MiB lifetime campaign."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from psephos.acquire import AcquisitionError
from psephos.campaign import CampaignAcquirer
from psephos.campaign import save as save
from psephos.georgia_rules import (
    CLOCK_NOTE,
    COLLECTION,
    INDEX,
    MEDIA_NOTE,
    PARSER,
    inventory,
    pdf_projection,
)
from psephos.store import Store, json_text, utc_now

CAP = 512 * 1024**2
FILE_CAP = 128 * 1024**2


def old_state(s: Store, ids: list[str] | None = None) -> dict[str, Any]:
    versions = [
        dict(r)
        for r in s.db.execute(
            "SELECT v.* FROM versions v JOIN documents d ON d.id=v.document_id "
            "WHERE d.collection_id=? ORDER BY v.id",
            (COLLECTION,),
        )
        if ids is None or r["id"] in ids
    ]
    version_ids = [v["id"] for v in versions]
    rows = [
        r[0]
        for v in version_ids
        for r in s.db.execute("SELECT id FROM provisions WHERE version_id=? ORDER BY id", (v,))
    ]
    return {
        "version_ids": version_ids,
        "version_rows_sha256": hashlib.sha256(json_text(versions).encode()).hexdigest(),
        "provision_count": len(rows),
        "provision_ids_sha256": hashlib.sha256(json_text(rows).encode()).hexdigest(),
    }


class GeorgiaAcquirer(CampaignAcquirer):
    """Same HTTP engine, one persistent byte budget and host clock, no prohibited endpoints."""

    def __init__(self, store: Store, directory: Path, initial_bytes: int):
        super().__init__(store, directory, initial_bytes, cap=CAP, file_cap=FILE_CAP)

    def _allowed(self, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "rules.sos.ga.gov"
            or parsed.path not in {"/Download_pdf.aspx", "/robots.txt"}
        ):
            raise AcquisitionError("Only the reviewed Georgia download endpoint is in scope")
        super()._allowed(url)


def plan(s: Store, directory: Path, index_id: int | None, edition: str | None) -> dict[str, Any]:
    path = directory / "plan.json"
    if path.exists():
        result: dict[str, Any] = json.loads(path.read_bytes())
        if edition:
            if result.get("target_snapshot") not in (None, edition):
                raise ValueError("Do not silently change an established campaign edition")
            result["target_snapshot"] = edition
            save(path, result)
        return result
    if index_id is None:
        raise ValueError("First run requires --index-receipt from a <=16 MiB preflight")
    row = s.db.execute("SELECT * FROM acquisitions WHERE id=?", (index_id,)).fetchone()
    if not row or row["url"] != INDEX or row["status"] != 200 or not row["sha256"]:
        raise ValueError("A successful native department inventory receipt is required")
    data = s.artifact(row["sha256"])
    items = inventory(data)
    prior = [
        dict(r)
        for r in s.db.execute("SELECT * FROM inventories WHERE collection_id=?", (COLLECTION,))
    ]
    if {r["item"]: r["url"] for r in prior} != {r["department"]: r["url"] for r in items}:
        raise ValueError("Reviewed publisher inventory changed; explicit reconciliation required")
    result = {
        "created_at": utc_now(),
        "collection": COLLECTION,
        "index_receipt": index_id,
        "index_sha256": row["sha256"],
        "items": items,
        "old_state": old_state(s),
        "prior_inventory": prior,
        "initial_preflight_bytes": len(data),
        "cap_bytes": CAP,
        "first_acquisition_id": index_id,
        "target_snapshot": edition,
        "prior_collector_consumed_bytes": 198207232,
        "scope": "Complete retained publisher department inventory, not certified current operative law",
    }
    save(path, result)
    return result


def run(s: Store, directory: Path, state: dict[str, Any], acquire_only: bool) -> dict[str, Any]:
    a = GeorgiaAcquirer(s, directory, state["initial_preflight_bytes"])
    attempted = []
    errors = []
    try:
        for item in state["items"]:
            doc = "ga-rules:department-" + item["department"]
            existing = s.db.execute(
                "SELECT * FROM versions WHERE document_id=? ORDER BY snapshot_date DESC,available_at DESC,rowid DESC",
                (doc,),
            ).fetchall()
            target = state.get("target_snapshot")
            if existing and any(
                v["snapshot_date"] == target and v["parser"].startswith(PARSER + "/")
                for v in existing
            ):
                continue
            try:
                # Only older-edition URLs need one fresh representation. New campaign
                # receipts are reused even if indexing stopped; failed reads are not caches.
                latest = s.db.execute(
                    "SELECT max(id) FROM acquisitions WHERE url=? AND status=200 AND sha256 IS NOT NULL",
                    (item["url"],),
                ).fetchone()[0]
                a.refresh = bool(target and latest and latest < state["first_acquisition_id"])
                r = a.fetch(item["url"], max_file_bytes=FILE_CAP)
                if acquire_only:
                    print(
                        json_text(
                            {
                                "acquired": item["department"],
                                "bytes": r.size,
                                "consumed_bytes": a.downloaded,
                            }
                        ),
                        flush=True,
                    )
                    continue
                units, snapshot, metadata = pdf_projection(
                    s.object_path(r.sha256),
                    item,
                    directory / "ocr",
                    json.loads(
                        (
                            Path(__file__).resolve().parents[1] / "docs/georgia-media-review.json"
                        ).read_bytes()
                    ).get("media_only_pages", {}),
                )
                metadata["inventory_acquisition_id"] = state["index_receipt"]
                if not target or snapshot != target:
                    raise ValueError(
                        "Filing-through date differs from the retained edition; not silently mixed"
                    )
                version, count, new = s.ingest(
                    collection=COLLECTION,
                    document=doc,
                    title=item["title"],
                    url=item["url"],
                    acquisition=r.id,
                    snapshot_date=snapshot,
                    snapshot_basis="Cover filing-through date, not legal effectiveness",
                    parser=PARSER,
                    provisions=units,
                    metadata=metadata,
                )
                s.inventory(
                    COLLECTION,
                    item["department"],
                    item["url"],
                    "ingested_with_source_media"
                    if metadata["machine_ocr_pages"] or metadata["media_only_pages"]
                    else "ingested",
                )
                print(
                    json_text(
                        {
                            "department": item["department"],
                            "pages": count,
                            "ocr_pages": metadata["machine_ocr_pages"],
                            "version": version,
                            "new": new,
                            "consumed_bytes": a.downloaded,
                        }
                    ),
                    flush=True,
                )
                attempted.append(
                    {"department": item["department"], "version": version, "pages": count}
                )
            except (AcquisitionError, ValueError) as exc:
                errors.append({"department": item["department"], "error": str(exc)})
                s.inventory(
                    COLLECTION,
                    item["department"],
                    item["url"],
                    "partial" if existing else "blocked",
                    str(exc),
                )
                print(
                    json_text(
                        {
                            "department": item["department"],
                            "error": str(exc),
                            "consumed_bytes": a.downloaded,
                        }
                    ),
                    flush=True,
                )
                if isinstance(exc, AcquisitionError):
                    raise
        assert old_state(s, state["old_state"]["version_ids"]) == state["old_state"], (
            "Historical IDs/versions changed"
        )
        if errors:
            raise ValueError(
                f"{len(errors)} unresolved department(s); exact reasons retained in inventories"
            )
        if not acquire_only:
            row = s.db.execute(
                "SELECT metadata FROM collections WHERE id=?", (COLLECTION,)
            ).fetchone()
            metadata = json.loads(row[0])
            metadata.update(
                warning="Complete publisher-listed department PDF inventory at the stated filing-through date; not certified current operative law or complete machine-readable text.",
                clock_treatment=CLOCK_NOTE,
                media_treatment=MEDIA_NOTE,
                completion={
                    "department_count": len(state["items"]),
                    "filing_through": state["target_snapshot"],
                    "inventory_acquisition_id": state["index_receipt"],
                    "verification": "scripts/verify_georgia.py",
                },
            )
            with s.db:
                s.db.execute(
                    "UPDATE collections SET metadata=? WHERE id=?",
                    (json_text(metadata), COLLECTION),
                )
        return {
            "attempted": attempted,
            "campaign_payload_bytes": a.downloaded,
            "old_state_unchanged": True,
        }
    finally:
        a.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--index-receipt", type=int)
    parser.add_argument(
        "--edition",
        help="Exact cover filing-through date, established from retained publisher evidence",
    )
    parser.add_argument("--acquire-only", action="store_true")
    args = parser.parse_args()
    s = Store(args.data)
    directory = s.root / "georgia-completion"
    directory.mkdir(exist_ok=True)
    try:
        with (directory / "writer.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = plan(s, directory, args.index_receipt, args.edition)
            print(json_text(run(s, directory, state, args.acquire_only)), flush=True)
    finally:
        s.close()


if __name__ == "__main__":
    main()
