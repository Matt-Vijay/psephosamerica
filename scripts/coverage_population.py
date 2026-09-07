"""Reconcile the compact coverage ledger with three retained Census estimate CSVs.

Read-only by default. --patch emits an apply_patch edit; no acquisition or database writes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

SOURCES = {
    2: "92188e29cb0a67dcf95afa7d6c47359409782f086478b70ea4128eb70e223ca9",
    3: "29b57993b8ddadd786825930702b05cc15a26f6c1c7f000d32734580ffe464fb",
    4: "d053ba1f5ca4838aa89c6b155b3b7b8bc089df6a10f2dddb3ad3dccc5d24a8e3",
}


def calculate(data: Path, ledger: dict[str, Any]) -> dict[str, Any]:
    evidence = data / "population-evidence"
    db = sqlite3.connect(f"file:{evidence.resolve()}/legal.sqlite3?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    tables, receipts = {}, []
    try:
        for receipt_id, sha in SOURCES.items():
            row = db.execute("SELECT * FROM acquisitions WHERE id=?", (receipt_id,)).fetchone()
            raw = (evidence / "objects" / sha[:2] / sha).read_bytes()
            assert row["status"] == 200 and not row["error"] and row["sha256"] == sha
            assert hashlib.sha256(raw).hexdigest() == sha
            tables[receipt_id] = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
            receipts.append(
                {
                    **{k: row[k] for k in ("id", "url", "sha256", "observed_at")},
                    "bytes": len(raw),
                    "rows": len(tables[receipt_id]),
                }
            )
    finally:
        db.close()
    db = sqlite3.connect(f"file:{data.resolve()}/legal.sqlite3?mode=ro", uri=True)
    try:
        states = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT f.properties FROM features f JOIN versions v ON v.id=f.version_id WHERE v.document_id='census:2025/tl_2025_us_state.zip'"
            )
        ]
    finally:
        db.close()
    state_ids = {s["STATEFP"]: "us-" + s["STUSPS"].lower() for s in states}
    national = [r for r in tables[2] if r["SUMLEV"] == "010" and r["STATE"] == "00"]
    selected = [r for r in tables[2] if r["SUMLEV"] == "040" and r["STATE"] != "72"]
    assert len(national) == 1 and len(selected) == 51
    population = {state_ids[r["STATE"]]: int(r["POPESTIMATE2025"]) for r in selected}
    assert (
        len(population) == 51
        and sum(population.values()) == int(national[0]["POPESTIMATE2025"]) == 341784857
    )
    entities = {state_ids[r["STATE"]]: r["STATE"] for r in selected}
    for receipt_id, state, place, jurisdiction in (
        (3, "36", "51000", "us-ny-nyc"),
        (4, "41", "59000", "us-or-portland"),
    ):
        city = [
            r
            for r in tables[receipt_id]
            if r["SUMLEV"] == "162"
            and r["STATE"] == state
            and r["PLACE"] == place
            and r["COUNTY"] == "000"
            and r["COUSUB"] == "00000"
        ]
        assert len(city) == 1
        population[jurisdiction] = int(city[0]["POPESTIMATE2025"])
        entities[jurisdiction] = state + place
    population["us"] = 341784857
    rows = ledger["layers"]
    assert len({(r["jurisdiction_id"], r["family"]) for r in rows}) == len(rows)
    metrics: dict[str, Any] = {}
    for label, family, scope in (
        ("federal_statutes", "statutes", "federal"),
        ("federal_regulations", "regulations", "federal"),
        ("state_statutes", "statutes", "state"),
        ("state_regulations", "regulations", "state"),
        ("local_code", "municipal_code", "local"),
        ("local_zoning_text", "zoning_text", "local"),
    ):
        group = [
            r
            for r in rows
            if r["family"] == family
            and (
                r["jurisdiction_id"] == "us"
                if scope == "federal"
                else len(r["jurisdiction_id"]) == 5
                if scope == "state"
                else len(r["jurisdiction_id"]) > 5
            )
        ]
        metrics[label] = {}
        buckets: dict[str, set[str]] = {
            "any_retained_body": set(),
            "closed_publisher_inventory": set(),
            "verified_complete_current_layer": set(),
        }
        for row in group:
            jurisdiction = row["jurisdiction_id"]
            if row["collection_ids"] and row["status"] not in {"absent", "blocked"}:
                buckets["any_retained_body"].add(jurisdiction)
            if row["status"] == "complete_publisher_edition":
                buckets["closed_publisher_inventory"].add(jurisdiction)
            if row["layer_complete"]:
                buckets["verified_complete_current_layer"].add(jurisdiction)
        for metric, jurisdictions in buckets.items():
            ids = sorted(jurisdictions)
            people = sum(population[j] for j in ids)
            metrics[label][metric] = {
                "jurisdiction_ids": ids,
                "people": people,
                "percent_of_us": round(100 * people / population["us"], 4),
            }
    return {
        "vintage": "2025-07-01",
        "field": "POPESTIMATE2025",
        "denominator": population["us"],
        "denominator_scope": "50 states plus DC; national SUMLEV010 equals 51 non-PR SUMLEV040 rows. Regions and Puerto Rico excluded from sums.",
        "source_store": "data/population-evidence",
        "receipts": receipts,
        "entity_population": {
            j: {"geoid": entities[j], "people": population[j]} for j in sorted(entities)
        },
        "metrics": metrics,
        "semantics": "Population living in a source jurisdiction, not laws per person or a legal-applicability finding. Each entity is counted once within a layer; never add layers or overlapping geography. Closed publisher inventories may be old/narrowed or have projection exclusions.",
        "geometry_vintage": "2025-01-01",
        "identity_basis": "Exact retained Census STATEFP/STUSPS crosswalk and whole-place GEOIDs 3651000/4159000. July estimates and January geometry are separate vintages; no overlay population is inferred.",
        "reproduce": "python scripts/coverage_population.py --data data --check",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--ledger", type=Path, default=Path("docs/coverage-ledger.json"))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--patch", action="store_true")
    args = parser.parse_args()
    original = args.ledger.read_text()
    ledger = json.loads(original)
    result = calculate(args.data, ledger)
    if args.check:
        assert ledger["population"] == result, (
            "Population ledger differs from retained inputs/classifications"
        )
        print("PASS: population identities, denominator, deduplication and layer metrics")
    elif args.patch:
        start, end = (
            original.index('  "population":'),
            original.index('  "fully_verified_combined_coverage":'),
        )
        replacement = (
            '  "population": ' + json.dumps(result, indent=2).replace("\n", "\n  ") + ",\n"
        )
        print("*** Begin Patch\n*** Update File: " + str(args.ledger.resolve()) + "\n@@")
        print("".join("-" + line + "\n" for line in original[start:end].splitlines()), end="")
        print("".join("+" + line + "\n" for line in replacement.splitlines()), end="")
        print("*** End Patch")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
