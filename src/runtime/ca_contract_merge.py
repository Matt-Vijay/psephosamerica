"""Merge California bills + legislators into the MAIN contract corpus (v7 #3).

One pass per session-year zip, all over the partial-ZIP range reader (~4 MB
ranged reads from the 975 MB archive, never the whole file):

1. ``BILL_VERSION_TBL`` -> titled CA bill rows (same ``BillRef`` id scheme the
   CA roll-calls resolve to), densified through
   :func:`~src.graph.regenerate.regenerate_corpus` (deterministic dossier +
   hash embedding + structural embedding) before they enter the corpus.
2. ``LEGISLATOR_TBL`` -> person SourceRecords -> canonical persons via
   :func:`~src.graph.materialize.materialize_person_nodes`, added when absent
   (upsert-by-canonical-id; existing rows never clobbered).
3. A CA content sidecar row ``{canonical_id, text}`` per bill so the semantic
   re-embed can fill 384-d vectors for CA bills exactly like federal ones.

Everything lands in the watched corpus with delta-CDC appended, so Track B
hot-swaps the new state tier. Resumable: a bill/person already in the corpus
is skipped (bills by canonical id, persons by canonical id).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.graph.cdc import diff_outputs
from src.graph.contracts import EntityResolutionOutput
from src.graph.export import (
    DELTAS_FILENAME,
    read_contract_corpus,
    write_contract_corpus,
    write_delta_feed,
)
from src.graph.ingest.ca_leginfo import (
    ca_bill_row,
    parse_bill_titles,
    parse_ca_legislators,
)
from src.graph.materialize import materialize_person_nodes
from src.graph.regenerate import regenerate_corpus
from src.runtime.ca_leginfo_votes import LEGISLATOR_FILE, open_remote_zip, pubinfo_zip_url
from src.runtime.http_client import client_or_default

BILL_VERSION_FILE = "BILL_VERSION_TBL.dat"


def fetch_ca_tables(year: int, *, client: httpx.Client) -> tuple[str, str]:
    """Range-extract ``(BILL_VERSION_TBL, LEGISLATOR_TBL)`` text for a year."""
    with open_remote_zip(pubinfo_zip_url(year), client=client) as archive:
        with archive.open(BILL_VERSION_FILE) as versions:
            version_text = versions.read().decode("latin-1")
        with archive.open(LEGISLATOR_FILE) as legislators:
            legislator_text = legislators.read().decode("latin-1")
    return version_text, legislator_text


@dataclass(frozen=True)
class CaMergeReport:
    """Counts from one CA session-year merge."""

    bills_parsed: int
    bills_added: int
    persons_parsed: int
    persons_added: int
    sidecar_rows: int
    corpus_total: int
    deltas_written: int


def merge_ca_into_corpus(
    *,
    year: int,
    main_directory: Path | str,
    content_sidecar: Path | str,
    client: httpx.Client | None = None,
    as_of: datetime | None = None,
    max_bills: int | None = None,
) -> CaMergeReport:
    """Fetch one session-year's tables and fold CA into the watched corpus."""
    main_dir = Path(main_directory)
    observed = as_of if as_of is not None else datetime.now(UTC)
    source_url = pubinfo_zip_url(year)
    http, owns = client_or_default(client, timeout=120.0)
    try:
        version_text, legislator_text = fetch_ca_tables(year, client=http)
    finally:
        if owns:
            http.close()

    main_rows = read_contract_corpus(main_dir)
    existing_ids = {row.canonical_id for row in main_rows}

    # Bills: parse titles, keep the unseen, densify via the regenerate driver.
    titles = parse_bill_titles(version_text)
    pending_pairs = [
        (ca_bill_row(t, source_url=source_url, first_observed_at=observed), t) for t in titles
    ]
    new_pairs = [(row, t) for row, t in pending_pairs if row.canonical_id not in existing_ids]
    if max_bills is not None:
        new_pairs = new_pairs[:max_bills]
    new_bills = [row for row, _ in new_pairs]
    dense_bills: list[EntityResolutionOutput] = []
    if new_bills:
        result = regenerate_corpus(
            person_records=[], bill_outputs=new_bills, edges=[], as_of=observed
        )
        dense_bills = result.rows

    # Persons: materialize and add the unseen.
    person_records = parse_ca_legislators(
        legislator_text, source_url=source_url, first_observed_at=observed
    )
    person_nodes, _assignment = materialize_person_nodes(person_records)
    new_persons = [row for row in person_nodes if row.canonical_id not in existing_ids]

    merged = [*main_rows, *dense_bills, *new_persons]
    prior = {row.canonical_id: row for row in main_rows}
    current = {row.canonical_id: row for row in merged}
    deltas = diff_outputs(prior, current)
    write_contract_corpus(merged, directory=main_dir, as_of=observed)
    deltas_written = write_delta_feed(deltas, path=main_dir / DELTAS_FILENAME, append=True)

    # Sidecar: the embeddable text for every NEW bill (semantic re-embed input).
    sidecar = Path(content_sidecar)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar_rows = 0
    with sidecar.open("a", encoding="utf-8") as handle:
        for row, title in sorted(new_pairs, key=lambda pair: pair[0].canonical_id):
            handle.write(
                json.dumps(
                    {
                        "canonical_id": row.canonical_id,
                        "text": f"{title.measure}: {title.subject}",
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            sidecar_rows += 1

    return CaMergeReport(
        bills_parsed=len(titles),
        bills_added=len(dense_bills),
        persons_parsed=len(person_records),
        persons_added=len(new_persons),
        sidecar_rows=sidecar_rows,
        corpus_total=len(merged),
        deltas_written=deltas_written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Merge CA bills+legislators into the corpus")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--corpus", default="data/exports/contract_records")
    parser.add_argument("--sidecar", default="data/exports/govinfo_bills/bill_content_ca.jsonl")
    parser.add_argument("--max-bills", type=int, default=None)
    args = parser.parse_args(argv)
    report = merge_ca_into_corpus(
        year=args.year,
        main_directory=args.corpus,
        content_sidecar=args.sidecar,
        max_bills=args.max_bills,
    )
    print(
        f"year={args.year} bills+={report.bills_added}/{report.bills_parsed} "
        f"persons+={report.persons_added}/{report.persons_parsed} "
        f"sidecar+={report.sidecar_rows} corpus={report.corpus_total} "
        f"deltas={report.deltas_written}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
