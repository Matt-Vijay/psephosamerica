"""Run the Senate LDA lobbying ingest: org corpus + lobbying edges + CDC + meta.

The runner for the lobbying deliverable. It drains a stream of Senate LDA filing
records (the keyless ``lda.senate.gov/api/v1/filings/`` REST API, which is also
the bulk source), runs each through the pure adapter in
:mod:`src.graph.ingest.lda`, resolves the lobbying **registrants** (firms) and
**clients** (the entities that hired them) into canonical org entities, and
writes four artifacts under an output directory:

* a contract corpus (``records.jsonl`` + ``manifest.json``) of the resolved
  registrant + client **org** rows;
* a ``deltas.jsonl`` CDC feed (every org ``created`` on first ingest);
* a ``lobbying_edges.jsonl`` sidecar — a ``lobbying_retention`` client -> registrant
  edge per filing, plus ``lobbying_contact`` client -> bill edges for every
  congress bill named in the filing's lobbying activities (the bills/agencies
  lobbied, the deliverable's link into the bill graph);
* an ``ingest_meta.json`` honest-cap record.

Disclosure-lag leakage: a filing is public when posted (``dt_posted``), so every
edge's ``known_at`` is the post time. Honest cap: the LDA corpus is large
(hundreds of thousands of filings); ``max_filings`` bounds a representative slice
recorded in the meta. Network/pagination I/O is isolated in
:func:`iter_filing_records` (``pragma: no cover``); :func:`build_lobbying_graph`
is pure and unit-tested from in-memory streams.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.graph.cdc import diff_outputs
from src.graph.contracts import EntityResolutionOutput, build_entity_resolution_output
from src.graph.edges import GraphEdge
from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.entity_resolution.records import SourceRecord
from src.graph.export import (
    DELTAS_FILENAME,
    write_contract_corpus,
    write_delta_feed,
)
from src.graph.ingest.lda import (
    LdaFiling,
    lda_bill_lobbying_edges,
    lda_provenance,
    lda_retention_edge,
    parse_lda_activities,
    parse_lda_client,
    parse_lda_filing,
    parse_lda_registrant,
)
from src.graph.provenance import ProvenanceEnvelope

LDA_API_URL = "https://lda.senate.gov/api/v1"
LDA_FILINGS_URL = f"{LDA_API_URL}/filings/"
LOBBYING_EDGES_FILENAME = "lobbying_edges.jsonl"
INGEST_META_FILENAME = "ingest_meta.json"


@dataclass(frozen=True)
class LdaIngestReport:
    """Counts + provenance from one LDA ingest run (in ingest_meta.json)."""

    api_url: str
    as_of: str
    rows_scanned: int
    rows_skipped: int
    filings: int
    registrants: int
    clients: int
    retention_edges: int
    bill_lobbying_edges: int
    deltas_written: int
    is_full_corpus: bool


@dataclass(frozen=True)
class _ParsedFiling:
    """One filing's parsed orgs + provenance + activities, ready for resolution."""

    filing: LdaFiling
    registrant: SourceRecord
    client: SourceRecord
    provenance: ProvenanceEnvelope
    activities: list[Any]


def _filing_url(filing: LdaFiling) -> str:
    return f"{LDA_FILINGS_URL}{filing.filing_uuid}/"


def _parse_one(record: dict[str, Any], *, first_observed_at: datetime) -> _ParsedFiling | None:
    try:
        filing = parse_lda_filing(record)
    except ValueError:
        return None
    if not filing.client_id or not filing.registrant_id:
        return None
    provenance = lda_provenance(
        filing,
        source_url=_filing_url(filing),
        content_sha256=_filing_sha(filing),
        first_observed_at=first_observed_at,
    )
    try:
        registrant = parse_lda_registrant(record.get("registrant") or {}, provenance=provenance)
        client = parse_lda_client(record.get("client") or {}, provenance=provenance)
    except ValueError:
        return None
    return _ParsedFiling(
        filing=filing,
        registrant=registrant,
        client=client,
        provenance=provenance,
        activities=parse_lda_activities(record),
    )


def _filing_sha(filing: LdaFiling) -> str:
    import hashlib

    payload = f"{filing.filing_uuid}|{filing.year}|{filing.income or ''}|{filing.expenses or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_lobbying_graph(
    records: Iterable[dict[str, Any]],
    *,
    first_observed_at: datetime,
) -> tuple[list[EntityResolutionOutput], list[GraphEdge], int, int]:
    """Drain LDA filings into (org rows, lobbying edges, scanned, skipped).

    Registrants and clients are resolved with the entity-resolution linker (strong
    LDA-id blocking collapses the same firm/client across filings), then projected
    to contract org rows. Each filing yields a ``lobbying_retention`` client ->
    registrant edge plus ``lobbying_contact`` client -> bill edges for the congress
    bills named in its activities. Rows and edges de-dupe by identity.
    """
    parsed: list[_ParsedFiling] = []
    scanned = skipped = 0
    for record in records:
        scanned += 1
        one = _parse_one(record, first_observed_at=first_observed_at)
        if one is None:
            skipped += 1
            continue
        parsed.append(one)

    records_by_id: dict[str, SourceRecord] = {}
    for item in parsed:
        records_by_id[item.registrant.record_id] = item.registrant
        records_by_id[item.client.record_id] = item.client
    result = resolve(records_by_id.values())
    canonical_by_source: dict[str, str] = {}
    rows_by_id: dict[str, EntityResolutionOutput] = {}
    for cluster in result.clusters:
        entity = build_canonical_entity(cluster, records_by_id)
        if entity is None:
            continue
        row = build_entity_resolution_output(entity)
        rows_by_id[row.canonical_id] = row
        for source_id in cluster.record_ids:
            canonical_by_source[source_id] = entity.canonical_id

    edges_by_id: dict[str, GraphEdge] = {}
    for item in parsed:
        client_cid = canonical_by_source[item.client.record_id]
        registrant_cid = canonical_by_source[item.registrant.record_id]
        if client_cid != registrant_cid:
            retention = lda_retention_edge(
                client_canonical_id=client_cid,
                registrant_canonical_id=registrant_cid,
                filing=item.filing,
                provenance=item.provenance,
            )
            edges_by_id[retention.edge_id] = retention
        for edge in lda_bill_lobbying_edges(
            client_canonical_id=client_cid,
            filing=item.filing,
            activities=item.activities,
            provenance=item.provenance,
            registrant_name=item.registrant.display_name,
        ):
            edges_by_id[edge.edge_id] = edge

    return list(rows_by_id.values()), list(edges_by_id.values()), scanned, skipped


def iter_filing_records(
    *,
    max_filings: int | None,
    filing_year: int | None = None,
    client: Any | None = None,
) -> Iterator[dict[str, Any]]:  # pragma: no cover - network + pagination I/O
    """Stream LDA filing rows from the keyless Senate LDA REST API."""
    import httpx

    http = client if client is not None else httpx.Client(timeout=60.0)
    owns = client is None
    yielded = 0
    params: dict[str, Any] = {"page_size": 100}
    if filing_year is not None:
        params["filing_year"] = filing_year
    url: str | None = LDA_FILINGS_URL
    try:
        while url is not None:
            if max_filings is not None and yielded >= max_filings:
                return
            resp = http.get(url, params=params if url == LDA_FILINGS_URL else None)
            if resp.status_code != 200:
                break
            body = resp.json()
            for row in body.get("results", []):
                if max_filings is not None and yielded >= max_filings:
                    return
                yield row
                yielded += 1
            url = body.get("next")
    finally:
        if owns:
            http.close()


def export_lda(
    *,
    out_directory: Path | str,
    max_filings: int | None = None,
    filing_year: int | None = None,
    as_of: datetime | None = None,
    client: Any | None = None,
) -> LdaIngestReport:  # pragma: no cover - orchestrates network I/O
    """Run the full LDA ingest: org corpus + lobbying edges + CDC + meta."""
    out_dir = Path(out_directory)
    observed = as_of if as_of is not None else datetime.now(tz=UTC)
    rows, edges, scanned, skipped = build_lobbying_graph(
        iter_filing_records(max_filings=max_filings, filing_year=filing_year, client=client),
        first_observed_at=observed,
    )

    write_contract_corpus(rows, directory=out_dir, as_of=observed)
    deltas = diff_outputs({}, {row.canonical_id: row for row in rows})
    deltas_written = write_delta_feed(deltas, path=out_dir / DELTAS_FILENAME, append=False)

    edge_path = out_dir / LOBBYING_EDGES_FILENAME
    with edge_path.open("w", encoding="utf-8") as handle:
        for edge in sorted(edges, key=lambda e: e.edge_id):
            handle.write(edge.model_dump_json() + "\n")

    registrants = sum(
        1 for r in rows if any(k.startswith("senate_lda_registrant:") for k in r.external_ids)
    )
    clients = sum(
        1 for r in rows if any(k.startswith("senate_lda_client:") for k in r.external_ids)
    )
    retention = sum(1 for e in edges if e.edge_type == "lobbying_retention")
    contacts = sum(1 for e in edges if e.edge_type == "lobbying_contact")
    report = LdaIngestReport(
        api_url=LDA_API_URL,
        as_of=observed.isoformat(),
        rows_scanned=scanned,
        rows_skipped=skipped,
        filings=retention,
        registrants=registrants,
        clients=clients,
        retention_edges=retention,
        bill_lobbying_edges=contacts,
        deltas_written=deltas_written,
        is_full_corpus=(max_filings is None and filing_year is None),
    )
    (out_dir / INGEST_META_FILENAME).write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Ingest Senate LDA lobbying disclosures")
    parser.add_argument("--out", default="data/exports/lda")
    parser.add_argument("--max-filings", type=int, default=2000, help="cap filings (None = full)")
    parser.add_argument("--filing-year", type=int, default=None)
    args = parser.parse_args(argv)
    report = export_lda(
        out_directory=args.out, max_filings=args.max_filings, filing_year=args.filing_year
    )
    print(
        f"LDA: filings={report.filings} registrants={report.registrants} "
        f"clients={report.clients} retention={report.retention_edges} "
        f"bill_edges={report.bill_lobbying_edges} scanned={report.rows_scanned} "
        f"skipped={report.rows_skipped} full={report.is_full_corpus}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
