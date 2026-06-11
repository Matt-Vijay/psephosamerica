"""Merge a person feed (materialized contract rows) into the MAIN corpus.

The municipal backfill writes each government's materialized council members to
``municipal_persons.jsonl`` — already :class:`EntityResolutionOutput` rows with
persistent canonical ids — so folding them into the watched corpus is an
upsert: add the unseen ids, never clobber existing rows (a person already in
the corpus may carry enrichment the feed lacks), and announce via delta-CDC.

Same race rule as every corpus writer: never run concurrently with another
corpus reader/writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.graph.cdc import diff_outputs
from src.graph.contracts import EntityResolutionOutput
from src.graph.export import (
    DELTAS_FILENAME,
    read_contract_corpus,
    write_contract_corpus,
    write_delta_feed,
)


@dataclass(frozen=True)
class PersonFeedMergeReport:
    """Counts from one feed merge."""

    feed_rows: int
    feed_unique: int
    persons_added: int
    corpus_total: int
    deltas_written: int


def load_person_feed(path: Path | str) -> dict[str, EntityResolutionOutput]:
    """Unique feed rows by canonical id (first occurrence wins)."""
    rows: dict[str, EntityResolutionOutput] = {}
    feed = Path(path)
    if not feed.exists():
        return rows
    with feed.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = EntityResolutionOutput.model_validate_json(line)
            rows.setdefault(row.canonical_id, row)
    return rows


def merge_person_feed_into_corpus(
    *,
    feed_path: Path | str,
    main_directory: Path | str,
    as_of: datetime | None = None,
) -> PersonFeedMergeReport:
    """Add the feed's unseen persons to the corpus + append delta-CDC."""
    main_dir = Path(main_directory)
    observed = as_of if as_of is not None else datetime.now(UTC)
    feed_rows_raw = 0
    feed = Path(feed_path)
    if feed.exists():
        with feed.open(encoding="utf-8") as handle:
            feed_rows_raw = sum(1 for line in handle if line.strip())
    unique = load_person_feed(feed_path)

    main_rows = read_contract_corpus(main_dir)
    existing = {row.canonical_id for row in main_rows}
    additions = [row for cid, row in sorted(unique.items()) if cid not in existing]

    merged = [*main_rows, *additions]
    prior = {row.canonical_id: row for row in main_rows}
    current = {row.canonical_id: row for row in merged}
    deltas = diff_outputs(prior, current)
    write_contract_corpus(merged, directory=main_dir, as_of=observed)
    deltas_written = write_delta_feed(deltas, path=main_dir / DELTAS_FILENAME, append=True)
    return PersonFeedMergeReport(
        feed_rows=feed_rows_raw,
        feed_unique=len(unique),
        persons_added=len(additions),
        corpus_total=len(merged),
        deltas_written=deltas_written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Merge a person feed into the corpus")
    parser.add_argument("--feed", default="data/exports/municipal/municipal_persons.jsonl")
    parser.add_argument("--corpus", default="data/exports/contract_records")
    args = parser.parse_args(argv)
    report = merge_person_feed_into_corpus(feed_path=args.feed, main_directory=args.corpus)
    print(
        f"feed={report.feed_unique}/{report.feed_rows} added={report.persons_added} "
        f"corpus={report.corpus_total} deltas={report.deltas_written}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
