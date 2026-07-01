"""Real House roll-call corpus from clerk.house.gov (public record, no auth).

Builds real ``VoteRow`` records from the House Clerk's published roll-call XML
(``clerk.house.gov/evs/<year>/roll<NNN>.xml``). Each recorded vote carries the
member's bioguide id, party, state, and choice; per roll-call we compute each
party's majority direction and emit, for every binary (yea/nay) member vote, a
``VoteRow`` whose ``party_alignment`` signal is +1 when the member's party
leaned yea and -1 when it leaned nay, with ``is_cross_pressured`` set when the
member defected from that party majority (the votes worth predicting).

The party-majority feature is computed from the whole party tally; a single
member's contribution to a ~200-member party is negligible, so this is a clean
real signal, not outcome leakage. Parsing/building is pure and offline-tested;
``fetch_house_rollcalls`` is the only network boundary.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from defusedxml import ElementTree as DefusedET

from src.graph.ingest.votes import normalize_vote_choice
from src.prediction.real_data_eval import VoteRow

_BINARY = {"yea", "nay"}


@dataclass(frozen=True)
class MemberVote:
    bioguide_id: str
    party: str
    state: str
    choice: str  # canonical: yea/nay/present/not_voting/...


@dataclass(frozen=True)
class RollCall:
    bill_id: str
    question: str
    vote_date: date
    congress: int
    votes: tuple[MemberVote, ...]
    description: str = ""


def _slug_bill(legis_num: str, congress: int) -> str:
    parts = legis_num.strip().lower().split()
    return (
        f"us_congress:{congress}:" + "-".join(parts) if parts else f"us_congress:{congress}:unknown"
    )


def parse_rollcall_xml(xml: str) -> RollCall | None:
    """Parse a House Clerk roll-call XML string into a ``RollCall`` (party/state included)."""
    root = DefusedET.fromstring(xml)
    metadata = root.find(".//vote-metadata")
    if metadata is None:
        return None

    def _text(tag: str) -> str:
        node = metadata.find(tag)
        return (node.text or "").strip() if node is not None else ""

    congress_text = _text("congress")
    legis_num = _text("legis-num")
    question = _text("vote-question")
    description = _text("vote-desc")
    action = _text("action-date")
    try:
        vote_date = datetime.strptime(action, "%d-%b-%Y").date()
    except ValueError:
        return None
    congress = int(congress_text) if congress_text.isdigit() else 0

    votes: list[MemberVote] = []
    for recorded in root.findall(".//recorded-vote"):
        legislator = recorded.find("legislator")
        vote_node = recorded.find("vote")
        if legislator is None or vote_node is None:
            continue
        bioguide = (legislator.get("name-id") or "").strip()
        party = (legislator.get("party") or "").strip()
        state = (legislator.get("state") or "").strip()
        raw_choice = (vote_node.text or "").strip()
        if not bioguide or not raw_choice:
            continue
        try:
            choice = normalize_vote_choice(raw_choice)
        except ValueError:
            continue
        votes.append(MemberVote(bioguide_id=bioguide, party=party, state=state, choice=choice))

    if not votes:
        return None
    return RollCall(
        bill_id=_slug_bill(legis_num, congress),
        question=question,
        vote_date=vote_date,
        congress=congress,
        votes=tuple(votes),
        description=description,
    )


def build_vote_rows_from_rollcalls(rollcalls: Iterable[RollCall]) -> list[VoteRow]:
    """Emit a ``VoteRow`` per binary member vote, with the party-alignment signal."""
    rows: list[VoteRow] = []
    for rollcall in rollcalls:
        binary_votes = [vote for vote in rollcall.votes if vote.choice in _BINARY]
        if not binary_votes:
            continue
        # Per-party majority direction (yea?), computed across that party's votes.
        party_yea: dict[str, int] = {}
        party_total: dict[str, int] = {}
        for vote in binary_votes:
            party_total[vote.party] = party_total.get(vote.party, 0) + 1
            if vote.choice == "yea":
                party_yea[vote.party] = party_yea.get(vote.party, 0) + 1
        party_leans_yea = {
            party: party_yea.get(party, 0) * 2 >= total for party, total in party_total.items()
        }
        for vote in binary_votes:
            leans_yea = party_leans_yea.get(vote.party, True)
            is_yea = vote.choice == "yea"
            rows.append(
                VoteRow(
                    member_bioguide_id=vote.bioguide_id,
                    canonical_bill_id=rollcall.bill_id,
                    vote_option=vote.choice,
                    vote_date=rollcall.vote_date,
                    party=vote.party or None,
                    state=vote.state or None,
                    jurisdiction_id="us_congress",
                    signals={"party_alignment": 1.0 if leans_yea else -1.0},
                    is_cross_pressured=is_yea != leans_yea,
                )
            )
    return rows


def fetch_house_rollcalls(
    *,
    year: int,
    numbers: Iterable[int],
    client: httpx.Client | None = None,
    user_agent: str = "psephosamerica-research/0.1 (public-record roll-call ingest)",
) -> list[RollCall]:
    """Fetch and parse a set of real House roll-calls (skips 404s and non-votes)."""
    owns_client = client is None
    http = client or httpx.Client(timeout=30.0, headers={"User-Agent": user_agent})
    rollcalls: list[RollCall] = []
    try:
        for number in numbers:
            url = f"https://clerk.house.gov/evs/{year}/roll{number:03d}.xml"
            response = http.get(url)
            if response.status_code != 200:
                continue
            rollcall = parse_rollcall_xml(response.text)
            if rollcall is not None:
                rollcalls.append(rollcall)
    finally:
        if owns_client:
            http.close()
    return rollcalls
