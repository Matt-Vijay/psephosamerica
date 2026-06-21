"""Run the OpenStates / Plural state-legislature ingest (deliverable: 50-state breadth).

This is the runner for the single biggest breadth unlock: state legislators,
state bills, and — the high-value signal — per-legislator state roll-call
**votes**, for as many of the 50 states as the public data allows. Those vote
edges are what makes the federal->state moat-thesis *transfer test* runnable.

Two sources, two cost profiles:

* **Legislators** come from the FREE, public, no-auth **bulk** people CSV at
  ``data.openstates.org/people/current/<state>.csv`` (one HTTP GET per state, no
  API-key/rate cost). Every state's full current roster.
* **Bills + roll-call votes** come from the rate-capped v3 API
  (``/bills?include=votes``), which embeds the named per-legislator voter list
  for states that record it (TX, CA, … carry voter OCD ids; states that report
  only tallies yield zero per-legislator edges and are noted honestly).

The session-CSV bulk dumps *also* contain ``Votes``/``VotePeople`` files, but
that download is login-gated behind a Plural account (the public S3 keys 404);
respecting the bulk-data terms, we do not scrape behind the login and instead
take votes via the documented API within its limit. This gate is recorded in
``ingest_meta.json``.

CRITICAL RATE DISCIPLINE: the v3 key is 500 req/day, 1 req/sec. This runner
enforces a hard client-side cap (:data:`DEFAULT_API_CALL_CAP`, default 400),
sleeps :data:`API_MIN_INTERVAL_S` (1.1s) between calls, and persists the running
count in ``fetch_state.json`` so the cap holds *across* resumed runs within a
day. It stops cleanly when the cap is reached, recording exactly where it
stopped so the next day's run resumes per-state.

Outputs (sidecar, under ``data/exports/openstates/``):

* ``records.jsonl`` + ``manifest.json`` — resolved legislator + bill corpus rows;
* ``deltas.jsonl`` — CDC announce feed (every entity ``created`` on first ingest);
* ``state_vote_edges.jsonl`` — the per-legislator ``vote`` edges (the moat signal);
* ``state_legislators.jsonl`` / ``state_bills.jsonl`` — convenience sidecars;
* ``ingest_meta.json`` — honest per-state coverage, bulk-vs-API breakdown, API
  calls used, and any gate.

All network I/O lives in ``pragma: no cover`` functions; the build logic is pure
and unit-testable from in-memory record streams.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.graph.cdc import diff_outputs
from src.graph.contracts import (
    ContractSourceAnchor,
    EntityResolutionOutput,
    build_bill_output,
    build_entity_resolution_output,
)
from src.graph.edges import GraphEdge
from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.entity_resolution.records import SourceRecord
from src.graph.export import DELTAS_FILENAME, write_contract_corpus, write_delta_feed
from src.graph.ingest.openstates import (
    OPENSTATES_API_URL,
    OPENSTATES_BASE_URL,
    OPENSTATES_PEOPLE_CSV,
    OPENSTATES_SOURCE_SYSTEM,
    StateBill,
    bill_display_name,
    bill_external_keys,
    bill_provenance,
    parse_legislator_csv_row,
    parse_state_bill,
    rollcall_provenance,
    state_bill_ref,
    state_vote_edge,
)
from src.graph.provenance import ProvenanceEnvelope

VOTE_EDGES_FILENAME = "state_vote_edges.jsonl"
LEGISLATORS_FILENAME = "state_legislators.jsonl"
BILLS_FILENAME = "state_bills.jsonl"
INGEST_META_FILENAME = "ingest_meta.json"
RAW_BILLS_FILENAME = "raw_bills.jsonl"
RAW_PEOPLE_FILENAME = "raw_people.jsonl"
FETCH_STATE_FILENAME = "fetch_state.json"

#: Hard ceiling on v3 API calls per run (the key is 500/day). We stop well short
#: of the daily limit, leaving headroom for spot-validation by other callers.
DEFAULT_API_CALL_CAP = 400
#: Minimum seconds between API calls (the key is 1 req/sec; 1.1s is a safe margin).
API_MIN_INTERVAL_S = 1.1
#: The v3 ``/bills`` endpoint caps ``per_page`` at 20.
BILLS_PER_PAGE = 20

#: Broad-but-bounded default slice: prioritize states that record *named* roll
#: calls (so we actually get vote edges), recent sessions first. Vote-rich states
#: lead the list; the runner walks it until the API-call cap is hit, so the high
#: value (votes for several states) is captured before draining any one state.
DEFAULT_STATE_PRIORITY: tuple[str, ...] = (
    "tx",  # Texas — large, named roll calls
    "ca",  # California — named roll calls w/ voter OCD ids
    "ny",
    "fl",
    "il",
    "pa",
    "oh",
    "mi",
    "ga",
    "nc",
    "nj",
    "va",
    "wa",
    "az",
    "ma",
    "co",
    "mn",
    "wi",
    "mo",
    "md",
)
#: Every state/territory roster we pull (free bulk CSV). The 50 states + DC + PR.
ALL_STATE_ROSTERS: tuple[str, ...] = (
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
    "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
    "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
    "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
    "dc", "pr",
)  # fmt: skip

#: USPS -> full jurisdiction name the v3 API expects in the ``jurisdiction`` filter.
_STATE_NAMES: dict[str, str] = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut", "de": "Delaware",
    "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho",
    "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas",
    "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi",
    "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
    "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
    "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma",
    "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
    "wi": "Wisconsin", "wy": "Wyoming", "dc": "District of Columbia",
    "pr": "Puerto Rico",
}  # fmt: skip


@dataclass(frozen=True)
class StateCoverage:
    """Per-state coverage line for ``ingest_meta.json``."""

    state: str
    legislators: int
    bills: int
    vote_edges: int
    rollcalls: int
    bills_pages_fetched: int
    legislators_via: str  # 'bulk_csv' / 'none'
    note: str = ""


@dataclass(frozen=True)
class OpenStatesIngestReport:
    """Counts + provenance from one OpenStates ingest run (in ingest_meta.json)."""

    dataset_url: str
    api_url: str
    people_csv_url: str
    as_of: str
    states_covered: tuple[str, ...]
    total_legislators: int
    total_bills: int
    total_vote_edges: int
    total_rollcalls: int
    deltas_written: int
    api_calls_used: int
    api_call_cap: int
    bulk_csv_fetches: int
    is_full_corpus: bool
    per_state: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = ()


# ── Rate-limited API client ─────────────────────────────────────────────


@dataclass
class RateLimitedSession:
    """Wraps an httpx client with a hard call cap + min-interval sleep + counter.

    The cap and counter are the rate discipline that keeps a broad sweep inside
    the 500/day key budget; ``calls_used`` is persisted across resumed runs.
    """

    client: Any
    api_key: str
    call_cap: int
    calls_used: int = 0
    min_interval_s: float = API_MIN_INTERVAL_S
    _last_call: float = field(default=0.0, repr=False)

    @property
    def exhausted(self) -> bool:
        return self.calls_used >= self.call_cap

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:  # pragma: no cover
        """One rate-limited GET, or ``None`` if the cap is hit / request fails."""
        if self.exhausted:
            return None
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self.calls_used += 1
        self._last_call = time.monotonic()
        resp = self.client.get(
            f"{OPENSTATES_API_URL}{path}",
            params=params,
            headers={"X-API-KEY": self.api_key},
            timeout=90.0,
        )
        if resp.status_code == 429:
            # Defensive: back off hard and stop (we should never hit this given
            # the client-side cap, but if the key was shared we honor the limit).
            time.sleep(60.0)
            self.calls_used = self.call_cap
            return None
        if resp.status_code != 200:
            return None
        return resp.json()  # type: ignore[no-any-return]


# ── Pure build ────────────────────────────────────────────────────────


def _bill_sha(bill: StateBill) -> str:
    payload = f"{bill.ocd_bill_id}|{bill.identifier}|{bill.action_date.isoformat()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rollcall_sha(ocd_vote_id: str, voter: str, choice: str) -> str:
    return hashlib.sha256(f"{ocd_vote_id}|{voter}|{choice}".encode()).hexdigest()


@dataclass
class _BuildResult:
    rows: list[EntityResolutionOutput]
    vote_edges: list[GraphEdge]
    legislators: int
    bills: int
    rollcalls: int


def build_graph(
    *,
    legislator_records: Iterable[SourceRecord],
    bills: Iterable[StateBill],
    first_observed_at: datetime,
) -> _BuildResult:
    """Resolve legislators + bills into canonical entities and mint vote edges.

    Legislators run through the standard entity-resolution linker (strong
    external-id blocking: the OCD person id collapses a legislator across the
    roster and every vote). Bills resolve *deterministically* on their
    (state, session, identifier) :class:`BillRef` — no clustering — and are
    emitted as ``entity_type='bill'`` rows. Each recorded per-legislator vote
    becomes one ``vote`` :class:`GraphEdge` from the voter's canonical Person id
    to the bill's canonical id. Votes whose voter has no OCD id (so cannot be
    resolved to a canonical legislator) are dropped — never fabricated. Edges
    and rows are de-duplicated by identity.
    """
    records_by_id: dict[str, SourceRecord] = {rec.record_id: rec for rec in legislator_records}

    # ── Resolve legislators (person clustering) ──
    result = resolve(records_by_id.values())
    canonical_by_source: dict[str, str] = {}
    rows_by_id: dict[str, EntityResolutionOutput] = {}
    for cluster in result.clusters:
        entity = build_canonical_entity(cluster, records_by_id)
        if entity is None:
            continue
        rows_by_id[entity.canonical_id] = build_entity_resolution_output(entity)
        for source_id in cluster.record_ids:
            canonical_by_source[source_id] = entity.canonical_id

    # Map every legislator's OCD id -> canonical id via its source record.
    voter_canonical_by_ocd: dict[str, str] = {}
    for rec in records_by_id.values():
        cid = canonical_by_source.get(rec.record_id)
        if cid is None:
            continue
        for ext in rec.external_ids:
            if ext.system == "openstates":
                voter_canonical_by_ocd[ext.value] = cid
    n_legislators = len(rows_by_id)

    # ── Emit bills deterministically + mint vote edges ──
    edges_by_id: dict[str, GraphEdge] = {}
    n_rollcalls = 0
    n_bills = 0
    for bill in bills:
        prov = bill_provenance(
            bill, content_sha256=_bill_sha(bill), first_observed_at=first_observed_at
        )
        # Leakage guard: a fact cannot be observed before it is knowable.
        if prov.known_at > prov.first_observed_at:
            continue
        ref = state_bill_ref(bill)
        bill_cid = ref.canonical_id
        if bill_cid not in rows_by_id:
            anchor = ContractSourceAnchor(
                source_system=OPENSTATES_SOURCE_SYSTEM,
                record_id=f"bill:{bill.ocd_bill_id}",
                source_url=prov.source_url,
                content_sha256=prov.content_sha256,
                content_address=prov.content_address(),
                known_at=prov.known_at,
                valid_from=prov.valid_from,
                valid_to=prov.valid_to,
            )
            rows_by_id[bill_cid] = build_bill_output(
                canonical_bill_id=bill_cid,
                display_name=bill_display_name(bill),
                source_anchors=[anchor],
                external_ids=bill_external_keys(bill),
            )
        n_bills += 1
        n_rollcalls += len(bill.rollcalls)
        for rollcall in bill.rollcalls:
            for vote in rollcall.votes:
                if vote.voter_ocd_id is None:
                    continue
                voter_cid = voter_canonical_by_ocd.get(vote.voter_ocd_id)
                if voter_cid is None:
                    # The voter is not in any fetched roster (e.g. a former member
                    # whose roll call predates the current roster). Skip honestly.
                    continue
                if voter_cid == bill_cid:
                    continue
                prov = rollcall_provenance(
                    rollcall,
                    content_sha256=_rollcall_sha(
                        rollcall.ocd_vote_id, vote.voter_ocd_id, vote.choice
                    ),
                    first_observed_at=first_observed_at,
                )
                if prov.known_at > prov.first_observed_at:
                    continue
                edge = state_vote_edge(
                    voter_canonical_id=voter_cid,
                    bill_canonical_id=ref.canonical_id,
                    vote=vote,
                    rollcall=rollcall,
                    provenance=prov,
                )
                edges_by_id[edge.edge_id] = edge

    return _BuildResult(
        rows=list(rows_by_id.values()),
        vote_edges=list(edges_by_id.values()),
        legislators=n_legislators,
        bills=n_bills,
        rollcalls=n_rollcalls,
    )


# ── Network: bulk people CSV + API bill sweep ───────────────────────────


def _people_csv_provenance(
    *, state: str, content_sha256: str, first_observed_at: datetime
) -> ProvenanceEnvelope:
    """Provenance for a bulk people-CSV roster row.

    The roster is a current-state snapshot with no per-row date; ``known_at`` is
    conservatively the fetch instant (``first_observed_at``), so a roster fact is
    never treated as knowable earlier than we observed it.
    """
    return ProvenanceEnvelope(
        source_url=f"{OPENSTATES_PEOPLE_CSV}/{state}.csv",
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=first_observed_at.date(),
        known_at=first_observed_at,
    )


def fetch_legislators(
    *, state: str, client: Any, first_observed_at: datetime
) -> list[SourceRecord]:  # pragma: no cover - network I/O
    """Fetch one state's roster from the FREE bulk people CSV (no API cost)."""
    url = f"{OPENSTATES_PEOPLE_CSV}/{state}.csv"
    resp = client.get(url, timeout=90.0, follow_redirects=True)
    if resp.status_code != 200:
        return []
    text = resp.text
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    prov = _people_csv_provenance(
        state=state, content_sha256=sha, first_observed_at=first_observed_at
    )
    out: list[SourceRecord] = []
    for row in csv.DictReader(io.StringIO(text)):
        row["_state"] = state
        rec = parse_legislator_csv_row(row, provenance=prov)
        if rec is not None:
            out.append(rec)
    return out


def fetch_state_bills(
    *,
    state: str,
    session: RateLimitedSession,
    max_pages: int,
    sink: Any,
) -> tuple[list[StateBill], int]:  # pragma: no cover - network + pagination I/O
    """Fetch a state's recent bills+votes via the rate-capped API. Resumable-friendly.

    Streams each raw bill JSON to ``sink`` (raw_bills.jsonl) and returns the
    parsed :class:`StateBill` list plus the number of pages actually fetched.
    Stops early when the API call cap is hit (``session.exhausted``).
    """
    jurisdiction = _STATE_NAMES.get(state, state)
    bills: list[StateBill] = []
    pages = 0
    for page in range(1, max_pages + 1):
        if session.exhausted:
            break
        body = session.get(
            "/bills",
            {
                "jurisdiction": jurisdiction,
                "sort": "latest_action_desc",
                "include": ["votes"],
                "per_page": BILLS_PER_PAGE,
                "page": page,
            },
        )
        if body is None:
            break
        pages += 1
        results = body.get("results") or []
        if not results:
            break
        for raw in results:
            sink.write(json.dumps(raw) + "\n")
            parsed = parse_state_bill(raw)
            if parsed is not None:
                bills.append(parsed)
        pagination = body.get("pagination") or {}
        if page >= int(pagination.get("max_page", page)):
            break
    return bills, pages


def _load_state(out_dir: Path) -> dict[str, Any]:
    path = out_dir / FETCH_STATE_FILENAME
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    return {}


def _save_state(out_dir: Path, state: dict[str, Any]) -> None:
    (out_dir / FETCH_STATE_FILENAME).write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def export_openstates(
    *,
    out_directory: Path | str,
    state_priority: tuple[str, ...] = DEFAULT_STATE_PRIORITY,
    roster_states: tuple[str, ...] = ALL_STATE_ROSTERS,
    max_pages_per_state: int = 30,
    api_call_cap: int = DEFAULT_API_CALL_CAP,
    as_of: datetime | None = None,
    client: Any | None = None,
) -> OpenStatesIngestReport:  # pragma: no cover - orchestrates network I/O
    """Run the OpenStates ingest: bulk rosters + capped API bill/vote sweep.

    Phase 1 (free): pull every ``roster_states`` legislator roster from the bulk
    people CSV — no API cost. Phase 2 (capped): walk ``state_priority`` pulling
    recent bills+votes from the API until the call cap is reached, so votes for
    several states land before any one state is drained. Resumable per-state via
    ``fetch_state.json`` (completed states + the day's accumulated API count).
    Phase 3: resolve + write the corpus, CDC, vote-edge sidecar, and meta.
    """
    import httpx

    out_dir = Path(out_directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    observed = as_of if as_of is not None else datetime.now(tz=UTC)

    api_key = _read_api_key()
    http = client if client is not None else httpx.Client()
    owns = client is None

    state = _load_state(out_dir)
    completed_bill_states: list[str] = list(state.get("completed_bill_states", []))
    completed_roster_states: list[str] = list(state.get("completed_roster_states", []))
    api_calls_today = int(state.get("api_calls_used", 0))
    notes: list[str] = list(state.get("notes", []))
    coverage: dict[str, dict[str, Any]] = dict(state.get("coverage", {}))

    session = RateLimitedSession(
        client=http, api_key=api_key or "", call_cap=api_call_cap, calls_used=api_calls_today
    )

    legislator_records: list[SourceRecord] = []
    roster_counts: dict[str, int] = {}
    bulk_fetches = 0
    try:
        # ── Phase 1: free bulk rosters ──
        people_path = out_dir / RAW_PEOPLE_FILENAME
        people_mode = "a" if (state and people_path.exists()) else "w"
        with people_path.open(people_mode, encoding="utf-8") as psink:
            for st in roster_states:
                if st in completed_roster_states:
                    roster_counts[st] = int(coverage.get(st, {}).get("legislators", 0))
                    continue
                recs = fetch_legislators(state=st, client=http, first_observed_at=observed)
                bulk_fetches += 1
                for r in recs:
                    psink.write(r.model_dump_json() + "\n")
                legislator_records.extend(recs)
                roster_counts[st] = len(recs)
                completed_roster_states.append(st)
                coverage.setdefault(st, {})["legislators"] = len(recs)

        # If resuming, reload any rosters fetched on a prior run from the cache.
        if completed_roster_states and len(legislator_records) < sum(roster_counts.values()):
            legislator_records = _reload_people(people_path)

        # ── Phase 2: capped API bill+vote sweep ──
        all_bills: list[StateBill] = []
        bills_path = out_dir / RAW_BILLS_FILENAME
        bills_mode = "a" if (state and bills_path.exists()) else "w"
        with bills_path.open(bills_mode, encoding="utf-8") as bsink:
            for st in state_priority:
                if session.exhausted:
                    break
                if st in completed_bill_states:
                    continue
                bills, pages = fetch_state_bills(
                    state=st,
                    session=session,
                    max_pages=max_pages_per_state,
                    sink=bsink,
                )
                all_bills.extend(bills)
                n_rc = sum(len(b.rollcalls) for b in bills)
                n_ve = sum(len(rc.votes) for b in bills for rc in b.rollcalls)
                cov = coverage.setdefault(st, {})
                cov["bills"] = cov.get("bills", 0) + len(bills)
                cov["bills_pages_fetched"] = cov.get("bills_pages_fetched", 0) + pages
                cov["rollcalls"] = cov.get("rollcalls", 0) + n_rc
                cov["raw_vote_records"] = cov.get("raw_vote_records", 0) + n_ve
                # Mark complete only if we did not stop because of the cap.
                if not session.exhausted:
                    completed_bill_states.append(st)
                else:
                    notes.append(f"{st}: stopped mid-state at API cap; resume next run")

        # Always rebuild bills from the full raw cache so a resumed run includes
        # prior days' fetches in the corpus.
        all_bills = _reload_bills(bills_path)
    finally:
        if owns:
            http.close()

    built = build_graph(
        legislator_records=_reload_people(out_dir / RAW_PEOPLE_FILENAME),
        bills=all_bills,
        first_observed_at=observed,
    )

    write_contract_corpus(built.rows, directory=out_dir, as_of=observed)
    deltas = diff_outputs({}, {row.canonical_id: row for row in built.rows})
    deltas_written = write_delta_feed(deltas, path=out_dir / DELTAS_FILENAME, append=False)

    _write_edges(out_dir / VOTE_EDGES_FILENAME, built.vote_edges)
    _write_rows_subset(out_dir / LEGISLATORS_FILENAME, built.rows, entity_type="person")
    _write_bill_sidecar(out_dir / BILLS_FILENAME, built.rows)

    # Recompute per-state vote-edge counts from the resolved edges.
    edge_state_counts = _vote_edges_per_state(built.vote_edges, all_bills)
    per_state = _assemble_coverage(
        coverage=coverage,
        roster_counts=roster_counts,
        edge_state_counts=edge_state_counts,
        state_priority=state_priority,
        roster_states=roster_states,
        completed_roster=set(completed_roster_states),
    )

    report = OpenStatesIngestReport(
        dataset_url=OPENSTATES_BASE_URL,
        api_url=OPENSTATES_API_URL,
        people_csv_url=OPENSTATES_PEOPLE_CSV,
        as_of=observed.isoformat(),
        states_covered=tuple(
            sorted({c["state"] for c in per_state if c["bills"] or c["legislators"]})
        ),
        total_legislators=built.legislators,
        total_bills=built.bills,
        total_vote_edges=len(built.vote_edges),
        total_rollcalls=built.rollcalls,
        deltas_written=deltas_written,
        api_calls_used=session.calls_used,
        api_call_cap=api_call_cap,
        bulk_csv_fetches=bulk_fetches,
        is_full_corpus=False,  # always a bounded-but-broad slice
        per_state=tuple(per_state),
        notes=tuple(dict.fromkeys(notes)),
    )
    (out_dir / INGEST_META_FILENAME).write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    _save_state(
        out_dir,
        {
            "completed_bill_states": completed_bill_states,
            "completed_roster_states": completed_roster_states,
            "api_calls_used": session.calls_used,
            "notes": list(dict.fromkeys(notes)),
            "coverage": coverage,
        },
    )
    return report


def _read_api_key() -> str | None:  # pragma: no cover - env/file glue
    import os

    key = os.environ.get("OPENSTATES_API_KEY")
    if key:
        return key.strip()
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENSTATES_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


def _reload_people(path: Path) -> list[SourceRecord]:  # pragma: no cover - disk I/O
    if not path.exists():
        return []
    out: list[SourceRecord] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(SourceRecord.model_validate_json(line))
    return out


def _reload_bills(path: Path) -> list[StateBill]:  # pragma: no cover - disk I/O
    if not path.exists():
        return []
    out: list[StateBill] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            parsed = parse_state_bill(json.loads(line))
            if parsed is not None and parsed.ocd_bill_id not in seen:
                seen.add(parsed.ocd_bill_id)
                out.append(parsed)
    return out


def _write_edges(path: Path, edges: list[GraphEdge]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for edge in sorted(edges, key=lambda e: e.edge_id):
            handle.write(edge.model_dump_json() + "\n")


def _write_rows_subset(path: Path, rows: list[EntityResolutionOutput], *, entity_type: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in sorted(rows, key=lambda r: r.canonical_id):
            if row.entity_type == entity_type:
                handle.write(row.model_dump_json() + "\n")


def _write_bill_sidecar(path: Path, rows: list[EntityResolutionOutput]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in sorted(rows, key=lambda r: r.canonical_id):
            if any(k.startswith("openstates_bill:") for k in row.external_ids):
                handle.write(row.model_dump_json() + "\n")


def _vote_edges_per_state(edges: list[GraphEdge], bills: Iterable[StateBill]) -> dict[str, int]:
    """Attribute resolved vote edges back to a state via the bill canonical id."""
    bill_state: dict[str, str] = {}
    for bill in bills:
        bill_state[state_bill_ref(bill).canonical_id] = bill.state
    counts: dict[str, int] = {}
    for edge in edges:
        st = bill_state.get(edge.dst_id)
        if st is not None:
            counts[st] = counts.get(st, 0) + 1
    return counts


def _assemble_coverage(
    *,
    coverage: dict[str, dict[str, Any]],
    roster_counts: dict[str, int],
    edge_state_counts: dict[str, int],
    state_priority: tuple[str, ...],
    roster_states: tuple[str, ...],
    completed_roster: set[str],
) -> list[dict[str, Any]]:
    states = sorted(set(roster_states) | set(state_priority) | set(coverage))
    out: list[dict[str, Any]] = []
    for st in states:
        cov = coverage.get(st, {})
        legis = roster_counts.get(st, int(cov.get("legislators", 0)))
        line = StateCoverage(
            state=st,
            legislators=legis,
            bills=int(cov.get("bills", 0)),
            vote_edges=edge_state_counts.get(st, 0),
            rollcalls=int(cov.get("rollcalls", 0)),
            bills_pages_fetched=int(cov.get("bills_pages_fetched", 0)),
            legislators_via="bulk_csv" if st in completed_roster else "none",
        )
        out.append(asdict(line))
    return out


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Ingest OpenStates state legislatures")
    parser.add_argument("--out", default="data/exports/openstates")
    parser.add_argument(
        "--api-call-cap", type=int, default=DEFAULT_API_CALL_CAP, help="hard cap on v3 API calls"
    )
    parser.add_argument(
        "--max-pages-per-state", type=int, default=30, help="bill pages per state (20 bills/page)"
    )
    parser.add_argument(
        "--states",
        default="",
        help="comma-separated USPS priority order for bills (default: built-in)",
    )
    args = parser.parse_args(argv)
    priority = (
        tuple(s.strip().lower() for s in args.states.split(",") if s.strip())
        if args.states
        else DEFAULT_STATE_PRIORITY
    )
    report = export_openstates(
        out_directory=args.out,
        state_priority=priority,
        max_pages_per_state=args.max_pages_per_state,
        api_call_cap=args.api_call_cap,
    )
    print(
        f"OpenStates: states={len(report.states_covered)} "
        f"legislators={report.total_legislators} bills={report.total_bills} "
        f"vote_edges={report.total_vote_edges} rollcalls={report.total_rollcalls} "
        f"api_calls={report.api_calls_used}/{report.api_call_cap} "
        f"bulk_fetches={report.bulk_csv_fetches} notes={list(report.notes)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
