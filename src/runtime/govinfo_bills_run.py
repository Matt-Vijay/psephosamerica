"""Resumable, checkpointing driver for the full govinfo BILLSTATUS bill ingest.

The one-shot :func:`~src.runtime.govinfo_bills_materialize.materialize_govinfo_bill_corpus`
fetches everything then writes once -- fine for a bounded slice, wrong for the
~300K bills across congresses 113-119. This driver runs in **resumable batches**:

* It lists every bill file (cheap JSON listings) and derives each bill's canonical
  id from the *filename* alone -- so bills already in the on-disk corpus are
  skipped **without re-fetching** their XML.
* Each batch fetches at most ``max_fetch`` new bills, enriches only those (the
  graph + embeddings for an isolated bill are batch-independent), merges them
  into the existing corpus, rewrites ``records.jsonl`` + ``manifest.json``, and
  appends ``created`` deltas to ``deltas.jsonl``.

So a crash/restart re-reads the corpus and continues; re-running after completion
fetches nothing. The CLI loop calls :func:`ingest_batch` until no candidates
remain (or a wall-clock cap), checkpointing every batch.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.graph.contracts import EntityResolutionOutput
from src.graph.export import (
    DELTAS_FILENAME,
    RECORDS_FILENAME,
    read_contract_corpus,
    write_contract_corpus,
    write_delta_feed,
)
from src.graph.ingest.govinfo_billstatus import (
    billstatus_bill_row,
    canonical_bill_id_from_filename,
    parse_billstatus_xml,
)
from src.graph.regenerate import regenerate_corpus
from src.runtime.govinfo_bills_materialize import (
    BILLSTATUS_BILL_TYPES,
    _client,
    list_billstatus_file_urls,
)


@dataclass(frozen=True)
class Candidate:
    """A listed bill file and its filename-derived canonical id (pre-fetch)."""

    url: str
    canonical_id: str


def collect_candidates(
    congresses: Iterable[int],
    *,
    bill_types: Iterable[str] = BILLSTATUS_BILL_TYPES,
    client: httpx.Client | None = None,
) -> list[Candidate]:
    """List every bill file across congresses+types with its canonical id (no fetch)."""
    http, owns = _client(client)
    seen: set[str] = set()
    candidates: list[Candidate] = []
    try:
        for congress in congresses:
            for bill_type in bill_types:
                for url in list_billstatus_file_urls(congress, bill_type, client=http):
                    cid = canonical_bill_id_from_filename(url.rsplit("/", 1)[-1])
                    if cid is None or cid in seen:
                        continue
                    seen.add(cid)
                    candidates.append(Candidate(url=url, canonical_id=cid))
    finally:
        if owns:
            http.close()
    return candidates


@dataclass(frozen=True)
class BatchProgress:
    """The outcome of one resumable ingest batch."""

    existing_before: int
    candidates_total: int
    fetched_new: int
    parsed_new: int
    skipped: int
    remaining_after: int
    corpus_rows: int
    deltas_written: int


def _existing_rows(directory: Path) -> list[EntityResolutionOutput]:
    if (directory / RECORDS_FILENAME).exists():
        return read_contract_corpus(directory)
    return []


def ingest_batch(
    *,
    congresses: Iterable[int],
    directory: Path | str,
    as_of: datetime,
    bill_types: Iterable[str] = BILLSTATUS_BILL_TYPES,
    client: httpx.Client | None = None,
    first_observed_at: datetime | None = None,
    max_fetch: int = 500,
    candidates: list[Candidate] | None = None,
) -> BatchProgress:
    """Fetch+enrich up to ``max_fetch`` not-yet-ingested bills, then checkpoint.

    Pass ``candidates`` to reuse a prior listing (avoid re-listing each batch).
    """
    out_dir = Path(directory)
    observed = first_observed_at if first_observed_at is not None else datetime.now(UTC)
    http, owns = _client(client)
    try:
        all_candidates = (
            candidates
            if candidates is not None
            else collect_candidates(congresses, bill_types=bill_types, client=http)
        )
        existing = _existing_rows(out_dir)
        existing_ids = {row.canonical_id for row in existing}
        todo = [c for c in all_candidates if c.canonical_id not in existing_ids]

        new_rows: list[EntityResolutionOutput] = []
        fetched = parsed = skipped = 0
        for candidate in todo[:max_fetch]:
            try:
                resp = http.get(candidate.url, follow_redirects=True)
                resp.raise_for_status()
                fetched += 1
                xml = resp.text
                status = parse_billstatus_xml(xml)
                new_rows.append(
                    billstatus_bill_row(
                        status,
                        source_url=candidate.url,
                        content_sha256=hashlib.sha256(xml.encode("utf-8")).hexdigest(),
                        first_observed_at=observed,
                    )
                )
                parsed += 1
            except (httpx.HTTPError, ValueError):
                skipped += 1
    finally:
        if owns:
            http.close()

    # Only the freshly-fetched bills are enriched + diffed (todo already excludes
    # everything in the corpus), so the deltas are all `created` -- not a spurious
    # `removed` for every existing row (which passing the full prior would cause).
    result = regenerate_corpus(
        person_records=[],
        bill_outputs=new_rows,
        edges=[],
        as_of=as_of,
    )
    merged = [*existing, *result.rows]
    write_contract_corpus(merged, directory=out_dir, as_of=as_of)
    deltas_written = write_delta_feed(result.deltas, path=out_dir / DELTAS_FILENAME, append=True)
    return BatchProgress(
        existing_before=len(existing),
        candidates_total=len(all_candidates),
        fetched_new=fetched,
        parsed_new=parsed,
        skipped=skipped,
        remaining_after=len(todo) - parsed - skipped,
        corpus_rows=len(merged),
        deltas_written=deltas_written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Resumable govinfo BILLSTATUS bill ingest")
    parser.add_argument("--congresses", default="113-119", help="range a-b or comma list")
    parser.add_argument("--directory", default="data/exports/govinfo_bills")
    parser.add_argument("--bill-types", default=",".join(BILLSTATUS_BILL_TYPES))
    parser.add_argument("--max-fetch", type=int, default=400, help="bills per batch")
    parser.add_argument("--max-batches", type=int, default=100000)
    args = parser.parse_args(argv)

    if "-" in args.congresses:
        lo, hi = (int(x) for x in args.congresses.split("-", 1))
        congresses = list(range(lo, hi + 1))
    else:
        congresses = [int(x) for x in args.congresses.split(",") if x.strip()]
    bill_types = tuple(t.strip() for t in args.bill_types.split(",") if t.strip())
    directory = Path(args.directory)
    as_of = datetime.now(UTC)

    print(f"listing candidates for congresses {congresses} types {bill_types} ...", flush=True)
    candidates = collect_candidates(congresses, bill_types=bill_types)
    print(f"candidates listed: {len(candidates)}", flush=True)

    for batch in range(args.max_batches):
        progress = ingest_batch(
            congresses=congresses,
            directory=directory,
            as_of=as_of,
            bill_types=bill_types,
            first_observed_at=as_of,
            max_fetch=args.max_fetch,
            candidates=candidates,
        )
        print(
            f"batch {batch}: corpus={progress.corpus_rows} "
            f"+{progress.parsed_new} new (skipped {progress.skipped}) "
            f"remaining={progress.remaining_after}",
            flush=True,
        )
        if progress.fetched_new == 0:
            print(f"DONE: {progress.corpus_rows} bills in {directory}", flush=True)
            return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
