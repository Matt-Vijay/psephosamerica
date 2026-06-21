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

Comprehensive coverage: USASpending exposes ~35M awards for FY2023-2026, the long
tail of which is small micro purchase-card lines carrying a negligible share of
total dollars. The runner therefore applies a ``min_amount`` threshold
(default $1M, recorded in the meta) to capture the dollar-meaningful universe
across *all* award families (contracts, grants, direct payments, loans, other)
and *all* awarding agencies. The fetch is **resumable**: every page is streamed to
``raw_awards.jsonl`` and checkpointed in ``fetch_state.json`` (cursor pagination
breaks the search endpoint's 10k-record numeric-page ceiling), so a stall resumes
mid-family instead of restarting. ``max_awards`` still bounds a constrained run.
All network + pagination I/O lives in :func:`fetch_raw_records` /
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
    AWARD_TYPE_GROUPS,
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
#: Resumable raw fetch cache. The network phase streams every award row here
#: (one JSON object per line) so a stall/crash never loses fetched pages; the
#: build phase then reads it back. Gitignored (large); kept across runs.
RAW_RECORDS_FILENAME = "raw_awards.jsonl"
#: Per-family fetch progress, so a resumed run skips already-drained families and
#: continues mid-family from the last cursor instead of refetching from page 1.
FETCH_STATE_FILENAME = "fetch_state.json"

#: Default minimum award amount. Federal award dollars are extremely top-heavy:
#: of ~35.4M FY2023-2026 awards, only ~1.14M are >= $1M yet they carry the large
#: majority of total obligated dollars. $1M therefore captures the
#: dollar-meaningful universe across all families/agencies while keeping the row
#: count (and the resumable run) tractable, rather than chasing tens of millions
#: of micro purchase-card lines. Lower it (e.g. --min-amount 250000, ~2.19M rows)
#: for a deeper multi-session backfill; the resumable cache accumulates either way.
DEFAULT_MIN_AMOUNT = 1_000_000.0
#: FY2023 begins 2023-10-01 in US federal fiscal terms; we span FY2023..FY2026 by
#: calendar action date 2022-10-01 (FY2023 start) through "today".
DEFAULT_SINCE = "2022-10-01"


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
    # Honest filter + coverage provenance (the "what did we actually fetch" record).
    since: str = ""
    until: str = ""
    min_amount: float = 0.0
    award_type_groups: tuple[str, ...] = ()
    total_dollars_covered: str = "0.00"
    raw_rows_fetched: int = 0
    families_completed: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


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


#: The award fields requested from the search endpoint. NB: the endpoint
#: frequently returns a null "Action Date" for contract rows but reliably
#: populates "Start Date" (the period-of-performance start). We request both so
#: the adapter's date fallback (Action Date -> action_date -> Start Date) always
#: has a parseable date; an award is dropped otherwise. "Start Date" is a real,
#: citable award date.
_AWARD_FIELDS = [
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


def _search_filters(
    award_type_codes: tuple[str, ...], *, since: str, until: str, min_amount: float
) -> dict[str, Any]:
    filters: dict[str, Any] = {
        "award_type_codes": list(award_type_codes),
        "time_period": [{"start_date": since, "end_date": until}],
    }
    if min_amount > 0:
        filters["award_amounts"] = [{"lower_bound": min_amount}]
    return filters


def _post_with_backoff(
    http: Any, endpoint: str, payload: dict[str, Any], *, max_retries: int = 6
) -> dict[str, Any] | None:  # pragma: no cover - network I/O
    """POST with exponential backoff on 429/5xx/transport errors.

    Returns the decoded body, or ``None`` once retries are exhausted (the caller
    records the gap honestly rather than crashing the whole run).
    """
    import time

    import httpx

    delay = 2.0
    for attempt in range(max_retries):
        try:
            resp = http.post(endpoint, json=payload)
        except httpx.TransportError:
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
            continue
        if resp.status_code == 200:
            return resp.json()  # type: ignore[no-any-return]
        if resp.status_code in (429, 500, 502, 503, 504):
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
            time.sleep(wait)
            delay = min(delay * 2, 60.0)
            continue
        # A non-retryable client error (e.g. 422): stop paging this family.
        return None
    return None


def fetch_raw_records(
    *,
    out_dir: Path,
    since: str,
    until: str,
    min_amount: float,
    award_type_groups: dict[str, tuple[str, ...]],
    max_awards: int | None = None,
    client: Any | None = None,
    page_size: int = 100,
) -> tuple[int, list[str], list[str]]:  # pragma: no cover - network + pagination I/O
    """Drain every matching award to ``raw_awards.jsonl``; resumable + backoff.

    Pages each award family separately with the search endpoint's cursor
    pagination (``last_record_unique_id`` + ``last_record_sort_value``), which —
    unlike numeric ``page`` — is not capped at the 10,000-record ceiling, so a
    family with millions of awards is fully drained. Progress is checkpointed to
    ``fetch_state.json`` after every page: a resumed run skips families already
    marked complete and continues an in-progress family from its saved cursor,
    appending to the existing raw cache rather than refetching.

    Returns ``(rows_written_this_run, families_completed, notes)``. ``notes``
    captures any family that hit the retry ceiling (a recorded, honest gap).
    """
    import httpx

    http = client if client is not None else httpx.Client(timeout=120.0)
    owns = client is None
    endpoint = f"{USASPENDING_API_URL}/search/spending_by_award/"
    raw_path = out_dir / RAW_RECORDS_FILENAME
    state_path = out_dir / FETCH_STATE_FILENAME

    state: dict[str, Any] = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    completed: list[str] = list(state.get("completed", []))
    cursor: dict[str, Any] = state.get("cursor", {})
    total_written = int(state.get("rows_written", 0))
    notes: list[str] = list(state.get("notes", []))

    # Append (resume) vs truncate (fresh start) the raw cache to match the state.
    mode = "a" if (state and raw_path.exists()) else "w"
    written_this_run = 0
    try:
        with raw_path.open(mode, encoding="utf-8") as sink:

            def checkpoint(family: str, last_id: Any, last_sort: Any) -> None:
                cursor[family] = {"last_id": last_id, "last_sort": last_sort}
                state_path.write_text(
                    json.dumps(
                        {
                            "completed": completed,
                            "cursor": cursor,
                            "rows_written": total_written,
                            "notes": notes,
                            "since": since,
                            "until": until,
                            "min_amount": min_amount,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )

            for family, codes in award_type_groups.items():
                if family in completed:
                    continue
                if max_awards is not None and total_written >= max_awards:
                    break
                filters = _search_filters(codes, since=since, until=until, min_amount=min_amount)
                resume = cursor.get(family, {})
                last_id = resume.get("last_id")
                last_sort = resume.get("last_sort")
                while True:
                    if max_awards is not None and total_written >= max_awards:
                        break
                    payload: dict[str, Any] = {
                        "fields": _AWARD_FIELDS,
                        "filters": filters,
                        "page": 1,
                        "limit": page_size,
                        "sort": "Award Amount",
                        "order": "desc",
                    }
                    if last_id is not None:
                        payload["last_record_unique_id"] = last_id
                        payload["last_record_sort_value"] = last_sort
                    body = _post_with_backoff(http, endpoint, payload)
                    if body is None:
                        notes.append(f"{family}: retries exhausted, partial coverage")
                        checkpoint(family, last_id, last_sort)
                        break
                    rows = body.get("results", [])
                    if not rows:
                        completed.append(family)
                        checkpoint(family, last_id, last_sort)
                        break
                    for row in rows:
                        if max_awards is not None and total_written >= max_awards:
                            break
                        sink.write(json.dumps(row) + "\n")
                        total_written += 1
                        written_this_run += 1
                    sink.flush()
                    meta = body.get("page_metadata", {})
                    last_id = meta.get("last_record_unique_id")
                    last_sort = meta.get("last_record_sort_value")
                    checkpoint(family, last_id, last_sort)
                    if not meta.get("hasNext") or last_id is None:
                        if family not in completed:
                            completed.append(family)
                        checkpoint(family, last_id, last_sort)
                        break
    finally:
        if owns:
            http.close()
    return written_this_run, completed, notes


def _iter_raw_cache(out_dir: Path) -> Iterator[dict[str, Any]]:
    raw_path = out_dir / RAW_RECORDS_FILENAME
    if not raw_path.exists():
        return
    with raw_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def iter_award_records(
    *,
    max_awards: int | None,
    page_size: int = 100,
    client: Any | None = None,
    since: str = DEFAULT_SINCE,
    until: str | None = None,
    min_amount: float = 0.0,
    award_type_groups: dict[str, tuple[str, ...]] | None = None,
) -> Iterator[dict[str, Any]]:  # pragma: no cover - network + pagination I/O
    """Stream award rows directly from the search endpoint (no disk cache).

    A thin, in-memory wrapper over the same cursor pagination as
    :func:`fetch_raw_records`, kept for callers/tests that want a record stream
    without the resumable raw cache. The comprehensive runner uses
    :func:`fetch_raw_records` instead so a stall never loses progress.
    """
    import httpx
    from datetime import date as _date

    http = client if client is not None else httpx.Client(timeout=120.0)
    owns = client is None
    end_date = until or _date.today().isoformat()
    endpoint = f"{USASPENDING_API_URL}/search/spending_by_award/"
    groups = award_type_groups or {k: AWARD_TYPE_GROUPS[k] for k in ("contracts", "grants")}
    yielded = 0
    try:
        for codes in groups.values():
            filters = _search_filters(codes, since=since, until=end_date, min_amount=min_amount)
            last_id = last_sort = None
            while True:
                if max_awards is not None and yielded >= max_awards:
                    return
                payload: dict[str, Any] = {
                    "fields": _AWARD_FIELDS,
                    "filters": filters,
                    "page": 1,
                    "limit": page_size,
                    "sort": "Award Amount",
                    "order": "desc",
                }
                if last_id is not None:
                    payload["last_record_unique_id"] = last_id
                    payload["last_record_sort_value"] = last_sort
                body = _post_with_backoff(http, endpoint, payload)
                if body is None:
                    break
                rows = body.get("results", [])
                if not rows:
                    break
                for row in rows:
                    if max_awards is not None and yielded >= max_awards:
                        return
                    yield row
                    yielded += 1
                meta = body.get("page_metadata", {})
                last_id = meta.get("last_record_unique_id")
                last_sort = meta.get("last_record_sort_value")
                if not meta.get("hasNext") or last_id is None:
                    break
    finally:
        if owns:
            http.close()


def _sum_award_dollars(edges: Iterable[GraphEdge]) -> str:
    """Sum the ``amount`` attribute over ``federal_award`` edges (cents-safe)."""
    from decimal import Decimal

    total = Decimal("0")
    for edge in edges:
        if edge.edge_type != "federal_award":
            continue
        raw = edge.attributes.get("amount")
        if raw:
            try:
                total += Decimal(raw)
            except (ArithmeticError, ValueError):
                continue
    return f"{total:.2f}"


def export_usaspending(
    *,
    out_directory: Path | str,
    max_awards: int | None = None,
    as_of: datetime | None = None,
    client: Any | None = None,
    since: str = DEFAULT_SINCE,
    until: str | None = None,
    min_amount: float = DEFAULT_MIN_AMOUNT,
    award_type_groups: dict[str, tuple[str, ...]] | None = None,
    fetch: bool = True,
) -> UsaSpendingIngestReport:  # pragma: no cover - orchestrates network I/O
    """Run the comprehensive USASpending ingest: fetch -> build -> corpus + meta.

    Phase 1 (resumable): :func:`fetch_raw_records` drains every matching award to
    ``raw_awards.jsonl``, checkpointing after each page so a stall resumes instead
    of restarting. Pass ``fetch=False`` to rebuild from an already-fetched cache.
    Phase 2: :func:`build_award_graph` resolves recipients + agencies and emits
    the org corpus, CDC deltas, award edges, and an honest ``ingest_meta.json``
    recording the exact filters, threshold, date range, totals, and any gaps.
    """
    from datetime import date as _date

    out_dir = Path(out_directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    observed = as_of if as_of is not None else datetime.now(tz=UTC)
    end_date = until or _date.today().isoformat()
    groups = award_type_groups or AWARD_TYPE_GROUPS

    completed: list[str] = []
    notes: list[str] = []
    raw_fetched = 0
    if fetch:
        raw_fetched, completed, notes = fetch_raw_records(
            out_dir=out_dir,
            since=since,
            until=end_date,
            min_amount=min_amount,
            award_type_groups=groups,
            max_awards=max_awards,
            client=client,
        )
    else:
        state_path = out_dir / FETCH_STATE_FILENAME
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            completed = list(state.get("completed", []))
            notes = list(state.get("notes", []))
            raw_fetched = int(state.get("rows_written", 0))

    rows, edges, scanned, skipped = build_award_graph(
        _iter_raw_cache(out_dir), first_observed_at=observed
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
    all_families_done = set(groups) <= set(completed) and not notes
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
        is_full_corpus=(max_awards is None and all_families_done),
        since=since,
        until=end_date,
        min_amount=min_amount,
        award_type_groups=tuple(groups),
        total_dollars_covered=_sum_award_dollars(edges),
        raw_rows_fetched=raw_fetched,
        families_completed=tuple(completed),
        notes=tuple(notes),
    )
    (out_dir / INGEST_META_FILENAME).write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Ingest USASpending.gov federal awards")
    parser.add_argument("--out", default="data/exports/usaspending")
    parser.add_argument(
        "--max-awards", type=int, default=None, help="cap awards (omit/None = comprehensive)"
    )
    parser.add_argument("--since", default=DEFAULT_SINCE, help="action-date lower bound")
    parser.add_argument("--until", default=None, help="action-date upper bound (default today)")
    parser.add_argument(
        "--min-amount", type=float, default=DEFAULT_MIN_AMOUNT, help="minimum award $ threshold"
    )
    parser.add_argument(
        "--no-fetch", action="store_true", help="rebuild from existing raw cache only"
    )
    args = parser.parse_args(argv)
    report = export_usaspending(
        out_directory=args.out,
        max_awards=args.max_awards,
        since=args.since,
        until=args.until,
        min_amount=args.min_amount,
        fetch=not args.no_fetch,
    )
    print(
        f"USASpending: awards={report.awards} recipients={report.recipients} "
        f"agencies={report.agencies} edges={report.award_edges} "
        f"$covered={report.total_dollars_covered} "
        f"min=${report.min_amount:,.0f} range={report.since}..{report.until} "
        f"scanned={report.rows_scanned} skipped={report.rows_skipped} "
        f"full={report.is_full_corpus} notes={list(report.notes)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
