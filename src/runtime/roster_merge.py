"""Merge the keyless congress-legislators roster into the MAIN contract corpus.

The CREC/GDELT/vote backfills link to people by bioguide -> canonical Person ID,
but the corpus only carried a current-House subset: 440 federal persons, most
with a *bioguide-id placeholder* display name (``M001199``) rather than a real
name. That broke two things at once -- GDELT name queries returned nothing for
placeholder names, and historical members of congresses 113-118 had no canonical
ID for floor speeches / roll-calls to attach to.

This runner closes both gaps from the keyless ``congress-legislators`` dataset
(``legislators-current.json`` + ``legislators-historical.json``):

* **Patch** every existing federal person whose bioguide appears in the roster
  with the member's real name (and union in their LIS id), keeping the same
  canonical id, source anchors, and all three embeddings untouched -- a surgical
  rename that immediately unblocks GDELT.
* **Add** every roster member absent from the corpus as a new ``pending`` person
  row (bioguide + LIS + real name + ``known_at`` at their earliest in-window term
  start), so CREC/Senate backfills can resolve 113-118 members to a canonical id.
  Their dense enrichment is filled by the next full :func:`regenerate_corpus`.

Both happen through the same upsert-by-canonical-id merge + delta-CDC append that
:func:`~src.runtime.bill_corpus_merge.merge_bill_corpus_into_main` uses, so Track
B's hot-swap watcher picks up the renamed / added people. Public-record only,
keyless, robots-respecting; no fabrication.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from src.graph.cdc import diff_outputs
from src.graph.contracts import EntityResolutionOutput
from src.graph.export import (
    DELTAS_FILENAME,
    read_contract_corpus,
    write_contract_corpus,
    write_delta_feed,
)
from src.graph.ingest.congress_legislators import DEFAULT_SINCE, parse_legislators
from src.graph.materialize import materialize_person_nodes
from src.runtime.http_client import client_or_default

_ROSTER_BASE = "https://unitedstates.github.io/congress-legislators"
ROSTER_FILES: tuple[str, ...] = ("legislators-current.json", "legislators-historical.json")


def roster_file_url(name: str) -> str:
    """The keyless GitHub-pages URL for one roster JSON file."""
    return f"{_ROSTER_BASE}/{name}"


def fetch_roster(
    *, client: httpx.Client | None = None, files: Iterable[str] = ROSTER_FILES
) -> list[dict[str, Any]]:
    """Fetch + concatenate the keyless roster JSON files into one member list."""
    http, owns = client_or_default(client)
    members: list[dict[str, Any]] = []
    try:
        for name in files:
            response = http.get(roster_file_url(name), follow_redirects=True)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                members.extend(item for item in payload if isinstance(item, dict))
    finally:
        if owns:
            http.close()
    return members


def _bioguide_key(external_ids: Iterable[str]) -> str | None:
    for ext in external_ids:
        if ext.startswith("bioguide:"):
            return ext
    return None


def _index_by_bioguide(
    nodes: Iterable[EntityResolutionOutput],
) -> dict[str, EntityResolutionOutput]:
    """Index person nodes by their ``bioguide:`` key (first wins; keyless skipped)."""
    by_key: dict[str, EntityResolutionOutput] = {}
    for node in nodes:
        key = _bioguide_key(node.external_ids)
        if key is not None:
            by_key.setdefault(key, node)
    return by_key


@dataclass(frozen=True)
class RosterMergeReport:
    """Counts from one roster merge into the main corpus."""

    roster_members: int
    main_before: int
    names_patched: int
    persons_added: int
    merged_total: int
    deltas_written: int


def merge_roster_into_corpus(
    *,
    main_directory: Path | str,
    roster_records: Iterable[dict[str, Any]],
    as_of: datetime,
    first_observed_at: datetime | None = None,
    since: date = DEFAULT_SINCE,
) -> RosterMergeReport:
    """Patch federal names + add missing members from the roster; append delta-CDC."""
    main_dir = Path(main_directory)
    observed = first_observed_at if first_observed_at is not None else datetime.now(UTC)
    main_rows = read_contract_corpus(main_dir)

    source_records = parse_legislators(roster_records, since=since, first_observed_at=observed)
    roster_nodes, _assignment = materialize_person_nodes(source_records)
    roster_by_bioguide = _index_by_bioguide(roster_nodes)

    out_rows: list[EntityResolutionOutput] = []
    matched_keys: set[str] = set()
    names_patched = 0
    for row in main_rows:
        key = _bioguide_key(row.external_ids)
        node = roster_by_bioguide.get(key) if key is not None else None
        if key is not None and node is not None:
            matched_keys.add(key)
            merged_ids = sorted(set(row.external_ids) | set(node.external_ids))
            if node.display_name != row.display_name or merged_ids != row.external_ids:
                row = row.model_copy(
                    update={"display_name": node.display_name, "external_ids": merged_ids}
                )
                names_patched += 1
        out_rows.append(row)

    existing_ids = {row.canonical_id for row in out_rows}
    persons_added = 0
    for key, node in roster_by_bioguide.items():
        if key not in matched_keys and node.canonical_id not in existing_ids:
            out_rows.append(node)
            existing_ids.add(node.canonical_id)
            persons_added += 1

    prior = {row.canonical_id: row for row in main_rows}
    curr = {row.canonical_id: row for row in out_rows}
    deltas = diff_outputs(prior, curr)
    write_contract_corpus(out_rows, directory=main_dir, as_of=as_of)
    deltas_written = write_delta_feed(deltas, path=main_dir / DELTAS_FILENAME, append=True)
    return RosterMergeReport(
        roster_members=len(roster_nodes),
        main_before=len(main_rows),
        names_patched=names_patched,
        persons_added=persons_added,
        merged_total=len(out_rows),
        deltas_written=deltas_written,
    )


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Merge congress-legislators roster into corpus")
    parser.add_argument("--corpus", default="data/exports/contract_records")
    args = parser.parse_args(argv)
    members = fetch_roster()
    report = merge_roster_into_corpus(
        main_directory=args.corpus, roster_records=members, as_of=datetime.now(UTC)
    )
    print(
        f"roster={report.roster_members} patched={report.names_patched} "
        f"added={report.persons_added} total={report.merged_total} "
        f"deltas={report.deltas_written}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
