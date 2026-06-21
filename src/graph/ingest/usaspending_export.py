"""Run the USASpending federal-award ingest: orgs + award edges + CDC + meta.

The runner for the money-out deliverable. It drains a stream of USASpending
award records (from the keyless ``/search/spending_by_award/`` REST endpoint or
a bulk "Custom Award Data" download, normalized to row dicts), runs each through
the pure adapter in :mod:`src.graph.ingest.usaspending`, resolves recipients and
awarding agencies into canonical entities (strong external-id blocking collapses
the same recipient across its many awards), and writes five artifacts under an
output directory:

* a contract corpus (``records.jsonl`` + ``manifest.json``) of the resolved
  recipient + agency **org** rows, via :func:`~src.graph.export.write_contract_corpus`;
* a ``deltas.jsonl`` CDC feed (every org is ``created`` on first ingest);
* an ``award_edges.jsonl`` sidecar — one ``federal_award`` recipient -> agency
  edge per award, plus a ``funded_by`` agency -> appropriating-bill edge whenever
  a public law is derivable — so a graph loader / Track B picks up the money flow;
* an ``ingest_meta.json`` honest-cap record (rows scanned/skipped, award count,
  whether the corpus is the full set or a bounded slice).

Honest cap: USASpending exposes tens of millions of awards; ``max_awards`` bounds
a representative slice for a constrained run and is recorded in the meta. The full
set drops in by raising/removing the cap. All network + pagination I/O lives in
:func:`iter_award_records` (``pragma: no cover``), keeping the build logic pure
and unit-testable from an in-memory stream.
"""

from __future__ import annotations

import hashlib
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
from src.graph.ingest.usaspending import (
    USASPENDING_API_URL,
    USASPENDING_BASE_URL,
    FederalAward,
    award_appropriation_edge,
    award_provenance,
    federal_award_edge,
    parse_awarding_agency,
    parse_federal_award,
    parse_recipient_org,
)
from src.graph.provenance import ProvenanceEnvelope

AWARD_EDGES_FILENAME = "award_edges.jsonl"
INGEST_META_FILENAME = "ingest_meta.json"


@dataclass(frozen=True)
class UsaSpendingIngestReport:
    """Counts + provenance from one USASpending ingest run (in ingest_meta.json)."""

    dataset_url: str
    api_url: str
    as_of: str
    rows_scanned: int
    rows_skipped: int
    awards: int
    recipients: int
    agencies: int
    award_edges: int
    appropriation_edges: int
    deltas_written: int
    is_full_corpus: bool


def _award_url(award: FederalAward) -> str:
    return f"{USASPENDING_BASE_URL}/{award.award_id}"


def _award_sha(award: FederalAward) -> str:
    """Content-address of the award's identity + amount (stable across reruns)."""
    payload = f"{award.award_id}|{award.amount or ''}|{award.action_date.isoformat()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _ParsedAward:
    """One award with its two org source records, ready for resolution + edges."""

    award: FederalAward
    recipient: SourceRecord
    agency: SourceRecord
    provenance: ProvenanceEnvelope


def _parse_one(record: dict[str, Any], *, first_observed_at: datetime) -> _ParsedAward | None:
    award = parse_federal_award(record)
    if award is None:
        return None
    # Leakage guard: an award's known_at defaults to its action/start date. Some
    # multi-year contracts report a period-of-performance start in the future
    # relative to now; a fact cannot be knowable before it is observed, so the
    # provenance envelope rejects known_at > first_observed_at. Skip such rows
    # rather than crash the run (and never fabricate an earlier date).
    award_known = datetime(
        award.action_date.year, award.action_date.month, award.action_date.day, tzinfo=UTC
    )
    if award_known > first_observed_at:
        return None
    provenance = award_provenance(
        award,
        source_url=_award_url(award),
        content_sha256=_award_sha(award),
        first_observed_at=first_observed_at,
    )
    return _ParsedAward(
        award=award,
        recipient=parse_recipient_org(award, provenance=provenance),
        agency=parse_awarding_agency(award, provenance=provenance),
        provenance=provenance,
    )


def build_award_graph(
    records: Iterable[dict[str, Any]],
    *,
    first_observed_at: datetime,
) -> tuple[list[EntityResolutionOutput], list[GraphEdge], int, int]:
    """Drain award rows into (org contract rows, award edges, scanned, skipped).

    Recipients and agencies are resolved with the standard entity-resolution
    linker (strong external-id blocking collapses the same recipient/agency across
    every award), then projected to contract rows. Each award yields a
    ``federal_award`` edge keyed by the source record IDs of its recipient/agency,
    remapped to the resolved canonical IDs, plus a ``funded_by`` edge when a public
    law is derivable. Edges and rows are de-duplicated by identity, so a recipient
    seen in N awards is one row with N distinct edges.
    """
    parsed: list[_ParsedAward] = []
    scanned = skipped = 0
    for record in records:
        scanned += 1
        one = _parse_one(record, first_observed_at=first_observed_at)
        if one is None:
            skipped += 1
            continue
        parsed.append(one)

    # Resolve the union of every recipient + agency record into canonical entities.
    records_by_id: dict[str, SourceRecord] = {}
    for item in parsed:
        records_by_id[item.recipient.record_id] = item.recipient
        records_by_id[item.agency.record_id] = item.agency
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
        recipient_cid = canonical_by_source[item.recipient.record_id]
        agency_cid = canonical_by_source[item.agency.record_id]
        if recipient_cid == agency_cid:
            # Degenerate: a recipient that is also its own awarding agency would be
            # a self-loop; skip rather than fabricate a malformed edge.
            continue
        edge = federal_award_edge(
            recipient_canonical_id=recipient_cid,
            agency_canonical_id=agency_cid,
            award=item.award,
            provenance=item.provenance,
        )
        edges_by_id[edge.edge_id] = edge
        appropriation = award_appropriation_edge(
            agency_canonical_id=agency_cid,
            award=item.award,
            provenance=item.provenance,
        )
        if appropriation is not None:
            edges_by_id[appropriation.edge_id] = appropriation

    return list(rows_by_id.values()), list(edges_by_id.values()), scanned, skipped


def iter_award_records(
    *,
    max_awards: int | None,
    page_size: int = 100,
    client: Any | None = None,
    since: str = "2024-10-01",
    until: str | None = None,
) -> Iterator[dict[str, Any]]:  # pragma: no cover - network + pagination I/O
    """Stream award rows from the keyless USASpending search endpoint.

    Pages ``/search/spending_by_award/`` for both contract and assistance award
    families, yielding each award row dict until ``max_awards`` is reached. Rows
    are filtered to a recent ``time_period`` (``since``..``until``) and sorted by
    award amount descending, so a bounded run captures the largest, most
    meaningful recent awards across *several* agencies rather than the unsorted
    default (which skews to a single agency).
    """
    import httpx
    from datetime import date as _date

    http = client if client is not None else httpx.Client(timeout=60.0)
    owns = client is None
    end_date = until or _date.today().isoformat()
    endpoint = f"{USASPENDING_API_URL}/search/spending_by_award/"
    # NB: the /search/spending_by_award/ endpoint frequently returns a null
    # "Action Date" for contract rows but reliably populates "Start Date" (the
    # period-of-performance start). We request both so the adapter's date fallback
    # (Action Date -> action_date -> Start Date) always has a parseable date; an
    # award is dropped otherwise. "Start Date" is a real, citable award date.
    fields = [
        "Award ID",
        "Recipient Name",
        "Recipient UEI",
        "Awarding Agency",
        "Awarding Sub Agency",
        "Award Type",
        "Award Amount",
        "Action Date",
        "Start Date",
        "generated_internal_id",
    ]
    yielded = 0
    try:
        for award_group in (["A", "B", "C", "D"], ["02", "03", "04", "05"]):
            page = 1
            while True:
                if max_awards is not None and yielded >= max_awards:
                    return
                payload = {
                    "fields": fields,
                    "filters": {
                        "award_type_codes": award_group,
                        "time_period": [{"start_date": since, "end_date": end_date}],
                    },
                    "page": page,
                    "limit": page_size,
                    "sort": "Award Amount",
                    "order": "desc",
                }
                resp = http.post(endpoint, json=payload)
                if resp.status_code != 200:
                    break
                body = resp.json()
                rows = body.get("results", [])
                if not rows:
                    break
                for row in rows:
                    if max_awards is not None and yielded >= max_awards:
                        return
                    yield row
                    yielded += 1
                if not body.get("page_metadata", {}).get("hasNext"):
                    break
                page += 1
    finally:
        if owns:
            http.close()


def export_usaspending(
    *,
    out_directory: Path | str,
    max_awards: int | None = None,
    as_of: datetime | None = None,
    client: Any | None = None,
) -> UsaSpendingIngestReport:  # pragma: no cover - orchestrates network I/O
    """Run the full USASpending ingest: org corpus + award edges + CDC + meta."""
    out_dir = Path(out_directory)
    observed = as_of if as_of is not None else datetime.now(tz=UTC)
    rows, edges, scanned, skipped = build_award_graph(
        iter_award_records(max_awards=max_awards, client=client),
        first_observed_at=observed,
    )

    write_contract_corpus(rows, directory=out_dir, as_of=observed)
    deltas = diff_outputs({}, {row.canonical_id: row for row in rows})
    deltas_written = write_delta_feed(deltas, path=out_dir / DELTAS_FILENAME, append=False)

    edge_path = out_dir / AWARD_EDGES_FILENAME
    with edge_path.open("w", encoding="utf-8") as handle:
        for edge in sorted(edges, key=lambda e: e.edge_id):
            handle.write(edge.model_dump_json() + "\n")

    recipients = sum(
        1
        for r in rows
        if any(k.startswith(("uei:", "duns:", "usaspending_recipient:")) for k in r.external_ids)
    )
    agencies = sum(1 for r in rows if any(k.startswith("usa_agency:") for k in r.external_ids))
    appropriation_edges = sum(1 for e in edges if e.edge_type == "funded_by")
    report = UsaSpendingIngestReport(
        dataset_url=USASPENDING_BASE_URL,
        api_url=USASPENDING_API_URL,
        as_of=observed.isoformat(),
        rows_scanned=scanned,
        rows_skipped=skipped,
        awards=sum(1 for e in edges if e.edge_type == "federal_award"),
        recipients=recipients,
        agencies=agencies,
        award_edges=len(edges),
        appropriation_edges=appropriation_edges,
        deltas_written=deltas_written,
        is_full_corpus=(max_awards is None),
    )
    (out_dir / INGEST_META_FILENAME).write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Ingest USASpending.gov federal awards")
    parser.add_argument("--out", default="data/exports/usaspending")
    parser.add_argument("--max-awards", type=int, default=2000, help="cap awards (None = full)")
    args = parser.parse_args(argv)
    report = export_usaspending(out_directory=args.out, max_awards=args.max_awards)
    print(
        f"USASpending: awards={report.awards} recipients={report.recipients} "
        f"agencies={report.agencies} edges={report.award_edges} "
        f"scanned={report.rows_scanned} skipped={report.rows_skipped} "
        f"full={report.is_full_corpus}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
