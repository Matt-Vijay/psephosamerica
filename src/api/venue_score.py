"""Venue-score endpoint: rank jurisdictions/committees by P(pass) for a bill (track 3).

The marginal-vote product has two halves: *who* to persuade (defection-watch) and
*where* to move a bill (this). Given a bill/project profile (its policy sectors),
we rank venues -- a venue is a (jurisdiction, chamber, committee) -- by the
probability a bill in those sectors passes, estimated from that venue's historical
roll-call outcomes with Wilson-interval uncertainty and **citations** (the actual
roll-calls that informed the estimate). Each ranked venue also surfaces the top
*marginal members* from the defection model, so the two products compose: bring
the bill to the highest-P(pass) venue, then work its swing votes.

Estimates are shrunk toward the venue's overall pass rate (empirical Bayes) so a
thin sector history borrows strength. Pure roll-call data; committee/multi-chamber
granularity fills in as Track A lands committee assignments and other chambers --
the venue key already carries those fields.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Venue:
    jurisdiction: str  # e.g. "us_congress:118"
    chamber: str  # "house" | "senate" | ...
    committee: str = ""  # filled when Track A lands committee assignments

    def key(self) -> str:
        return f"{self.jurisdiction}|{self.chamber}|{self.committee}"

    def label(self) -> str:
        base = f"{self.jurisdiction} {self.chamber}".strip()
        return f"{base} · {self.committee}" if self.committee else base


@dataclass(frozen=True)
class Citation:
    bill_id: str
    vote_date: str
    passed: bool
    yea: int
    nay: int


@dataclass
class _SectorTally:
    passes: int = 0
    total: int = 0
    citations: list[Citation] = field(default_factory=list)


@dataclass(frozen=True)
class VenueScore:
    venue: Venue
    p_pass: float
    interval_lower: float
    interval_upper: float
    sample_count: int
    citations: list[Citation]
    marginal_members: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue.label(),
            "venue_key": self.venue.key(),
            "p_pass": self.p_pass,
            "interval": [self.interval_lower, self.interval_upper],
            "sample_count": self.sample_count,
            "citations": [
                {
                    "bill_id": c.bill_id,
                    "date": c.vote_date,
                    "passed": c.passed,
                    "yea": c.yea,
                    "nay": c.nay,
                }
                for c in self.citations
            ],
            "marginal_members": self.marginal_members,
        }


def _wilson_interval(passes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 1.0
    p = passes / total
    denom = 1.0 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, center - margin), min(1.0, center + margin)


class VenueIndex:
    """Historical pass rates per (venue, sector) with citations, from roll-calls."""

    def __init__(self) -> None:
        self._sector: dict[str, dict[str, _SectorTally]] = defaultdict(
            lambda: defaultdict(_SectorTally)
        )
        self._overall: dict[str, _SectorTally] = defaultdict(_SectorTally)
        self._venues: dict[str, Venue] = {}

    def add_rollcall(
        self, venue: Venue, bill_id: str, vote_date: date, sectors: list[str], yea: int, nay: int
    ) -> None:
        passed = yea > nay
        self._venues[venue.key()] = venue
        citation = Citation(
            bill_id=bill_id, vote_date=vote_date.isoformat(), passed=passed, yea=yea, nay=nay
        )
        overall = self._overall[venue.key()]
        overall.passes += int(passed)
        overall.total += 1
        for sector in sectors or ["_unsectored"]:
            tally = self._sector[venue.key()][sector]
            tally.passes += int(passed)
            tally.total += 1
            if len(tally.citations) < 5:
                tally.citations.append(citation)

    def score(
        self,
        sectors: list[str],
        *,
        prior_strength: float = 10.0,
        marginal_members_by_venue: dict[str, list[str]] | None = None,
        top_n: int = 10,
    ) -> list[VenueScore]:
        """Rank venues by shrunk P(pass) on the bill's sectors, with citations."""
        marginal = marginal_members_by_venue or {}
        scores: list[VenueScore] = []
        for key, venue in self._venues.items():
            overall = self._overall[key]
            overall_rate = overall.passes / overall.total if overall.total else 0.5
            passes = total = 0
            citations: list[Citation] = []
            for sector in sectors or ["_unsectored"]:
                tally = self._sector[key].get(sector)
                if tally:
                    passes += tally.passes
                    total += tally.total
                    citations.extend(tally.citations)
            shrunk = (passes + prior_strength * overall_rate) / (total + prior_strength)
            lower, upper = _wilson_interval(passes, total)
            scores.append(
                VenueScore(
                    venue=venue,
                    p_pass=shrunk,
                    interval_lower=lower,
                    interval_upper=upper,
                    sample_count=total,
                    citations=citations[:5],
                    marginal_members=marginal.get(key, [])[:5],
                )
            )
        scores.sort(key=lambda s: s.p_pass, reverse=True)
        return scores[:top_n]


def build_venue_index(rollcalls: list[dict[str, Any]], *, chamber: str = "house") -> VenueIndex:
    """Build a venue index from rich roll-calls (venue = congress jurisdiction)."""
    index = VenueIndex()
    for rollcall in rollcalls:
        votes = rollcall.get("votes", [])
        yea = sum(1 for v in votes if v[3] == "yea")
        nay = sum(1 for v in votes if v[3] == "nay")
        if yea + nay == 0:
            continue
        congress = rollcall.get("congress", "?")
        venue = Venue(jurisdiction=f"us_congress:{congress}", chamber=chamber)
        index.add_rollcall(
            venue,
            bill_id=str(rollcall.get("bill_id", "")),
            vote_date=date.fromisoformat(str(rollcall["date"])),
            sectors=list(rollcall.get("sectors", [])),
            yea=yea,
            nay=nay,
        )
    return index
