"""Run the CourtListener court-records ingest: courts + judges + opinions + CDC.

The runner for the judicial-records deliverable. It drains three keyless v4
streams from CourtListener (Free Law Project) — ``search?type=o`` opinion
clusters, the ``courts`` registry, and the ``people`` judge registry — through
the pure adapter in :mod:`src.graph.ingest.courtlistener`, resolves courts (orgs)
and judges (persons) into canonical entities with the standard entity-resolution
linker (so a judge dedupes across all their opinions and can later link to the
Senate confirmation/nomination already in the graph via the shared ``fjc`` id),
and writes the corpus + sidecars under an output directory:

* a contract corpus (``records.jsonl`` + ``manifest.json``) of the resolved
  court **org** + judge **person** rows, via
  :func:`~src.graph.export.write_contract_corpus`;
* a ``deltas.jsonl`` CDC feed (every entity ``created`` on first ingest) — the
  delta-CDC announce the query layer tails;
* a ``court_edges.jsonl`` sidecar — ``decided_by`` (opinion -> court),
  ``authored_opinion`` / ``joined_opinion`` (judge -> opinion),
  ``cites_opinion`` (opinion -> opinion within the pull), and ``cites_statute``
  (opinion -> U.S. Code / Public Law) edges;
* a ``court_opinions.jsonl`` sidecar — one ``court_opinion`` node per cluster
  (cluster id, court, date, case name, docket, reporter cites) so the query layer
  can materialize the opinion nodes the edges point at;
* an ``ingest_meta.json`` honest-cap record documenting the exact scope/cap.

Scope/cap (documented in ``ingest_meta.json``): a **bounded** but meaningful
pull of recent opinions — the configured courts (default: SCOTUS + all 13 U.S.
Courts of Appeals + a slice of large U.S. district courts + several state high
courts) filtered to ``date_filed >= since`` (default 2015-01-01), capped at
``max_opinions_per_court`` per court (default 500). Judges are fetched for the
author/panel ids referenced by the pulled opinions plus the full bench of the
configured courts; courts are fetched for the configured set. The fetch is
**resumable** (each phase checkpoints to ``fetch_state.json`` and streams to raw
caches) and backs off on 429/5xx. All network/pagination I/O is isolated in the
``fetch_*`` helpers (``pragma: no cover``); the build logic is pure and unit-
testable from in-memory streams.

Ethics: public-record only, no logins, keyless (a token only raises rate limits
and is NOT required). A conservative inter-request delay + exponential backoff
respect CourtListener's open-by-default rate guidance.
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
from src.graph.ingest.courtlistener import (
    COURTLISTENER_API_URL,
    COURTLISTENER_BASE_URL,
    Court,
    CourtOpinion,
    Judge,
    court_provenance,
    judge_opinion_edges,
    judge_provenance,
    opinion_canonical_id,
    opinion_citation_edges,
    opinion_court_edge,
    opinion_provenance,
    opinion_sha,
    opinion_statute_edges,
    opinion_url,
    parse_court,
    parse_court_opinion,
    parse_court_org,
    parse_judge,
    parse_judge_person,
)
from src.graph.provenance import ProvenanceEnvelope

COURT_EDGES_FILENAME = "court_edges.jsonl"
COURT_OPINIONS_FILENAME = "court_opinions.jsonl"
INGEST_META_FILENAME = "ingest_meta.json"
#: Resumable raw fetch caches (one JSON object per line). Gitignored (large).
RAW_OPINIONS_FILENAME = "raw_opinions.jsonl"
RAW_COURTS_FILENAME = "raw_courts.jsonl"
RAW_JUDGES_FILENAME = "raw_judges.jsonl"
FETCH_STATE_FILENAME = "fetch_state.json"

#: Default decision-date lower bound. Recent-decade case law keeps the pull
#: meaningful (the courts that matter most for governance prediction) and bounded.
DEFAULT_SINCE = "2015-01-01"
#: Default per-court opinion cap (documented in the meta). Keeps the bounded pull
#: tractable on the keyless rate while still spanning every configured court.
DEFAULT_MAX_OPINIONS_PER_COURT = 500
#: Conservative inter-request delay (seconds) respecting CourtListener's
#: open-by-default rate guidance for keyless reads.
DEFAULT_REQUEST_DELAY = 1.0

#: The bounded, documented court scope: SCOTUS + every U.S. Court of Appeals +
#: a slice of the largest U.S. district courts + several state courts of last
#: resort. This is the "recent SCOTUS + Courts of Appeals + a slice of district /
#: state high courts" scope the deliverable calls for.
SCOTUS_COURTS: tuple[str, ...] = ("scotus",)
APPEALS_COURTS: tuple[str, ...] = (
    "ca1",
    "ca2",
    "ca3",
    "ca4",
    "ca5",
    "ca6",
    "ca7",
    "ca8",
    "ca9",
    "ca10",
    "ca11",
    "cadc",
    "cafc",
)
DISTRICT_COURTS: tuple[str, ...] = ("dcd", "nysd", "cand", "txnd", "ilnd", "flsd")
STATE_HIGH_COURTS: tuple[str, ...] = ("cal", "ny", "tex", "fla", "ill")
DEFAULT_COURTS: tuple[str, ...] = (
    SCOTUS_COURTS + APPEALS_COURTS + DISTRICT_COURTS + STATE_HIGH_COURTS
)


@dataclass(frozen=True)
class CourtListenerIngestReport:
    """Counts + provenance from one CourtListener ingest run (ingest_meta.json)."""

    dataset_url: str
    api_url: str
    as_of: str
    opinions_scanned: int
    opinions_skipped: int
    courts: int
    judges: int
    opinions: int
    decided_by_edges: int
    authored_edges: int
    joined_edges: int
    cites_opinion_edges: int
    cites_statute_edges: int
    total_edges: int
    deltas_written: int
    is_full_corpus: bool
    # Honest scope/cap provenance.
    since: str = ""
    courts_scope: tuple[str, ...] = ()
    max_opinions_per_court: int = 0
    raw_opinions_fetched: int = 0
    courts_completed: tuple[str, ...] = ()
    keyless: bool = True
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ParsedOpinion:
    """One opinion with its provenance, ready for edge construction."""

    opinion: CourtOpinion
    provenance: ProvenanceEnvelope


def _parse_opinion_one(
    record: dict[str, Any], *, first_observed_at: datetime
) -> _ParsedOpinion | None:
    opinion = parse_court_opinion(record)
    if opinion is None:
        return None
    # Leakage guard: an opinion's known_at is its filing date; a fact cannot be
    # knowable before it is observed. Skip rows dated after the observation time
    # rather than crash (the provenance envelope would reject them).
    opinion_known = datetime(
        opinion.date_filed.year, opinion.date_filed.month, opinion.date_filed.day, tzinfo=UTC
    )
    if opinion_known > first_observed_at:
        return None
    provenance = opinion_provenance(
        opinion,
        source_url=opinion_url(opinion),
        content_sha256=opinion_sha(opinion),
        first_observed_at=first_observed_at,
    )
    return _ParsedOpinion(opinion=opinion, provenance=provenance)


def build_court_graph(
    *,
    opinion_records: Iterable[dict[str, Any]],
    court_records: Iterable[dict[str, Any]],
    judge_records: Iterable[dict[str, Any]],
    first_observed_at: datetime,
) -> tuple[
    list[EntityResolutionOutput],
    list[GraphEdge],
    list[CourtOpinion],
    int,
    int,
]:
    """Drain the three streams into (org/person rows, edges, opinions, scanned, skipped).

    Courts (orgs) and judges (persons) are resolved with the standard linker (the
    shared ``fjc`` / CourtListener-person external ids collapse a judge across all
    their opinions and to other sources). Each opinion yields a ``decided_by`` edge
    to its court, ``authored_opinion`` / ``joined_opinion`` edges to its judges,
    ``cites_opinion`` edges to other opinions *in the pull*, and ``cites_statute``
    edges where a statute is derivable. Rows and edges are de-duplicated by
    identity, so a judge seen in N opinions is one row with N edges.
    """
    # Parse opinions first; they tell us which courts + judges we must have.
    parsed_opinions: list[_ParsedOpinion] = []
    scanned = skipped = 0
    for record in opinion_records:
        scanned += 1
        one = _parse_opinion_one(record, first_observed_at=first_observed_at)
        if one is None:
            skipped += 1
            continue
        parsed_opinions.append(one)

    # Map every CourtListener opinion id -> its cluster id, so cites_opinion edges
    # connect clusters we actually ingested (never a fabricated canonical id).
    cited_cluster_for_opinion: dict[int, int] = {}
    for item in parsed_opinions:
        for authorship in item.opinion.opinions:
            cited_cluster_for_opinion[authorship.opinion_id] = item.opinion.cluster_id

    # Parse + resolve courts and judges into canonical entities.
    source_records: dict[str, SourceRecord] = {}
    court_source_by_id: dict[str, str] = {}  # court_id -> source record_id
    for record in court_records:
        court: Court | None = parse_court(record)
        if court is None:
            continue
        prov = court_provenance(
            court,
            source_url=f"{COURTLISTENER_API_URL}/courts/{court.court_id}/",
            content_sha256=_court_sha(court),
            first_observed_at=first_observed_at,
        )
        src = parse_court_org(court, provenance=prov)
        source_records[src.record_id] = src
        court_source_by_id[court.court_id] = src.record_id

    judge_source_by_person: dict[int, str] = {}  # CL person id -> source record_id
    for record in judge_records:
        judge: Judge | None = parse_judge(record)
        if judge is None:
            continue
        prov = judge_provenance(
            source_url=f"{COURTLISTENER_API_URL}/people/{judge.person_id}/",
            content_sha256=_judge_sha(judge),
            first_observed_at=first_observed_at,
        )
        src = parse_judge_person(judge, provenance=prov)
        source_records[src.record_id] = src
        judge_source_by_person[judge.person_id] = src.record_id

    result = resolve(source_records.values())
    canonical_by_source: dict[str, str] = {}
    rows_by_id: dict[str, EntityResolutionOutput] = {}
    for cluster in result.clusters:
        entity = build_canonical_entity(cluster, source_records)
        if entity is None:
            continue
        row = build_entity_resolution_output(entity)
        rows_by_id[row.canonical_id] = row
        for source_id in cluster.record_ids:
            canonical_by_source[source_id] = entity.canonical_id

    def court_cid(court_id: str) -> str | None:
        src_id = court_source_by_id.get(court_id)
        return canonical_by_source.get(src_id) if src_id else None

    def judge_cid(person_id: int) -> str | None:
        src_id = judge_source_by_person.get(person_id)
        return canonical_by_source.get(src_id) if src_id else None

    edges_by_id: dict[str, GraphEdge] = {}
    opinions_out: list[CourtOpinion] = []
    for item in parsed_opinions:
        opinion = item.opinion
        opinions_out.append(opinion)
        court_canonical = court_cid(opinion.court_id)
        if court_canonical is not None and court_canonical != opinion_canonical_id(
            opinion.cluster_id
        ):
            edge = opinion_court_edge(
                opinion=opinion,
                court_canonical_id=court_canonical,
                provenance=item.provenance,
            )
            edges_by_id[edge.edge_id] = edge
        joined_cids = {
            jid: cid
            for jid in {j for a in opinion.opinions for j in a.joined_by_ids}
            if (cid := judge_cid(jid)) is not None
        }
        for authorship in opinion.opinions:
            author_canonical = (
                judge_cid(authorship.author_id) if authorship.author_id is not None else None
            )
            for edge in judge_opinion_edges(
                opinion=opinion,
                authorship=authorship,
                author_canonical_id=author_canonical,
                joined_canonical_ids=joined_cids,
                provenance=item.provenance,
            ):
                edges_by_id[edge.edge_id] = edge
            for edge in opinion_citation_edges(
                opinion=opinion,
                authorship=authorship,
                provenance=item.provenance,
                cited_cluster_for_opinion=cited_cluster_for_opinion,
            ):
                edges_by_id[edge.edge_id] = edge
        for edge in opinion_statute_edges(opinion=opinion, provenance=item.provenance):
            edges_by_id[edge.edge_id] = edge

    return list(rows_by_id.values()), list(edges_by_id.values()), opinions_out, scanned, skipped


def _court_sha(court: Court) -> str:
    import hashlib

    payload = f"court|{court.court_id}|{court.full_name}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _judge_sha(judge: Judge) -> str:
    import hashlib

    payload = f"judge|{judge.person_id}|{judge.fjc_id or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def opinion_node_row(opinion: CourtOpinion) -> dict[str, Any]:
    """Serialize a ``court_opinion`` node for the sidecar (cluster -> node)."""
    return {
        "canonical_id": opinion_canonical_id(opinion.cluster_id),
        "entity_type": "court_opinion",
        "cluster_id": opinion.cluster_id,
        "case_name": opinion.case_name,
        "court_id": opinion.court_id,
        "date_filed": opinion.date_filed.isoformat(),
        "docket_number": opinion.docket_number,
        "reporter_citations": list(opinion.reporter_citations),
        "source_url": opinion_url(opinion),
        "known_at": datetime(
            opinion.date_filed.year, opinion.date_filed.month, opinion.date_filed.day, tzinfo=UTC
        ).isoformat(),
    }


# ── Network I/O (resumable, backoff) ─────────────────────────────────────────


def _get_with_backoff(
    http: Any, url: str, *, max_retries: int = 6, delay: float = DEFAULT_REQUEST_DELAY
) -> dict[str, Any] | None:  # pragma: no cover - network I/O
    """GET with exponential backoff on 429/5xx/transport errors; courteous delay.

    Returns the decoded body, or ``None`` once retries are exhausted (the caller
    records the gap honestly rather than crashing the whole run).
    """
    import time

    import httpx

    backoff = 2.0
    for _attempt in range(max_retries):
        try:
            resp = http.get(url)
        except httpx.TransportError:
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
            continue
        if resp.status_code == 200:
            time.sleep(delay)  # courteous inter-request spacing
            return resp.json()  # type: ignore[no-any-return]
        if resp.status_code in (429, 500, 502, 503, 504):
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff
            time.sleep(wait)
            backoff = min(backoff * 2, 60.0)
            continue
        return None  # non-retryable client error (e.g. 400/403): stop this stream
    return None


def fetch_opinions(
    *,
    out_dir: Path,
    courts: Iterable[str],
    since: str,
    max_opinions_per_court: int,
    client: Any | None = None,
) -> tuple[int, list[str], list[str]]:  # pragma: no cover - network + pagination I/O
    """Drain recent opinions per court to ``raw_opinions.jsonl``; resumable + backoff.

    Pages the keyless ``search?type=o`` endpoint per court (cursor pagination),
    filtered to ``dateFiled >= since`` and capped at ``max_opinions_per_court``.
    Checkpoints completed courts to ``fetch_state.json`` so a resumed run skips
    courts already drained. Returns ``(rows_written_this_run, courts_completed,
    notes)``; ``notes`` records any court that hit the retry ceiling.
    """
    import httpx

    http = (
        client
        if client is not None
        else httpx.Client(
            timeout=120.0,
            headers={
                "User-Agent": "psephosamerica/1.0 (public-record ingest; +https://github.com/psephosamerica)"
            },
        )
    )
    owns = client is None
    raw_path = out_dir / RAW_OPINIONS_FILENAME
    state_path = out_dir / FETCH_STATE_FILENAME
    state: dict[str, Any] = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    completed: list[str] = list(state.get("opinions_completed", []))
    total_written = int(state.get("opinions_written", 0))
    notes: list[str] = list(state.get("notes", []))
    written_this_run = 0
    mode = "a" if (state and raw_path.exists()) else "w"

    def checkpoint() -> None:
        state.update(
            {
                "opinions_completed": completed,
                "opinions_written": total_written,
                "notes": notes,
                "since": since,
                "max_opinions_per_court": max_opinions_per_court,
            }
        )
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    try:
        with raw_path.open(mode, encoding="utf-8") as sink:
            for court in courts:
                if court in completed:
                    continue
                base = (
                    f"{COURTLISTENER_API_URL}/search/?type=o&court={court}"
                    f"&filed_after={since}&order_by=dateFiled+desc&page_size=100"
                )
                url: str | None = base
                fetched_for_court = 0
                while url is not None and fetched_for_court < max_opinions_per_court:
                    body = _get_with_backoff(http, url)
                    if body is None:
                        notes.append(f"{court}: retries exhausted, partial coverage")
                        break
                    rows = body.get("results", [])
                    if not rows:
                        break
                    for row in rows:
                        if fetched_for_court >= max_opinions_per_court:
                            break
                        sink.write(json.dumps(row) + "\n")
                        total_written += 1
                        written_this_run += 1
                        fetched_for_court += 1
                    sink.flush()
                    url = body.get("next")
                completed.append(court)
                checkpoint()
    finally:
        if owns:
            http.close()
    return written_this_run, completed, notes


def fetch_courts(
    *, out_dir: Path, court_ids: Iterable[str], client: Any | None = None
) -> int:  # pragma: no cover - network I/O
    """Fetch each configured court's ``/courts/{id}/`` record to ``raw_courts.jsonl``."""
    import httpx

    http = (
        client
        if client is not None
        else httpx.Client(
            timeout=60.0,
            headers={"User-Agent": "psephosamerica/1.0 (public-record ingest)"},
        )
    )
    owns = client is None
    raw_path = out_dir / RAW_COURTS_FILENAME
    written = 0
    try:
        with raw_path.open("w", encoding="utf-8") as sink:
            for court_id in court_ids:
                body = _get_with_backoff(http, f"{COURTLISTENER_API_URL}/courts/{court_id}/")
                if body and body.get("id"):
                    sink.write(json.dumps(body) + "\n")
                    written += 1
    finally:
        if owns:
            http.close()
    return written


def fetch_judges(
    *, out_dir: Path, person_ids: Iterable[int], client: Any | None = None
) -> int:  # pragma: no cover - network I/O
    """Fetch each referenced judge's ``/people/{id}/`` record to ``raw_judges.jsonl``."""
    import httpx

    http = (
        client
        if client is not None
        else httpx.Client(
            timeout=60.0,
            headers={"User-Agent": "psephosamerica/1.0 (public-record ingest)"},
        )
    )
    owns = client is None
    raw_path = out_dir / RAW_JUDGES_FILENAME
    written = 0
    seen: set[int] = set()
    try:
        with raw_path.open("w", encoding="utf-8") as sink:
            for person_id in person_ids:
                if person_id in seen:
                    continue
                seen.add(person_id)
                body = _get_with_backoff(http, f"{COURTLISTENER_API_URL}/people/{person_id}/")
                if body and body.get("id") is not None:
                    sink.write(json.dumps(body) + "\n")
                    written += 1
    finally:
        if owns:
            http.close()
    return written


def _iter_raw(out_dir: Path, filename: str) -> Iterator[dict[str, Any]]:
    raw_path = out_dir / filename
    if not raw_path.exists():
        return
    with raw_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _referenced_person_ids(out_dir: Path) -> list[int]:
    """Collect every author / joined / panel / non-participating judge id in the pull."""
    ids: set[int] = set()
    for record in _iter_raw(out_dir, RAW_OPINIONS_FILENAME):
        opinion = parse_court_opinion(record)
        if opinion is None:
            continue
        ids.update(opinion.panel_ids)
        ids.update(opinion.non_participating_ids)
        for authorship in opinion.opinions:
            if authorship.author_id is not None:
                ids.add(authorship.author_id)
            ids.update(authorship.joined_by_ids)
    return sorted(ids)


def export_courtlistener(
    *,
    out_directory: Path | str,
    courts: Iterable[str] = DEFAULT_COURTS,
    since: str = DEFAULT_SINCE,
    max_opinions_per_court: int = DEFAULT_MAX_OPINIONS_PER_COURT,
    as_of: datetime | None = None,
    client: Any | None = None,
    fetch: bool = True,
) -> CourtListenerIngestReport:  # pragma: no cover - orchestrates network I/O
    """Run the bounded CourtListener ingest: fetch -> build -> corpus + sidecars + meta.

    Phase 1 (resumable): :func:`fetch_opinions` drains recent opinions per court,
    then :func:`fetch_courts` + :func:`fetch_judges` fetch the courts and the
    judges referenced by those opinions. Pass ``fetch=False`` to rebuild from an
    already-fetched cache. Phase 2: :func:`build_court_graph` resolves courts +
    judges and emits the org/person corpus, CDC deltas, the edge sidecar, the
    opinion-node sidecar, and an honest ``ingest_meta.json``.
    """
    out_dir = Path(out_directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    observed = as_of if as_of is not None else datetime.now(tz=UTC)
    court_list = tuple(courts)

    raw_fetched = 0
    completed: list[str] = []
    notes: list[str] = []
    if fetch:
        raw_fetched, completed, notes = fetch_opinions(
            out_dir=out_dir,
            courts=court_list,
            since=since,
            max_opinions_per_court=max_opinions_per_court,
            client=client,
        )
        fetch_courts(out_dir=out_dir, court_ids=court_list, client=client)
        fetch_judges(out_dir=out_dir, person_ids=_referenced_person_ids(out_dir), client=client)
    else:
        state_path = out_dir / FETCH_STATE_FILENAME
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            completed = list(state.get("opinions_completed", []))
            notes = list(state.get("notes", []))
            raw_fetched = int(state.get("opinions_written", 0))

    rows, edges, opinions, scanned, skipped = build_court_graph(
        opinion_records=_iter_raw(out_dir, RAW_OPINIONS_FILENAME),
        court_records=_iter_raw(out_dir, RAW_COURTS_FILENAME),
        judge_records=_iter_raw(out_dir, RAW_JUDGES_FILENAME),
        first_observed_at=observed,
    )

    write_contract_corpus(rows, directory=out_dir, as_of=observed)
    deltas = diff_outputs({}, {row.canonical_id: row for row in rows})
    deltas_written = write_delta_feed(deltas, path=out_dir / DELTAS_FILENAME, append=False)

    edge_path = out_dir / COURT_EDGES_FILENAME
    with edge_path.open("w", encoding="utf-8") as handle:
        for edge in sorted(edges, key=lambda e: e.edge_id):
            handle.write(edge.model_dump_json() + "\n")

    opinions_path = out_dir / COURT_OPINIONS_FILENAME
    with opinions_path.open("w", encoding="utf-8") as handle:
        for opinion in sorted(opinions, key=lambda o: o.cluster_id):
            handle.write(json.dumps(opinion_node_row(opinion)) + "\n")

    courts_count = sum(
        1 for r in rows if any(k.startswith("courtlistener_court:") for k in r.external_ids)
    )
    judges_count = sum(
        1 for r in rows if any(k.startswith("courtlistener_person:") for k in r.external_ids)
    )
    report = CourtListenerIngestReport(
        dataset_url=COURTLISTENER_BASE_URL,
        api_url=COURTLISTENER_API_URL,
        as_of=observed.isoformat(),
        opinions_scanned=scanned,
        opinions_skipped=skipped,
        courts=courts_count,
        judges=judges_count,
        opinions=len(opinions),
        decided_by_edges=sum(1 for e in edges if e.edge_type == "decided_by"),
        authored_edges=sum(1 for e in edges if e.edge_type == "authored_opinion"),
        joined_edges=sum(1 for e in edges if e.edge_type == "joined_opinion"),
        cites_opinion_edges=sum(1 for e in edges if e.edge_type == "cites_opinion"),
        cites_statute_edges=sum(1 for e in edges if e.edge_type == "cites_statute"),
        total_edges=len(edges),
        deltas_written=deltas_written,
        is_full_corpus=False,  # always a bounded slice by design
        since=since,
        courts_scope=court_list,
        max_opinions_per_court=max_opinions_per_court,
        raw_opinions_fetched=raw_fetched,
        courts_completed=tuple(completed),
        keyless=True,
        notes=tuple(notes),
    )
    (out_dir / INGEST_META_FILENAME).write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Ingest CourtListener court records (keyless)")
    parser.add_argument("--out", default="data/exports/courtlistener")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="dateFiled lower bound")
    parser.add_argument(
        "--max-per-court",
        type=int,
        default=DEFAULT_MAX_OPINIONS_PER_COURT,
        help="cap opinions per court (bounded pull)",
    )
    parser.add_argument(
        "--courts",
        default=None,
        help="comma-separated court ids (default: SCOTUS+appeals+district+state-high)",
    )
    parser.add_argument(
        "--no-fetch", action="store_true", help="rebuild from existing raw cache only"
    )
    args = parser.parse_args(argv)
    courts = tuple(c.strip() for c in args.courts.split(",")) if args.courts else DEFAULT_COURTS
    report = export_courtlistener(
        out_directory=args.out,
        courts=courts,
        since=args.since,
        max_opinions_per_court=args.max_per_court,
        fetch=not args.no_fetch,
    )
    print(
        f"CourtListener: courts={report.courts} judges={report.judges} "
        f"opinions={report.opinions} edges={report.total_edges} "
        f"(decided_by={report.decided_by_edges} authored={report.authored_edges} "
        f"joined={report.joined_edges} cites_op={report.cites_opinion_edges} "
        f"cites_statute={report.cites_statute_edges}) "
        f"scanned={report.opinions_scanned} skipped={report.opinions_skipped} "
        f"since={report.since} cap={report.max_opinions_per_court} "
        f"notes={list(report.notes)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
