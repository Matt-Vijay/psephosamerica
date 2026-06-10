"""Resumable sector sidecar: a CRS policy area + subjects for every bill.

The bill ingest embeds bills but did not persist their CRS classification, so
Track B's defection slice still leans on the ~10%-coverage clerk ``vote-desc``
sectors. This runner re-reads the keyless BILLSTATUS feed -- *parse only*, no
enrichment, so it is far lighter than the full ingest -- and writes one
``{canonical_id, policy_area, subjects}`` record per bill to a JSONL sidecar
keyed by the same ``cb-<digest>`` the votes resolve to. Track B joins it to give
**every** bill a real sector.

Resumable + checkpointing like :mod:`src.runtime.govinfo_bills_run`: bills
already in the sidecar are skipped (by filename-derived canonical id, no fetch),
each batch appends up to ``max_fetch`` new records, a crash/restart continues.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

from src.graph.ingest.govinfo_billstatus import canonical_bill_id, parse_billstatus_xml
from src.runtime.govinfo_bills_materialize import BILLSTATUS_BILL_TYPES, _client
from src.runtime.govinfo_bills_run import Candidate, collect_candidates


def bill_sector_record(xml: str) -> dict[str, object]:
    """Parse a BILLSTATUS doc into a sidecar sector record."""
    status = parse_billstatus_xml(xml)
    return {
        "canonical_id": canonical_bill_id(status),
        "policy_area": status.policy_area,
        "subjects": list(status.subjects),
    }


def existing_sector_ids(path: Path) -> set[str]:
    """Canonical ids already present in the sidecar (for resumable skipping)."""
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ids.add(str(json.loads(line)["canonical_id"]))
    return ids


@dataclass(frozen=True)
class SectorProgress:
    """The outcome of one sector-sidecar batch."""

    existing_before: int
    candidates_total: int
    written_new: int
    skipped: int
    remaining_after: int
    sidecar_rows: int


def build_sector_sidecar_batch(
    congresses: Iterable[int],
    *,
    path: Path | str,
    bill_types: Iterable[str] = BILLSTATUS_BILL_TYPES,
    client: httpx.Client | None = None,
    max_fetch: int = 1000,
    candidates: list[Candidate] | None = None,
) -> SectorProgress:
    """Fetch+append up to ``max_fetch`` not-yet-recorded bill sector records."""
    sidecar = Path(path)
    http, owns = _client(client)
    written = skipped = 0
    todo_len = 0
    try:
        all_candidates = (
            candidates
            if candidates is not None
            else collect_candidates(congresses, bill_types=bill_types, client=http)
        )
        existing = existing_sector_ids(sidecar)
        before = len(existing)
        todo = [c for c in all_candidates if c.canonical_id not in existing]
        todo_len = len(todo)

        sidecar.parent.mkdir(parents=True, exist_ok=True)
        with sidecar.open("a", encoding="utf-8") as handle:
            for candidate in todo[:max_fetch]:
                try:
                    resp = http.get(candidate.url, follow_redirects=True)
                    resp.raise_for_status()
                    record = bill_sector_record(resp.text)
                    handle.write(json.dumps(record) + "\n")
                    written += 1
                except (httpx.HTTPError, ValueError):
                    skipped += 1
    finally:
        if owns:
            http.close()
    return SectorProgress(
        existing_before=before,
        candidates_total=len(all_candidates),
        written_new=written,
        skipped=skipped,
        remaining_after=todo_len - written - skipped,
        sidecar_rows=before + written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Resumable govinfo bill sector sidecar")
    parser.add_argument("--congresses", default="113-119")
    parser.add_argument("--path", default="data/exports/govinfo_bills/bill_sectors.jsonl")
    parser.add_argument("--bill-types", default=",".join(BILLSTATUS_BILL_TYPES))
    parser.add_argument("--max-fetch", type=int, default=1000)
    parser.add_argument("--max-batches", type=int, default=100000)
    args = parser.parse_args(argv)

    if "-" in args.congresses:
        lo, hi = (int(x) for x in args.congresses.split("-", 1))
        congresses = list(range(lo, hi + 1))
    else:
        congresses = [int(x) for x in args.congresses.split(",") if x.strip()]
    bill_types = tuple(t.strip() for t in args.bill_types.split(",") if t.strip())

    print(f"listing candidates for {congresses} ...", flush=True)
    candidates = collect_candidates(congresses, bill_types=bill_types)
    print(f"candidates: {len(candidates)}", flush=True)
    for batch in range(args.max_batches):
        progress = build_sector_sidecar_batch(
            congresses,
            path=args.path,
            bill_types=bill_types,
            max_fetch=args.max_fetch,
            candidates=candidates,
        )
        print(
            f"batch {batch}: sidecar={progress.sidecar_rows} +{progress.written_new} "
            f"(skipped {progress.skipped}) remaining={progress.remaining_after}",
            flush=True,
        )
        if progress.written_new == 0 and progress.skipped == 0:
            print(f"DONE: {progress.sidecar_rows} sector records in {args.path}", flush=True)
            return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
