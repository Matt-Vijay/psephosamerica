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

# Comprehensive-backfill artifacts. Raw per-year filing rows are cached under
# ``years/<year>.jsonl`` so a re-run resumes without refetching, and ``state.json``
# records which years are fully drained plus the page reached mid-year.
YEARS_SUBDIR = "years"
BACKFILL_STATE_FILENAME = "backfill_state.json"

# The Senate LDA API caps ``page_size`` at 25 and *requires* a filter (a bare
# ``page=1`` 400s); ``filing_year`` is the cheapest comprehensive partition.
LDA_PAGE_SIZE = 25
# Years of comprehensive coverage requested for the backfill (inclusive).
DEFAULT_BACKFILL_YEARS = tuple(range(2016, 2027))


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
class YearStats:
    """Per-filing-year coverage + edge counts for the comprehensive backfill.

    ``description_fill_rate`` is the share of activity lines carrying a non-empty
    ``description`` — null descriptions are a *real* property of the LDA corpus
    (many filings disclose only an issue-area code), recorded here per year rather
    than papered over. ``bill_fill_rate`` is the share of filings that mine at
    least one congress bill reference out of their activity descriptions.
    """

    year: int
    filings: int
    filings_skipped: int
    activities: int
    activities_with_description: int
    filings_with_bill: int
    registrants: int
    clients: int
    retention_edges: int
    bill_lobbying_edges: int

    @property
    def description_fill_rate(self) -> float:
        if self.activities == 0:
            return 0.0
        return round(self.activities_with_description / self.activities, 4)

    @property
    def bill_fill_rate(self) -> float:
        if self.filings == 0:
            return 0.0
        return round(self.filings_with_bill / self.filings, 4)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["description_fill_rate"] = self.description_fill_rate
        data["bill_fill_rate"] = self.bill_fill_rate
        return data


def year_coverage_stats(records: Iterable[dict[str, Any]], *, year: int) -> dict[str, int]:
    """Tally activity-description fill + bill-mention coverage over a year's rows.

    Pure and order-independent: counts activity lines, how many carry a non-empty
    description (the rest are genuinely null in the source), and how many *filings*
    name at least one congress bill. Edge/org counts are filled in by the caller
    that resolves the year.
    """
    from src.graph.ingest.lda import _bill_ids_in

    activities = with_description = filings_with_bill = filings = 0
    for record in records:
        filings += 1
        has_bill = False
        for raw in record.get("lobbying_activities") or []:
            if not isinstance(raw, dict):
                continue
            activities += 1
            description = (raw.get("description") or "").strip()
            if description:
                with_description += 1
                if _bill_ids_in(description):
                    has_bill = True
        if has_bill:
            filings_with_bill += 1
    return {
        "year": year,
        "filings": filings,
        "activities": activities,
        "activities_with_description": with_description,
        "filings_with_bill": filings_with_bill,
    }


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


def _get_with_backoff(
    http: Any,
    url: str,
    *,
    params: dict[str, Any] | None,
    max_retries: int = 6,
) -> Any:  # pragma: no cover - network I/O + sleep
    """GET with exponential backoff on 429/5xx; honors a ``Retry-After`` header."""
    import time

    delay = 2.0
    for _attempt in range(max_retries):
        resp = http.get(url, params=params)
        if resp.status_code == 200:
            return resp
        if resp.status_code in (429, 500, 502, 503, 504):
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
            time.sleep(wait)
            delay = min(delay * 2, 60.0)
            continue
        return resp  # non-retryable (e.g. 400) — let the caller decide
    return resp


def fetch_year_to_cache(
    year: int,
    *,
    out_directory: Path | str,
    client: Any | None = None,
    request_delay: float = 0.0,
) -> int:  # pragma: no cover - network + pagination I/O
    """Drain every filing for ``year`` into ``years/<year>.jsonl``; resumable.

    Pages the API with the required ``filing_year`` filter (a bare page-1 request
    400s without it), appending each page to the year shard and recording the next
    page in ``backfill_state.json`` so an interrupted run resumes mid-year instead
    of refetching. A year already marked ``done`` returns its cached count
    immediately. Returns the number of filing rows cached for the year.
    """
    import time

    import httpx

    out_dir = Path(out_directory)
    years_dir = out_dir / YEARS_SUBDIR
    years_dir.mkdir(parents=True, exist_ok=True)
    shard = years_dir / f"{year}.jsonl"
    state = _read_backfill_state(out_dir)
    year_state = state["years"].get(str(year), {})
    if year_state.get("done"):
        return int(year_state.get("rows", _count_lines(shard)))

    start_page = int(year_state.get("next_page", 1))
    if start_page > 1 and shard.exists():
        # Resuming mid-year: reconcile the shard to exactly the rows the state
        # recorded as complete, dropping any partial trailing line a crash mid-page
        # may have left, then append from the recorded ``next_page``.
        rows_cached = _truncate_shard_to(shard, int(year_state.get("rows", _count_lines(shard))))
        # Re-derive the next page from the rows actually on disk so a short shard
        # (fewer durable rows than the state claimed) re-fetches the right page.
        start_page = rows_cached // LDA_PAGE_SIZE + 1
        mode = "a"
    else:
        rows_cached = 0
        start_page = 1
        mode = "w"

    http = client if client is not None else httpx.Client(timeout=60.0)
    owns = client is None
    page = start_page
    try:
        with shard.open(mode, encoding="utf-8") as handle:
            while True:
                params = {"filing_year": year, "page": page, "page_size": LDA_PAGE_SIZE}
                resp = _get_with_backoff(http, LDA_FILINGS_URL, params=params)
                if resp.status_code != 200:
                    break
                body = resp.json()
                for row in body.get("results", []):
                    handle.write(json.dumps(row) + "\n")
                    rows_cached += 1
                handle.flush()
                has_next = body.get("next") is not None
                page += 1
                _write_year_state(
                    out_dir, year, {"next_page": page, "rows": rows_cached, "done": not has_next}
                )
                if not has_next:
                    break
                if request_delay:
                    time.sleep(request_delay)
    finally:
        if owns:
            http.close()
    return rows_cached


def iter_cached_year(year: int, *, out_directory: Path | str) -> Iterator[dict[str, Any]]:
    """Yield the cached raw filing rows for ``year`` from its shard."""
    shard = Path(out_directory) / YEARS_SUBDIR / f"{year}.jsonl"
    if not shard.exists():
        return
    with shard.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _read_backfill_state(out_dir: Path) -> dict[str, Any]:
    path = out_dir / BACKFILL_STATE_FILENAME
    if not path.exists():
        return {"years": {}}
    state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("years", {})
    return state


def _write_year_state(out_dir: Path, year: int, year_state: dict[str, Any]) -> None:
    state = _read_backfill_state(out_dir)
    state["years"][str(year)] = year_state
    (out_dir / BACKFILL_STATE_FILENAME).write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _truncate_shard_to(path: Path, keep: int) -> int:
    """Rewrite ``path`` keeping its first ``keep`` non-empty lines; return that count.

    Used on resume to discard a partial trailing line (or an over-written tail) so
    the shard exactly matches the rows the backfill state recorded as durable.
    """
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()]
    if len(lines) <= keep:
        return len(lines)
    path.write_text("".join(lines[:keep]), encoding="utf-8")
    return keep


def backfill_lda_years(
    years: Iterable[int] = DEFAULT_BACKFILL_YEARS,
    *,
    out_directory: Path | str = "data/exports/lda",
    as_of: datetime | None = None,
    client: Any | None = None,
    request_delay: float = 0.0,
) -> LdaIngestReport:  # pragma: no cover - orchestrates network I/O
    """Comprehensively backfill LDA filings for ``years`` into one corpus + edges.

    Phase 1 drains every requested year to its on-disk shard (resumable, rate-limit
    backed off). Phase 2 reads all shards back, runs the pure
    :func:`build_lobbying_graph` across the *combined* stream so a registrant/client
    deduplicates globally (not per year), and writes the contract corpus, CDC deltas,
    and lobbying edges. ``ingest_meta.json`` carries the run totals plus a
    per-year breakdown including the honest description-fill and bill-fill rates.
    """
    out_dir = Path(out_directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    observed = as_of if as_of is not None else datetime.now(tz=UTC)
    ordered_years = sorted(set(years))

    per_year_raw: dict[int, dict[str, int]] = {}
    for year in ordered_years:
        fetch_year_to_cache(year, out_directory=out_dir, client=client, request_delay=request_delay)
        per_year_raw[year] = year_coverage_stats(
            iter_cached_year(year, out_directory=out_dir), year=year
        )

    def _all_rows() -> Iterator[dict[str, Any]]:
        for year in ordered_years:
            yield from iter_cached_year(year, out_directory=out_dir)

    rows, edges, scanned, skipped = build_lobbying_graph(_all_rows(), first_observed_at=observed)

    write_contract_corpus(rows, directory=out_dir, as_of=observed)
    deltas = diff_outputs({}, {row.canonical_id: row for row in rows})
    deltas_written = write_delta_feed(deltas, path=out_dir / DELTAS_FILENAME, append=False)

    edge_path = out_dir / LOBBYING_EDGES_FILENAME
    with edge_path.open("w", encoding="utf-8") as handle:
        for edge in sorted(edges, key=lambda e: e.edge_id):
            handle.write(edge.model_dump_json() + "\n")

    # Attribute resolved edges back to filing years (filing_uuid -> year), so the
    # per-year report carries retention/bill-edge counts alongside the fill rates.
    edge_year = _edges_by_year(out_dir, ordered_years)
    per_year_report = _build_year_reports(per_year_raw, edges, edge_year, rows, out_dir)
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
        is_full_corpus=True,
    )
    meta = asdict(report)
    meta["years"] = ordered_years
    meta["per_year"] = [s.as_dict() for s in per_year_report]
    (out_dir / INGEST_META_FILENAME).write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _edges_by_year(out_dir: Path, years: list[int]) -> dict[str, int]:
    """Map each filing_uuid back to its filing_year from the cached shards."""
    mapping: dict[str, int] = {}
    for year in years:
        for record in iter_cached_year(year, out_directory=out_dir):
            uuid = (record.get("filing_uuid") or "").strip()
            if uuid:
                mapping[uuid] = int(record.get("filing_year") or year)
    return mapping


def _build_year_reports(
    per_year_raw: dict[int, dict[str, int]],
    edges: list[GraphEdge],
    edge_year: dict[str, int],
    rows: list[Any],
    out_dir: Path,
) -> list[YearStats]:
    """Fold edge counts into the per-year coverage tallies."""
    retention_by_year: dict[int, int] = {}
    contact_by_year: dict[int, int] = {}
    for edge in edges:
        uuid = (edge.external_key or "").split(":", 1)[0]
        year = edge_year.get(uuid)
        if year is None:
            continue
        if edge.edge_type == "lobbying_retention":
            retention_by_year[year] = retention_by_year.get(year, 0) + 1
        elif edge.edge_type == "lobbying_contact":
            contact_by_year[year] = contact_by_year.get(year, 0) + 1

    reports: list[YearStats] = []
    for year in sorted(per_year_raw):
        raw = per_year_raw[year]
        reports.append(
            YearStats(
                year=year,
                filings=raw["filings"],
                filings_skipped=0,
                activities=raw["activities"],
                activities_with_description=raw["activities_with_description"],
                filings_with_bill=raw["filings_with_bill"],
                registrants=0,
                clients=0,
                retention_edges=retention_by_year.get(year, 0),
                bill_lobbying_edges=contact_by_year.get(year, 0),
            )
        )
    return reports


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
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="comprehensive resumable backfill across --years (ignores --max-filings)",
    )
    parser.add_argument(
        "--years",
        default=None,
        help="comma-separated or START-END filing years (default 2016-2026)",
    )
    parser.add_argument(
        "--request-delay", type=float, default=0.0, help="seconds to sleep between API pages"
    )
    args = parser.parse_args(argv)

    if args.backfill:
        years = _parse_years(args.years) if args.years else DEFAULT_BACKFILL_YEARS
        report = backfill_lda_years(years, out_directory=args.out, request_delay=args.request_delay)
    else:
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


def _parse_years(spec: str) -> tuple[int, ...]:  # pragma: no cover - CLI glue
    if "-" in spec and "," not in spec:
        start, end = (int(p) for p in spec.split("-", 1))
        return tuple(range(start, end + 1))
    return tuple(int(p) for p in spec.split(","))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
