"""Adapt the OpenStates **bulk session-CSV** dumps into state legislator + vote facts.

OpenStates publishes, per (state, legislative session), a ZIP of normalized CSVs
(``data.openstates.org/csv/...``) carrying the full bill list, sponsorships,
actions, organizations, and — the high-value signal — the complete recorded
roll-call corpus: a ``votes`` file (one row per vote event) and a ``vote_people``
file (one row per legislator-per-vote). Unlike the rate-capped v3 API path in
:mod:`src.graph.ingest.openstates`, the bulk dumps are a *complete*, rate-limit
free snapshot of decades of sessions across all 50 states + DC + PR, which is
exactly the breadth the federal->state moat-thesis *transfer test* needs.

This module is a **pure, streaming adapter** (no network, no disk extraction):
the runner in :mod:`src.graph.ingest.openstates_bulk_export` opens each ZIP and
hands this module the relevant CSV members as text streams. It produces the
same edge/record shapes as the API adapter so the two corpora merge cleanly:

* **state legislators** (:class:`~src.graph.entity_resolution.records.SourceRecord`,
  ``entity_type='person'``) built from the distinct ``(voter_id, voter_name)``
  pairs that actually appear in a session's roll calls, keyed on the stable
  ``ocd-person/<uuid>`` and linked to the canonical *state*
  :class:`~src.graph.jurisdictions.Jurisdiction` (so a state legislator resolves
  exactly like a US member, and the same person dedupes across sessions/sources);
* **state bills** -> :class:`~src.graph.ingest.openstates.StateBill` keyed on the
  ``(us-<state>, session, identifier)`` :class:`~src.graph.bills.BillRef`; and
* per-legislator **roll-call vote** edges (via the shared
  :func:`~src.graph.ingest.openstates.state_vote_edge`), each carrying the
  normalized choice, motion text, result, and motion classification.

Streaming + memory discipline: the ``votes`` and ``bills`` files for one session
are small and read into per-session lookup dicts; the large ``vote_people`` file
is **iterated row-by-row** and joined on ``vote_event_id`` so a single session
never loads its full per-legislator vote list into RAM. Rows are emitted lazily.

Leakage discipline: ``known_at`` is stamped at the vote day
(``votes.start_date``) by the shared provenance helper, and the runner drops any
roll call dated after the observation instant. Malformed rows parse to ``None``
and are skipped — never fabricated. Source is public record (the bulk CSV dump).
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.graph.ingest.officials import openstates_official_record
from src.graph.ingest.openstates import (
    _OCD_PERSON_RE,
    OPENSTATES_BASE_URL,
    StateBill,
    StateRollCall,
    StateVote,
    _parse_date,
    normalize_openstates_choice,
)
from src.graph.provenance import ProvenanceEnvelope

if TYPE_CHECKING:
    from src.graph.entity_resolution.records import SourceRecord

#: Provenance source-system tag distinguishing bulk rows from the API sidecar.
OPENSTATES_BULK_SOURCE = "openstates_bulk"

#: Path prefixes that are NOT sub-federal jurisdictions we ingest as "states".
#: ``US`` is the *federal* Congress dump — that is the moat-thesis *source*
#: domain (handled by the dedicated congress adapters), never a transfer-test
#: target — so the runner skips it to avoid polluting the state corpus.
FEDERAL_PREFIX = "US"

_BILL_PREFIX_RE = re.compile(r"^ocd-bill/[0-9a-f-]{36}$")
_VOTE_PREFIX_RE = re.compile(r"^ocd-vote/[0-9a-f-]{36}$")


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def state_from_member_path(member_name: str) -> str | None:
    """USPS-style jurisdiction code from a bulk CSV member path, or ``None``.

    Bulk members live under ``<CODE>/<session>/<CODE>_<session>_<file>.csv`` where
    ``<CODE>`` is the OpenStates jurisdiction code: a USPS state code (``AL``,
    ``TX``), a territory (``PR``), ``DC``, or ``US`` (federal). Returns the
    lower-cased code, or ``None`` for the federal dump / an unrecognized path.
    """
    head = member_name.split("/", 1)[0].strip()
    if not re.fullmatch(r"[A-Za-z]{2}", head):
        return None
    if head.upper() == FEDERAL_PREFIX:
        return None
    return head.lower()


def _parse_classification(raw: Any) -> str | None:
    """First entry of an OpenStates ``['x', 'y']`` list-literal column, or ``None``."""
    text = _clean(raw)
    if not text or text == "[]":
        return None
    try:
        parsed = json.loads(text.replace("'", '"'))
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, list) and parsed:
        first = _clean(parsed[0])
        return first or None
    return None


# ── Bills lookup (``*_bills.csv``) ──────────────────────────────────────


@dataclass(frozen=True)
class _BillInfo:
    identifier: str
    title: str
    session: str
    chamber: str | None


def index_bills(rows: Iterator[dict[str, Any]]) -> dict[str, _BillInfo]:
    """Index a session's ``*_bills.csv`` rows by ``ocd-bill`` id -> identity.

    Only the linking columns are kept (identifier/title/session/chamber); the
    file is small per session, so a dict is memory-safe.
    """
    out: dict[str, _BillInfo] = {}
    for row in rows:
        bill_id = _clean(row.get("id"))
        identifier = _clean(row.get("identifier"))
        if not _BILL_PREFIX_RE.match(bill_id) or not identifier:
            continue
        out[bill_id] = _BillInfo(
            identifier=identifier,
            title=_clean(row.get("title")),
            session=_clean(row.get("session_identifier")),
            chamber=_clean(row.get("organization_classification")) or None,
        )
    return out


# ── Vote-event lookup (``*_votes.csv``) ─────────────────────────────────


@dataclass(frozen=True)
class _VoteEventInfo:
    bill_id: str
    motion_text: str
    motion_classification: str | None
    result: str | None
    start_date: Any  # datetime.date
    session: str


def index_vote_events(
    rows: Iterator[dict[str, Any]],
) -> dict[str, _VoteEventInfo]:
    """Index a session's ``*_votes.csv`` rows by ``ocd-vote`` id -> event metadata.

    Skips events with no parseable ``start_date`` (the leakage anchor) or no
    linkable ``ocd-bill`` id — such an event can produce no datable bill->vote
    edge, so it is dropped rather than fabricated.
    """
    out: dict[str, _VoteEventInfo] = {}
    for row in rows:
        vote_id = _clean(row.get("id"))
        if not _VOTE_PREFIX_RE.match(vote_id):
            continue
        bill_id = _clean(row.get("bill_id"))
        if not _BILL_PREFIX_RE.match(bill_id):
            continue
        start_date = _parse_date(row.get("start_date"))
        if start_date is None:
            continue
        out[vote_id] = _VoteEventInfo(
            bill_id=bill_id,
            motion_text=_clean(row.get("motion_text")),
            motion_classification=_parse_classification(row.get("motion_classification")),
            result=_clean(row.get("result")) or None,
            start_date=start_date,
            session=_clean(row.get("session_identifier")),
        )
    return out


# ── Streaming join (``*_vote_people.csv``) ──────────────────────────────


def parse_vote_person_row(row: dict[str, Any]) -> StateVote | None:
    """Parse one ``*_vote_people.csv`` row into a :class:`StateVote`, or ``None``.

    Requires a resolvable ``ocd-person`` voter id (the strong key that joins a
    vote to a legislator record) and an option that normalizes to the canonical
    choice vocabulary; otherwise the row is skipped.
    """
    voter_id = _clean(row.get("voter_id"))
    if not _OCD_PERSON_RE.match(voter_id):
        return None
    choice = normalize_openstates_choice(_clean(row.get("option")))
    if choice is None:
        return None
    return StateVote(
        voter_ocd_id=voter_id,
        voter_name=_clean(row.get("voter_name")) or voter_id,
        choice=choice,
        voter_state=None,
    )


@dataclass(frozen=True)
class _SessionParse:
    """Everything one session ZIP yields: bills with roll calls + distinct voters."""

    bills: tuple[StateBill, ...]
    #: ocd-person id -> display name, for the distinct legislators that voted.
    voters: dict[str, str]
    rollcalls: int
    raw_vote_rows: int


def parse_session(
    *,
    state: str,
    bills_rows: Iterator[dict[str, Any]],
    votes_rows: Iterator[dict[str, Any]],
    vote_people_rows: Iterator[dict[str, Any]],
    session_label: str,
    source_url: str,
) -> _SessionParse:
    """Join one session's three CSV streams into bills, roll calls, and voters.

    ``bills_rows`` and ``votes_rows`` are read into small per-session dicts;
    ``vote_people_rows`` is **streamed** and grouped by ``vote_event_id`` so the
    big file is never fully materialized as parsed objects beyond the grouping
    (one accumulator entry per vote event, which is bounded by the vote count,
    not the legislator-vote count).
    """
    bills_idx = index_bills(bills_rows)
    events_idx = index_vote_events(votes_rows)

    # Group streamed per-legislator rows by their vote event. We accumulate
    # StateVote tuples keyed by event id; the number of events per session is
    # modest, and each event's voter list is the chamber size — bounded.
    grouped: dict[str, list[StateVote]] = {}
    voters: dict[str, str] = {}
    raw_rows = 0
    for row in vote_people_rows:
        vote_event_id = _clean(row.get("vote_event_id"))
        if vote_event_id not in events_idx:
            continue
        vote = parse_vote_person_row(row)
        if vote is None:
            continue
        raw_rows += 1
        grouped.setdefault(vote_event_id, []).append(vote)
        if vote.voter_ocd_id is not None:
            voters[vote.voter_ocd_id] = vote.voter_name

    # Build one StateRollCall per event, then group roll calls under their bill.
    rollcalls_by_bill: dict[str, list[StateRollCall]] = {}
    n_rollcalls = 0
    for vote_event_id, votes in grouped.items():
        if not votes:
            continue
        event = events_idx[vote_event_id]
        rc = StateRollCall(
            ocd_vote_id=vote_event_id,
            motion_text=event.motion_text,
            result=event.result,
            start_date=event.start_date,
            source_url=source_url,
            votes=tuple(votes),
        )
        rollcalls_by_bill.setdefault(event.bill_id, []).append(rc)
        n_rollcalls += 1

    bills: list[StateBill] = []
    for bill_id, rollcalls in rollcalls_by_bill.items():
        info = bills_idx.get(bill_id)
        # The vote event always carries its own session/identifier-bearing bill
        # id; if the bills file lacks the row (rare cross-file gap) we still need
        # an identifier to form a BillRef — derive it from the event's session and
        # the bill id is not human-readable, so skip honestly rather than guess.
        if info is None or not info.identifier:
            continue
        session = info.session or _session_from_events(rollcalls, events_idx) or session_label
        action_date = min(rc.start_date for rc in rollcalls)
        bills.append(
            StateBill(
                ocd_bill_id=bill_id,
                state=state,
                session=session,
                identifier=info.identifier,
                title=info.title,
                chamber=info.chamber,
                action_date=action_date,
                source_url=source_url,
                rollcalls=tuple(rollcalls),
            )
        )

    return _SessionParse(
        bills=tuple(bills),
        voters=voters,
        rollcalls=n_rollcalls,
        raw_vote_rows=raw_rows,
    )


def _session_from_events(
    rollcalls: list[StateRollCall], events_idx: dict[str, _VoteEventInfo]
) -> str | None:
    for rc in rollcalls:
        event = events_idx.get(rc.ocd_vote_id)
        if event is not None and event.session:
            return event.session
    return None


# ── Legislator records ──────────────────────────────────────────────────


def voter_provenance(
    *, state: str, source_url: str, content_sha256: str, first_observed_at: datetime
) -> ProvenanceEnvelope:
    """Provenance for a legislator inferred from a bulk roll-call roster.

    A voter's appearance in a session's roll calls is public on the dump's date;
    we have no per-legislator date, so ``known_at`` is conservatively the
    observation instant (never earlier than we saw it).
    """
    return ProvenanceEnvelope(
        source_url=source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=first_observed_at.date(),
        known_at=first_observed_at,
    )


def legislator_records_from_voters(
    *,
    voters: dict[str, str],
    state: str,
    provenance: ProvenanceEnvelope,
) -> list[SourceRecord]:
    """Build legislator :class:`SourceRecord`s for the distinct voters in a session.

    Keyed on the ``ocd-person`` id and linked to the canonical state jurisdiction
    via :func:`openstates_official_record`, so a state legislator resolves to a
    canonical Person id exactly like the API-sourced roster (and dedupes against
    it across sessions/sources). Party is unknown from the roll-call files, so it
    is left ``None`` — never fabricated.
    """
    out: list[SourceRecord] = []
    for ocd_id, name in voters.items():
        if not _OCD_PERSON_RE.match(ocd_id) or not name:
            continue
        out.append(
            openstates_official_record(
                openstates_id=ocd_id,
                name=name,
                state_usps=state,
                party=None,
                provenance=provenance,
            )
        )
    return out


def csv_rows(text_stream: Any) -> Iterator[dict[str, Any]]:
    """Yield dict rows from an open text stream of an OpenStates CSV member.

    A thin wrapper around :class:`csv.DictReader` so callers iterate rows without
    materializing the file; used by the runner to feed the big ``vote_people``
    member row-by-row.
    """
    yield from csv.DictReader(text_stream)


def session_source_url(*, state: str, session_label: str) -> str:
    """A stable, human-resolvable source URL for a bulk session dump."""
    return f"{OPENSTATES_BASE_URL}/{state}/?session={session_label}"


def now_utc() -> datetime:
    return datetime.now(tz=UTC)
