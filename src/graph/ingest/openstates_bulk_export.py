"""Run the OpenStates **bulk session-CSV** ingest (rate-limit-free 50-state votes).

This is the runner for the breadth unlock the rate-capped API path cannot reach:
the *complete* recorded roll-call corpus for every (state, session) ZIP in the
bulk dump under ``data/raw/openstates_bulk/*.zip`` — all 50 states + DC + PR,
decades of sessions, tens of millions of per-legislator vote edges, with no API
budget. Those state vote edges are what the federal->state moat-thesis *transfer
test* consumes.

It streams each ZIP **in memory** (no disk extraction), reads only the
``*_bills.csv`` / ``*_votes.csv`` / ``*_vote_people.csv`` members, and joins them
row-by-row in :func:`~src.graph.ingest.openstates_bulk.parse_session` so a single
session never loads its full per-legislator vote list into RAM. Resolved vote
edges are **streamed to disk** as they are minted (the corpus is far too large to
hold in memory), deduplicated against both each other and the existing
API-sourced sidecar by ``(vote_event_id, voter_id)`` — i.e. the edge's
``external_key`` (the ``ocd-vote`` id) paired with its canonical voter id.

Why the dedup key works without re-resolving the API people: a legislator's
canonical id is a pure function of its OpenStates ``ocd-person`` id (the strong
external-id key), so a bulk voter and an API roster legislator with the same OCD
id collapse to the *same* canonical ``ce-`` id. The combined edge set therefore
dedupes cleanly on ``(external_key, src_id)``.

Resumability: progress is checkpointed per (state, session) ZIP in
``bulk_fetch_state.json``; a stalled run continues from the next unprocessed ZIP.
ZIPs are processed **recent-session-first** (2015+ descending, then older) so
partial completion is maximally useful for the transfer test.

Outputs (sidecar, under ``data/exports/openstates/``; the API sidecar is never
clobbered):

* ``state_vote_edges_combined.jsonl`` — API + bulk vote edges, deduped (the moat
  signal at full breadth);
* ``bulk_records.jsonl`` — resolved bulk legislator + bill rows (large; gitignored);
* ``bulk_deltas.jsonl`` — CDC announce feed for entities new in the bulk corpus;
* ``bulk_ingest_meta.json`` — per-state table (legislators, bills, vote edges,
  sessions) + grand totals + runtime;
* ``bulk_fetch_state.json`` — the per-ZIP resume checkpoint.

All ZIP/disk I/O lives in the orchestrator; the parse/join/edge-mint logic is the
pure, unit-tested :mod:`~src.graph.ingest.openstates_bulk` adapter.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.graph.cdc import diff_outputs
from src.graph.contracts import (
    ContractSourceAnchor,
    EntityResolutionOutput,
    build_bill_output,
)
from src.graph.edges import GraphEdge
from src.graph.entity_resolution.canonical import build_canonical_entity
from src.graph.entity_resolution.linker import resolve
from src.graph.entity_resolution.records import SourceRecord
from src.graph.export import write_delta_feed
from src.graph.ingest.openstates import (
    OPENSTATES_SOURCE_SYSTEM,
    StateBill,
    bill_display_name,
    bill_external_keys,
    bill_provenance,
    rollcall_provenance,
    state_bill_ref,
    state_vote_edge,
)
from src.graph.ingest.openstates_bulk import (
    OPENSTATES_BULK_SOURCE,
    csv_rows,
    legislator_records_from_voters,
    now_utc,
    parse_session,
    session_source_url,
    state_from_member_path,
    voter_provenance,
)
from src.graph.provenance import ProvenanceEnvelope

DEFAULT_RAW_DIR = Path("data/raw/openstates_bulk")
DEFAULT_OUT_DIR = Path("data/exports/openstates")

#: Existing API-sourced sidecar (read-only here; never clobbered).
API_EDGES_FILENAME = "state_vote_edges.jsonl"
API_LEGISLATORS_FILENAME = "state_legislators.jsonl"

#: Bulk + combined outputs.
COMBINED_EDGES_FILENAME = "state_vote_edges_combined.jsonl"
BULK_RECORDS_FILENAME = "bulk_records.jsonl"
BULK_DELTAS_FILENAME = "bulk_deltas.jsonl"
BULK_META_FILENAME = "bulk_ingest_meta.json"
CHECKPOINT_FILENAME = "bulk_fetch_state.json"

#: Sessions at/after this year are processed first (most useful for the transfer test).
RECENT_SESSION_YEAR = 2015


# ── ZIP discovery + recency ordering ────────────────────────────────────


@dataclass(frozen=True)
class _ZipJob:
    path: Path
    label: str  # the ZIP stem, e.g. "Alabama_2023_Regular_Session"
    year: int  # best-effort latest year in the label (for recency ordering)


def _latest_year(label: str) -> int:
    """Best-effort latest 4-digit year in a ZIP label, or 0 if none."""
    years = [int(m) for m in re.findall(r"(?:19|20)\d{2}", label)]
    return max(years) if years else 0


def discover_zips(raw_dir: Path) -> list[_ZipJob]:
    """All session ZIPs ordered **recent-first** (2015+ desc, then older desc).

    Recent sessions are the highest-value slice for the transfer test, so a
    partial run still yields the most useful edges. Ties break on label for a
    deterministic, resumable order.
    """
    jobs = [
        _ZipJob(path=p, label=p.stem, year=_latest_year(p.stem))
        for p in sorted(raw_dir.glob("*.zip"))
    ]

    def sort_key(job: _ZipJob) -> tuple[int, int, str]:
        recent = 0 if job.year >= RECENT_SESSION_YEAR else 1
        return (recent, -job.year, job.label)

    return sorted(jobs, key=sort_key)


def _member(zf: zipfile.ZipFile, suffix: str) -> str | None:
    for name in zf.namelist():
        if name.endswith(suffix):
            return name
    return None


def _open_csv(zf: zipfile.ZipFile, name: str) -> Iterator[dict[str, Any]]:
    with zf.open(name) as raw:
        # csv.DictReader fully consumes the stream within this context; we yield
        # eagerly into a generator the caller drains while the file is open.
        text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        yield from csv_rows(text)


# ── Edge minting (streamed) ─────────────────────────────────────────────


def _bill_sha(bill: StateBill) -> str:
    payload = f"{bill.ocd_bill_id}|{bill.identifier}|{bill.action_date.isoformat()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rollcall_sha(ocd_vote_id: str, voter: str, choice: str) -> str:
    return hashlib.sha256(f"{ocd_vote_id}|{voter}|{choice}".encode()).hexdigest()


@dataclass
class _EdgeWriter:
    """Streams deduped vote edges to the combined sidecar, tracking per-state counts.

    Holds only a ``seen`` set of ``(external_key, src_id)`` keys — the dedup
    identity — plus integer counters, so memory stays bounded across tens of
    millions of edges.
    """

    handle: Any
    seen: set[tuple[str, str]] = field(default_factory=set)
    written: int = 0
    per_state: dict[str, int] = field(default_factory=dict)

    def seed_from_api(self, path: Path) -> int:
        """Copy the existing API edges through verbatim and seed the dedup set.

        Returns the number of API edges carried into the combined sidecar. The
        API sidecar file itself is untouched.
        """
        if not path.exists():
            return 0
        count = 0
        with path.open(encoding="utf-8") as src:
            for line in src:
                line = line.rstrip("\n")
                if not line:
                    continue
                obj = json.loads(line)
                key = (obj.get("external_key") or "", obj.get("src_id") or "")
                if key in self.seen:
                    continue
                self.seen.add(key)
                self.handle.write(line + "\n")
                count += 1
        self.written += count
        return count

    def add(self, edge: GraphEdge, *, state: str) -> bool:
        key = (edge.external_key or "", edge.src_id)
        if key in self.seen:
            return False
        self.seen.add(key)
        self.handle.write(edge.model_dump_json() + "\n")
        self.written += 1
        self.per_state[state] = self.per_state.get(state, 0) + 1
        return True


# ── Per-state accumulation ──────────────────────────────────────────────


@dataclass
class _StateAccumulator:
    """Per-state running counts for the report (no per-edge object retention)."""

    legislators: set[str] = field(default_factory=set)  # canonical ce ids
    bills: int = 0
    rollcalls: int = 0
    sessions: set[str] = field(default_factory=set)


# ── Checkpoint ──────────────────────────────────────────────────────────


def _load_checkpoint(out_dir: Path) -> dict[str, Any]:
    path = out_dir / CHECKPOINT_FILENAME
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    return {}


def _save_checkpoint(out_dir: Path, payload: dict[str, Any]) -> None:
    (out_dir / CHECKPOINT_FILENAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# ── Report ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BulkStateCoverage:
    state: str
    legislators: int
    bills: int
    vote_edges: int
    rollcalls: int
    sessions: int


@dataclass(frozen=True)
class OpenStatesBulkReport:
    as_of: str
    raw_dir: str
    zips_total: int
    zips_processed: int
    total_legislators: int
    total_bills: int
    total_vote_edges: int  # combined (API + bulk), deduped
    bulk_vote_edges: int  # net-new edges contributed by the bulk dump
    api_vote_edges: int  # edges carried in from the API sidecar
    total_rollcalls: int
    distinct_states_with_votes: int
    states_with_votes: tuple[str, ...]
    deltas_written: int
    runtime_seconds: float
    per_state: tuple[dict[str, Any], ...]
    notes: tuple[str, ...] = ()


# ── Orchestrator ────────────────────────────────────────────────────────


def _bill_row(bill: StateBill, *, observed: datetime) -> tuple[str, EntityResolutionOutput]:
    prov = bill_provenance(bill, content_sha256=_bill_sha(bill), first_observed_at=observed)
    ref = state_bill_ref(bill)
    anchor = ContractSourceAnchor(
        source_system=OPENSTATES_BULK_SOURCE,
        record_id=f"bill:{bill.ocd_bill_id}",
        source_url=prov.source_url,
        content_sha256=prov.content_sha256,
        content_address=prov.content_address(),
        known_at=prov.known_at,
        valid_from=prov.valid_from,
        valid_to=prov.valid_to,
    )
    row = build_bill_output(
        canonical_bill_id=ref.canonical_id,
        display_name=bill_display_name(bill),
        source_anchors=[anchor],
        external_ids=bill_external_keys(bill),
    )
    return ref.canonical_id, row


def export_openstates_bulk(
    *,
    raw_dir: Path | str = DEFAULT_RAW_DIR,
    out_directory: Path | str = DEFAULT_OUT_DIR,
    as_of: datetime | None = None,
    limit_zips: int | None = None,
    resume: bool = True,
) -> OpenStatesBulkReport:  # pragma: no cover - orchestrates ZIP/disk I/O
    """Parse every bulk session ZIP into combined state vote edges + a report.

    Streams edges to ``state_vote_edges_combined.jsonl`` (seeded with the existing
    API edges), resolves legislators + bills per ZIP, checkpoints per ZIP, and
    writes the bulk record corpus, CDC announce feed, and per-state meta. The API
    sidecar files are read but never modified.
    """
    started = time.monotonic()
    observed = as_of if as_of is not None else now_utc()
    raw = Path(raw_dir)
    out_dir = Path(out_directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = discover_zips(raw)
    if limit_zips is not None:
        jobs = jobs[:limit_zips]

    checkpoint = _load_checkpoint(out_dir) if resume else {}
    done: set[str] = set(checkpoint.get("completed_zips", []))

    # Resume the combined sidecar / records / deltas only when continuing a run;
    # a fresh run rewrites them. We always re-seed the dedup set from whatever the
    # combined file already holds so a resumed run never double-writes.
    appending = resume and bool(done) and (out_dir / COMBINED_EDGES_FILENAME).exists()

    # ── Build the OCD-person -> canonical-id map from the API legislator sidecar.
    # This lets bulk voters reuse the exact canonical ids the API run minted, so
    # the two edge sets dedupe on (ocd-vote, ce-id) without re-resolving people.
    ocd_to_canonical: dict[str, str] = _load_api_canonical_map(out_dir / API_LEGISLATORS_FILENAME)

    bill_rows: dict[str, EntityResolutionOutput] = {}
    per_state: dict[str, _StateAccumulator] = {}
    notes: list[str] = list(checkpoint.get("notes", []))
    zips_processed = int(checkpoint.get("zips_processed", 0)) if appending else 0

    edges_mode = "a" if appending else "w"
    records_mode = "a" if appending else "w"
    edges_path = out_dir / COMBINED_EDGES_FILENAME
    records_path = out_dir / BULK_RECORDS_FILENAME

    with edges_path.open(edges_mode, encoding="utf-8") as edge_handle:
        writer = _EdgeWriter(handle=edge_handle)
        if appending:
            # Re-seed dedup identity from the already-written combined edges.
            api_seed = _reseed_writer(writer, edges_path_existing=edges_path)
        else:
            api_seed = writer.seed_from_api(out_dir / API_EDGES_FILENAME)

        with records_path.open(records_mode, encoding="utf-8") as rec_handle:
            for job in jobs:
                if job.label in done:
                    continue
                try:
                    _process_zip(
                        job=job,
                        observed=observed,
                        ocd_to_canonical=ocd_to_canonical,
                        writer=writer,
                        bill_rows=bill_rows,
                        per_state=per_state,
                        rec_handle=rec_handle,
                    )
                except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
                    notes.append(f"{job.label}: skipped ({type(exc).__name__}: {exc})")
                    continue
                done.add(job.label)
                zips_processed += 1
                _save_checkpoint(
                    out_dir,
                    {
                        "completed_zips": sorted(done),
                        "zips_processed": zips_processed,
                        "notes": list(dict.fromkeys(notes)),
                    },
                )

    # Bill rows accumulate in memory (hundreds of thousands at most) and are
    # appended to the bulk record corpus alongside the streamed legislator rows.
    with records_path.open("a", encoding="utf-8") as rec_handle:
        for row in sorted(bill_rows.values(), key=lambda r: r.canonical_id):
            rec_handle.write(row.model_dump_json() + "\n")

    # ── CDC announce: every bulk row is "created" on first ingest. ──
    all_rows = list(bill_rows.values())
    deltas = diff_outputs({}, {r.canonical_id: r for r in all_rows})
    deltas_written = write_delta_feed(deltas, path=out_dir / BULK_DELTAS_FILENAME, append=False)

    states_with_votes = tuple(sorted(s for s, n in writer.per_state.items() if n > 0))
    coverage = _assemble_coverage(per_state=per_state, edge_counts=writer.per_state)
    report = OpenStatesBulkReport(
        as_of=observed.isoformat(),
        raw_dir=str(raw),
        zips_total=len(jobs),
        zips_processed=zips_processed,
        total_legislators=sum(len(a.legislators) for a in per_state.values()),
        total_bills=sum(a.bills for a in per_state.values()),
        total_vote_edges=writer.written,
        bulk_vote_edges=writer.written - api_seed,
        api_vote_edges=api_seed,
        total_rollcalls=sum(a.rollcalls for a in per_state.values()),
        distinct_states_with_votes=len(states_with_votes),
        states_with_votes=states_with_votes,
        deltas_written=deltas_written,
        runtime_seconds=round(time.monotonic() - started, 1),
        per_state=tuple(coverage),
        notes=tuple(dict.fromkeys(notes)),
    )
    (out_dir / BULK_META_FILENAME).write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _process_zip(
    *,
    job: _ZipJob,
    observed: datetime,
    ocd_to_canonical: dict[str, str],
    writer: _EdgeWriter,
    bill_rows: dict[str, EntityResolutionOutput],
    per_state: dict[str, _StateAccumulator],
    rec_handle: Any,
) -> None:  # pragma: no cover - ZIP/disk I/O
    """Stream-parse one session ZIP and mint+write its vote edges."""
    with zipfile.ZipFile(job.path) as zf:
        vp_name = _member(zf, "_vote_people.csv")
        votes_name = _member(zf, "_votes.csv")
        bills_name = _member(zf, "_bills.csv")
        if vp_name is None or votes_name is None or bills_name is None:
            return
        state = state_from_member_path(vp_name)
        if state is None:
            return  # federal (US) dump or unrecognized — skip.
        source_url = session_source_url(state=state, session_label=job.label)

        parsed = parse_session(
            state=state,
            bills_rows=_open_csv(zf, bills_name),
            votes_rows=_open_csv(zf, votes_name),
            vote_people_rows=_open_csv(zf, vp_name),
            session_label=job.label,
            source_url=source_url,
        )

    # Resolve this session's voters to canonical ids (reusing API ids by OCD id).
    prov = voter_provenance(
        state=state,
        source_url=source_url,
        content_sha256=hashlib.sha256(job.label.encode("utf-8")).hexdigest(),
        first_observed_at=observed,
    )
    voter_canonical = _resolve_voters(
        voters=parsed.voters, state=state, provenance=prov, ocd_to_canonical=ocd_to_canonical
    )

    acc = per_state.setdefault(state, _StateAccumulator())
    acc.legislators.update(voter_canonical.values())

    # Emit bills + stream their vote edges.
    for bill in parsed.bills:
        if bill.action_date > observed.date():
            continue  # leakage guard.
        bill_cid, row = _bill_row(bill, observed=observed)
        if bill_cid not in bill_rows:
            bill_rows[bill_cid] = row
        acc.bills += 1
        acc.sessions.add(bill.session)
        for rollcall in bill.rollcalls:
            if rollcall.start_date > observed.date():
                continue
            acc.rollcalls += 1
            for vote in rollcall.votes:
                if vote.voter_ocd_id is None:
                    continue
                voter_cid = voter_canonical.get(vote.voter_ocd_id)
                if voter_cid is None or voter_cid == bill_cid:
                    continue
                edge_prov = rollcall_provenance(
                    rollcall,
                    content_sha256=_rollcall_sha(
                        rollcall.ocd_vote_id, vote.voter_ocd_id, vote.choice
                    ),
                    first_observed_at=observed,
                )
                edge = state_vote_edge(
                    voter_canonical_id=voter_cid,
                    bill_canonical_id=bill_cid,
                    vote=vote,
                    rollcall=rollcall,
                    provenance=edge_prov,
                )
                writer.add(edge, state=state)

    # Stream the legislator rows for this session's voters into the record corpus.
    _write_voter_rows(
        rec_handle=rec_handle,
        voters=parsed.voters,
        voter_canonical=voter_canonical,
        prov=prov,
    )


def _resolve_voters(
    *,
    voters: dict[str, str],
    state: str,
    provenance: ProvenanceEnvelope,
    ocd_to_canonical: dict[str, str],
) -> dict[str, str]:  # pragma: no cover - thin glue over the linker
    """Map each voter OCD id to its canonical id, reusing API ids where known.

    Voters already in the API map reuse that canonical id; the rest are resolved
    among themselves (OCD-id-keyed clustering, deterministic) and cached back into
    the shared map so the same person reuses one id across every session/ZIP.
    """
    out: dict[str, str] = {}
    unknown = {ocd: name for ocd, name in voters.items() if ocd not in ocd_to_canonical}
    for ocd in voters:
        if ocd in ocd_to_canonical:
            out[ocd] = ocd_to_canonical[ocd]
    if unknown:
        recs = legislator_records_from_voters(voters=unknown, state=state, provenance=provenance)
        rbid: dict[str, SourceRecord] = {r.record_id: r for r in recs}
        result = resolve(rbid.values())
        for cluster in result.clusters:
            entity = build_canonical_entity(cluster, rbid)
            if entity is None:
                continue
            for rid in cluster.record_ids:
                for ext in rbid[rid].external_ids:
                    if ext.system == OPENSTATES_SOURCE_SYSTEM:
                        ocd_to_canonical[ext.value] = entity.canonical_id
                        out[ext.value] = entity.canonical_id
    return out


def _write_voter_rows(
    *,
    rec_handle: Any,
    voters: dict[str, str],
    voter_canonical: dict[str, str],
    prov: ProvenanceEnvelope,
) -> None:  # pragma: no cover - disk I/O
    """Append one resolved-legislator record line per voter (canonical-id keyed).

    Dedup across sessions happens at merge time; here we just emit the row so the
    bulk record corpus carries every legislator that cast a recorded vote.
    """
    for ocd, name in voters.items():
        cid = voter_canonical.get(ocd)
        if cid is None:
            continue
        anchor = ContractSourceAnchor(
            source_system=OPENSTATES_BULK_SOURCE,
            record_id=f"person:{ocd}",
            source_url=prov.source_url,
            content_sha256=prov.content_sha256,
            content_address=prov.content_address(),
            known_at=prov.known_at,
            valid_from=prov.valid_from,
            valid_to=prov.valid_to,
        )
        row = EntityResolutionOutput(
            canonical_id=cid,
            entity_type="person",
            display_name=name,
            external_ids=[f"openstates:{ocd}"],
            known_at=anchor.known_at,
            source_anchors=[anchor],
        )
        rec_handle.write(row.model_dump_json() + "\n")


def _load_api_canonical_map(path: Path) -> dict[str, str]:
    """OCD-person id -> canonical id from the API legislator sidecar (if present)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            cid = obj.get("canonical_id")
            for ext in obj.get("external_ids", []):
                if isinstance(ext, str) and ext.startswith("openstates:ocd-person/"):
                    out[ext.split(":", 1)[1]] = cid
    return out


def _reseed_writer(writer: _EdgeWriter, *, edges_path_existing: Path) -> int:
    """Seed the dedup set from an already-written combined edge file (resume)."""
    api_like = 0
    if not edges_path_existing.exists():
        return 0
    with edges_path_existing.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                continue
            obj = json.loads(line)
            writer.seen.add((obj.get("external_key") or "", obj.get("src_id") or ""))
            api_like += 1
    writer.written = api_like
    return api_like


def _assemble_coverage(
    *, per_state: dict[str, _StateAccumulator], edge_counts: dict[str, int]
) -> list[dict[str, Any]]:
    states = sorted(set(per_state) | set(edge_counts))
    out: list[dict[str, Any]] = []
    for st in states:
        acc = per_state.get(st, _StateAccumulator())
        out.append(
            asdict(
                BulkStateCoverage(
                    state=st,
                    legislators=len(acc.legislators),
                    bills=acc.bills,
                    vote_edges=edge_counts.get(st, 0),
                    rollcalls=acc.rollcalls,
                    sessions=len(acc.sessions),
                )
            )
        )
    return out


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue
    import argparse

    parser = argparse.ArgumentParser(description="Ingest OpenStates bulk session-CSV votes")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--limit-zips", type=int, default=None, help="cap ZIPs (smoke test)")
    parser.add_argument("--no-resume", action="store_true", help="ignore the checkpoint; restart")
    args = parser.parse_args(argv)

    report = export_openstates_bulk(
        raw_dir=args.raw_dir,
        out_directory=args.out,
        limit_zips=args.limit_zips,
        resume=not args.no_resume,
    )
    print(
        f"OpenStates bulk: zips={report.zips_processed}/{report.zips_total} "
        f"legislators={report.total_legislators} bills={report.total_bills} "
        f"vote_edges={report.total_vote_edges} (bulk_new={report.bulk_vote_edges}, "
        f"api_seed={report.api_vote_edges}) rollcalls={report.total_rollcalls} "
        f"states_with_votes={report.distinct_states_with_votes} "
        f"runtime={report.runtime_seconds}s",
        flush=True,
    )
    for line in report.per_state:
        print(
            f"  {line['state']:>3}  legis={line['legislators']:>5}  "
            f"bills={line['bills']:>6}  votes={line['vote_edges']:>9}  "
            f"rollcalls={line['rollcalls']:>7}  sessions={line['sessions']:>3}",
            flush=True,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
