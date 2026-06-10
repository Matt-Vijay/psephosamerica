"""Merge the govinfo bill corpus into the MAIN contract corpus Track B watches.

The bill ingest writes a dedicated ``data/exports/govinfo_bills/`` corpus, but
Track B's hot-swap watcher reads ``data/exports/contract_records/`` -- a bill
that never lands there does not exist for inference. This merges the two:
person rows are preserved untouched, every bill canonical id present in the
govinfo corpus is taken from there (superseding the handful of pre-existing bill
rows), and the rest of the bills (if any) are kept. It diffs against the prior
main corpus to append a delta-CDC feed (``created`` for new bills, ``updated``
for superseded ones) so the watcher hot-swaps, then rewrites the corpus.

Pure read/merge/write over the two on-disk corpora; deterministic for a given
pair of inputs + ``as_of``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.graph.cdc import diff_outputs
from src.graph.export import read_contract_corpus, write_contract_corpus, write_delta_feed


@dataclass(frozen=True)
class MergeReport:
    """Counts from one merge of the bill corpus into the main corpus."""

    main_before: int
    bills_in: int
    persons: int
    bills_after: int
    merged_total: int
    deltas_written: int


def merge_bill_corpus_into_main(
    *,
    main_directory: Path | str,
    bills_directory: Path | str,
    as_of: datetime,
) -> MergeReport:
    """Fold the govinfo bill corpus into the main corpus + append delta-CDC."""
    main_dir = Path(main_directory)
    main_rows = read_contract_corpus(main_dir)
    bill_rows = read_contract_corpus(bills_directory)

    bill_ids = {row.canonical_id for row in bill_rows}
    # Keep every main row that the govinfo bills do not supersede (all persons +
    # any bill not in the govinfo set), then add the govinfo bills.
    kept = [row for row in main_rows if row.canonical_id not in bill_ids]
    merged = [*kept, *bill_rows]

    prior = {row.canonical_id: row for row in main_rows}
    curr = {row.canonical_id: row for row in merged}
    deltas = diff_outputs(prior, curr)

    write_contract_corpus(merged, directory=main_dir, as_of=as_of)
    deltas_written = write_delta_feed(deltas, path=main_dir / "deltas.jsonl", append=True)
    return MergeReport(
        main_before=len(main_rows),
        bills_in=len(bill_rows),
        persons=sum(1 for row in merged if row.entity_type == "person"),
        bills_after=sum(1 for row in merged if row.entity_type == "bill"),
        merged_total=len(merged),
        deltas_written=deltas_written,
    )
