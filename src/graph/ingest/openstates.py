"""Adapt OpenStates / Plural records into canonical state legislators, bills, and votes.

OpenStates (open.pluralpolicy.com) is the public system of record for *state*
legislatures: every bill, every action, every recorded roll call, and the full
roster of legislators for all 50 states, D.C., and the territories. It is the
single biggest breadth unlock for the governance graph — it carries the federal
model's three core fact-types (person, bill, vote) down to the 50 statehouses,
which is what makes the federal->state moat-thesis *transfer test* runnable.

This module is a **pure adapter** (no network): the runner in
:mod:`src.graph.ingest.openstates_export` does all I/O and hands this module
already-parsed record dicts/CSV-rows. It produces:

* a **state legislator** :class:`SourceRecord` (``entity_type='person'``) keyed
  on its stable OpenStates/OCD person id (``ocd-person/<uuid>``), linked to its
  canonical *state* :class:`~src.graph.jurisdictions.Jurisdiction` so a state
  legislator resolves to a canonical Person ID exactly like a US member does;
* a **state bill** :class:`StateBill` -> :class:`BillRef`, keyed on the
  (state, session, identifier) triple so bills resolve deterministically; and
* per-legislator **roll-call vote** edges (:class:`StateVote` ->
  :func:`state_vote_edge`): one ``vote`` :class:`GraphEdge` from a legislator's
  canonical Person ID to the bill's canonical ID carrying the normalized choice.
  These are the vote edges the transfer test consumes.

Leakage discipline: a roll call is public on its ``start_date``; a bill action
on its action date. :func:`vote_provenance` (reused from :mod:`src.graph.ingest.\
votes`) stamps ``known_at`` at the vote day, so a vote can never leak into a
prediction made before it happened. Malformed rows parse to ``None`` and are
skipped — never fabricated.

Sources & ethics: legislators come from the **free, public, no-auth bulk**
people CSV (``data.openstates.org/people/current/<state>.csv``); bills + roll
calls come from the rate-capped v3 API (``include=votes`` embeds the named
voter list for states that record it). Both are public record. The runner
respects the documented 500-req/day, 1-req/sec API limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from src.graph.bills import BillRef, normalize_bill_identifier
from src.graph.edges import GraphEdge
from src.graph.entity_resolution.records import ExternalId, SourceRecord
from src.graph.ingest.officials import openstates_official_record
from src.graph.ingest.votes import VOTE_CHOICES, normalize_vote_choice, vote_edge, vote_provenance
from src.graph.jurisdictions import Jurisdiction
from src.graph.provenance import ProvenanceEnvelope

OPENSTATES_SOURCE_SYSTEM = "openstates"
OPENSTATES_BASE_URL = "https://openstates.org"
OPENSTATES_API_URL = "https://v3.openstates.org"
#: Public, no-auth, no-API-cost nightly per-state legislator roster.
OPENSTATES_PEOPLE_CSV = "https://data.openstates.org/people/current"

#: OCD jurisdiction ids embed the USPS state code: ``…/country:us/state:tx/…``.
_OCD_STATE_RE = re.compile(r"country:us/state:([a-z]{2})\b")
_OCD_PERSON_RE = re.compile(r"^ocd-person/[0-9a-f-]{36}$")

#: OpenStates vote options beyond the federal vocabulary. ``excused`` and
#: ``absent`` both map to ``not_voting`` (the legislator cast no recorded vote);
#: the catch-all ``other`` is OpenStates' bucket for paired/abstain/etc.
_OPENSTATES_CHOICE_ALIASES: dict[str, str] = {
    "yes": "yea",
    "no": "nay",
    "excused": "not_voting",
    "absent": "not_voting",
    "abstain": "abstain",
    "other": "abstain",
    "not voting": "not_voting",
}


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def state_from_ocd(ocd_id: str) -> str | None:
    """Extract the USPS state code from any OCD id, or ``None``.

    ``ocd-jurisdiction/country:us/state:tx/government`` -> ``"tx"``;
    ``ocd-division/country:us/state:ca/sldl:66`` -> ``"ca"``.
    """
    match = _OCD_STATE_RE.search(ocd_id or "")
    return match.group(1) if match else None


def normalize_openstates_choice(raw: str) -> str | None:
    """Map an OpenStates vote ``option`` to the canonical choice vocabulary.

    Returns ``None`` for an unrecognized/blank option (the row is skipped rather
    than crashing the run). OpenStates' own option labels (``yes``/``no``/
    ``excused``/``absent``/``other``) are translated to the federal vocabulary
    so a state vote and a federal vote are the same edge shape.
    """
    key = " ".join(_clean(raw).lower().split())
    if not key:
        return None
    mapped = _OPENSTATES_CHOICE_ALIASES.get(key)
    if mapped is not None:
        return mapped
    if key in VOTE_CHOICES:
        return key
    try:
        return normalize_vote_choice(key)
    except ValueError:
        return None


# ── Legislators (bulk people CSV) ──────────────────────────────────────


def parse_legislator_csv_row(
    row: dict[str, Any],
    *,
    provenance: ProvenanceEnvelope,
) -> SourceRecord | None:
    """Parse one ``people/current/<state>.csv`` row into a legislator record.

    Extracts only public-conduct fields — name, party, state, the stable OCD
    person id — and deliberately drops the private-life columns the hard
    constraints forbid (birth/death date, personal email, gender, addresses).
    Returns ``None`` for a row missing the OCD id / name / resolvable state.
    """
    person_id = _clean(row.get("id"))
    if not _OCD_PERSON_RE.match(person_id):
        return None
    name = _clean(row.get("name"))
    if not name:
        return None
    state = state_from_ocd(_clean(row.get("jurisdiction") or row.get("current_district") or ""))
    # The current people CSV omits an explicit OCD jurisdiction column; the
    # runner injects the state it is fetching. Fall back to that injected value.
    state = state or _clean(row.get("_state")).lower() or None
    if state is None or not re.match(r"^[a-z]{2}$", state):
        return None
    party = _clean(row.get("current_party")) or None
    return openstates_official_record(
        openstates_id=person_id,
        name=name,
        state_usps=state,
        party=party,
        provenance=provenance,
    )


# ── Bills + votes (v3 API ``/bills?include=votes``) ─────────────────────


@dataclass(frozen=True)
class StateVote:
    """One legislator's recorded choice on one roll call."""

    voter_ocd_id: str | None  # ocd-person/<uuid> when the source records it
    voter_name: str
    choice: str  # canonical VOTE_CHOICES value
    voter_state: str | None


@dataclass(frozen=True)
class StateBill:
    """One parsed state bill with its roll-call votes."""

    ocd_bill_id: str
    state: str  # USPS code
    session: str
    identifier: str  # e.g. "HB 22"
    title: str
    chamber: str | None  # 'lower' / 'upper' from from_organization
    action_date: date  # latest action / first action — the bill's known-at floor
    source_url: str
    rollcalls: tuple["StateRollCall", ...]


@dataclass(frozen=True)
class StateRollCall:
    """One roll call on a bill: a motion, a date, and the per-legislator votes."""

    ocd_vote_id: str
    motion_text: str
    result: str | None
    start_date: date
    source_url: str
    votes: tuple[StateVote, ...]


def _parse_date(raw: Any) -> date | None:
    text = _clean(raw)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_state_bill(record: dict[str, Any]) -> StateBill | None:
    """Parse one v3 ``/bills`` result (with ``include=votes``) into a StateBill.

    Skips a row lacking the identity it needs to link: an OCD bill id, a
    resolvable state, a session, a bill identifier, and a parseable date.
    Roll calls whose ``option`` does not normalize, or which carry no individual
    voters *and* no tally, are dropped; a bill with zero usable roll calls is
    still kept (it is a real bill node).
    """
    ocd_bill_id = _clean(record.get("id"))
    if not ocd_bill_id.startswith("ocd-bill/"):
        return None
    identifier = _clean(record.get("identifier"))
    if not identifier:
        return None
    jurisdiction = record.get("jurisdiction") or {}
    state = state_from_ocd(_clean(jurisdiction.get("id")) if isinstance(jurisdiction, dict) else "")
    if state is None:
        return None
    session = _clean(record.get("session"))
    if not session:
        return None
    action_date = (
        _parse_date(record.get("latest_action_date"))
        or _parse_date(record.get("first_action_date"))
        or _parse_date(record.get("latest_passage_date"))
    )
    if action_date is None:
        return None
    from_org = record.get("from_organization") or {}
    chamber = (_clean(from_org.get("classification")) if isinstance(from_org, dict) else "") or None
    source_url = _clean(record.get("openstates_url")) or f"{OPENSTATES_BASE_URL}/bill/{ocd_bill_id}"

    rollcalls: list[StateRollCall] = []
    for raw_vote in record.get("votes") or []:
        rc = _parse_rollcall(raw_vote, fallback_url=source_url)
        if rc is not None:
            rollcalls.append(rc)

    return StateBill(
        ocd_bill_id=ocd_bill_id,
        state=state,
        session=session,
        identifier=identifier,
        title=_clean(record.get("title")),
        chamber=chamber,
        action_date=action_date,
        source_url=source_url,
        rollcalls=tuple(rollcalls),
    )


def _parse_rollcall(raw: dict[str, Any], *, fallback_url: str) -> StateRollCall | None:
    ocd_vote_id = _clean(raw.get("id"))
    if not ocd_vote_id:
        return None
    start_date = _parse_date(raw.get("start_date"))
    if start_date is None:
        return None
    sources = raw.get("sources") or []
    source_url = fallback_url
    if isinstance(sources, list):
        for src in sources:
            if isinstance(src, dict) and _clean(src.get("url")).startswith(("http://", "https://")):
                source_url = _clean(src.get("url"))
                break

    votes: list[StateVote] = []
    for raw_v in raw.get("votes") or []:
        v = _parse_voter(raw_v)
        if v is not None:
            votes.append(v)
    if not votes:
        # No named voters recorded (many states report only tallies). We keep the
        # roll call only when it has individual votes — tally-only roll calls
        # produce no per-legislator edges, which is all this source is for.
        return None
    return StateRollCall(
        ocd_vote_id=ocd_vote_id,
        motion_text=_clean(raw.get("motion_text")),
        result=_clean(raw.get("result")) or None,
        start_date=start_date,
        source_url=source_url,
        votes=tuple(votes),
    )


def _parse_voter(raw: dict[str, Any]) -> StateVote | None:
    if not isinstance(raw, dict):
        return None
    choice = normalize_openstates_choice(_clean(raw.get("option")))
    if choice is None:
        return None
    voter = raw.get("voter") or {}
    raw_ocd = _clean(voter.get("id")) if isinstance(voter, dict) else ""
    voter_ocd: str | None = raw_ocd if _OCD_PERSON_RE.match(raw_ocd) else None
    name = (_clean(voter.get("name")) if isinstance(voter, dict) else "") or _clean(
        raw.get("voter_name")
    )
    if not name and voter_ocd is None:
        return None
    voter_state = None
    if isinstance(voter, dict):
        role = voter.get("current_role") or {}
        if isinstance(role, dict):
            voter_state = state_from_ocd(_clean(role.get("division_id")))
    return StateVote(
        voter_ocd_id=voter_ocd,
        voter_name=name or (voter_ocd or "unknown"),
        choice=choice,
        voter_state=voter_state,
    )


# ── Canonical projections ──────────────────────────────────────────────


def state_bill_ref(bill: StateBill) -> BillRef:
    """The deterministic canonical bill ref for a state bill.

    Keyed on ``(us-<state>, <session>, <normalized identifier>)`` so the same
    bill resolves to one canonical id across the bill node and every vote edge.
    """
    jurisdiction = Jurisdiction.state(bill.state)
    return BillRef(
        jurisdiction_id=jurisdiction.code,
        session_id=bill.session,
        identifier=normalize_bill_identifier(bill.identifier),
    )


def bill_display_name(bill: StateBill) -> str:
    """Human-readable bill label: ``HB 22 (TX 89)``."""
    return f"{bill.identifier} ({bill.state.upper()} {bill.session})"


def bill_external_keys(bill: StateBill) -> list[str]:
    """Sorted, deduped external-id keys for a bill row (OCD id + canonical id)."""
    ref = state_bill_ref(bill)
    return sorted({f"openstates_bill:{bill.ocd_bill_id}", f"canonical_bill:{ref.canonical_id}"})


def bill_provenance(
    bill: StateBill, *, content_sha256: str, first_observed_at: datetime
) -> ProvenanceEnvelope:
    """Provenance for a state bill; ``known_at`` defaults to its action date."""
    known = datetime(
        bill.action_date.year, bill.action_date.month, bill.action_date.day, tzinfo=UTC
    )
    return ProvenanceEnvelope(
        source_url=bill.source_url,
        content_sha256=content_sha256,
        first_observed_at=first_observed_at,
        valid_from=bill.action_date,
        known_at=known,
    )


def legislator_external_id(voter_ocd_id: str) -> ExternalId:
    """The strong external id that joins a vote's voter to its legislator record."""
    return ExternalId(system="openstates", value=voter_ocd_id)


def state_vote_edge(
    *,
    voter_canonical_id: str,
    bill_canonical_id: str,
    vote: StateVote,
    rollcall: StateRollCall,
    provenance: ProvenanceEnvelope,
) -> GraphEdge:
    """Build the legislator -> bill ``vote`` edge for one recorded choice.

    The roll-call OCD id is the ``external_key`` so multiple roll calls on the
    same bill by the same legislator (e.g. committee then floor) stay distinct.
    """
    edge = vote_edge(
        member_canonical_id=voter_canonical_id,
        bill_canonical_id=bill_canonical_id,
        choice=vote.choice,
        provenance=provenance,
    )
    return edge.model_copy(
        update={
            "external_key": rollcall.ocd_vote_id,
            "attributes": {
                "choice": vote.choice,
                "motion": rollcall.motion_text[:200],
                **({"result": rollcall.result} if rollcall.result else {}),
                "source": OPENSTATES_SOURCE_SYSTEM,
            },
        }
    )


def rollcall_provenance(
    rollcall: StateRollCall, *, content_sha256: str, first_observed_at: datetime
) -> ProvenanceEnvelope:
    """Provenance for one roll call; ``known_at`` = the vote day (leakage gate)."""
    return vote_provenance(
        source_url=rollcall.source_url,
        content_sha256=content_sha256,
        vote_date=rollcall.start_date,
        first_observed_at=first_observed_at,
    )
