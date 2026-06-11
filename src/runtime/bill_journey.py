"""Build bill-journey rows: introduction -> first recorded floor vote (v7 #1 data).

Joins the govinfo full sidecar (``bill_content_full.jsonl``: introduced_date,
sponsor, cosponsors, committees, policy area) to the ingested roll-call corpora
(House 113/115-119, Senate 113-119) by normalised bill key. The observable event
is the bill's **first recorded floor roll-call in its origin chamber** -- a bill
advanced by voice vote only is right-censored, and a (chamber, congress) with no
ingested roll-calls contributes no rows at all (House 114 floor data is not
ingested; those bills are skipped rather than mislabelled). Censoring time is
the earlier of the congress's end and the chamber's ingested coverage end, so an
ongoing congress (119th) is censored at the data edge, never labelled "no floor
vote".

The sidecar has no govinfo action history (committee report dates, cloture,
signature); when Track A exports one, later links get their own loaders. This
module prices the first -- and most lethal -- link.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from src.runtime.bill_content_experiment import _iter_records, normalize_bill_key

# Origin-chamber majority party by congress (public record).
HOUSE_MAJORITY = {113: "R", 114: "R", 115: "R", 116: "D", 117: "D", 118: "R", 119: "R"}
SENATE_MAJORITY = {113: "D", 114: "R", 115: "R", 116: "R", 117: "D", 118: "D", 119: "R"}

_PARTY_RE = re.compile(r"\[([A-Z])[-\]]")


def congress_window(congress: int) -> tuple[date, date]:
    """(start, end) of a congress: Jan 3 of the odd year to Jan 3 two years on."""
    start_year = 1789 + 2 * (congress - 1)
    return date(start_year, 1, 3), date(start_year + 2, 1, 3)


@dataclass(frozen=True)
class BillJourney:
    bill_key: str
    congress: int
    chamber: str  # "house" | "senate"
    policy_area: str
    sponsor_party: str
    sponsor_in_majority: bool
    cosponsor_count: int
    bipartisan_share: float
    committee_count: int
    intro_phase: float  # fraction of the 2-year congress elapsed at introduction
    duration_days: float  # days to first floor roll-call, or to censoring
    event_observed: bool


def _sponsor_party(sponsors: list[dict[str, object]]) -> str:
    for s in sponsors:
        m = _PARTY_RE.search(str(s.get("full_name", "")))
        if m:
            return m.group(1)
    return "?"


def _cosponsor_date(cosponsor: dict[str, object]) -> date | None:
    raw = str(cosponsor.get("sponsorship_date") or "")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def load_floor_events(corpus_paths: list[Path]) -> tuple[dict[str, date], dict[int, date]]:
    """First roll-call date per bill key + ingested coverage end per congress.

    Accepts both corpus shapes: rich roll-call rows (``bill_id``/``date``) and
    flat per-vote rows (``canonical_bill_id``/``vote_date``).
    """
    first: dict[str, date] = {}
    coverage_end: dict[int, date] = {}
    for path in corpus_paths:
        for row in _iter_records(path):
            raw_id = str(row.get("bill_id") or row.get("canonical_bill_id") or "")
            raw_date = str(row.get("date") or row.get("vote_date") or "")
            key = normalize_bill_key(raw_id)
            try:
                when = date.fromisoformat(raw_date)
            except ValueError:
                continue
            congress = int(key.split(":")[0]) if key else _congress_of_id(raw_id)
            if congress:
                prev = coverage_end.get(congress)
                if prev is None or when > prev:
                    coverage_end[congress] = when
            if key is None:
                continue
            existing = first.get(key)
            if existing is None or when < existing:
                first[key] = when
    return first, coverage_end


def _congress_of_id(raw_id: str) -> int:
    m = re.match(r"us_congress:(\d+):", raw_id)
    return int(m.group(1)) if m else 0


def load_bill_journeys(
    sidecar: Path,
    house_events: tuple[dict[str, date], dict[int, date]],
    senate_events: tuple[dict[str, date], dict[int, date]],
) -> list[BillJourney]:
    """One journey row per sidecar bill whose origin chamber has floor coverage."""
    journeys: list[BillJourney] = []
    for r in _iter_records(sidecar):
        intro_raw = str(r.get("introduced_date") or "")
        bill_type = str(r.get("bill_type") or "")
        congress = int(r.get("congress") or 0)
        if not intro_raw or not bill_type or not congress:
            continue
        try:
            introduced = date.fromisoformat(intro_raw)
        except ValueError:
            continue
        chamber = "house" if bill_type.startswith("h") else "senate"
        first, coverage_end = house_events if chamber == "house" else senate_events
        end_of_coverage = coverage_end.get(congress)
        if end_of_coverage is None:
            continue  # no ingested floor data for this (chamber, congress)
        _start, congress_end = congress_window(congress)
        censor = min(congress_end, end_of_coverage + timedelta(days=1))
        if censor <= introduced:
            continue  # introduced after the observable window: nothing to learn
        key = f"{congress}:{bill_type}:{int(r.get('number') or 0)}"
        floor = first.get(key)
        if floor is not None and floor >= introduced:
            duration = float((floor - introduced).days)
            event = True
        else:
            duration = float((censor - introduced).days)
            event = False
        sponsors = list(r.get("sponsors") or [])
        # Ex-ante discipline: only cosponsors who joined within 30 days of
        # introduction count -- the final tally leaks the bill's later success.
        early_cutoff = introduced + timedelta(days=30)
        cosponsors = [
            c
            for c in (r.get("cosponsors") or [])
            if (joined := _cosponsor_date(c)) is None or joined <= early_cutoff
        ]
        sponsor_party = _sponsor_party(sponsors)
        majority = (HOUSE_MAJORITY if chamber == "house" else SENATE_MAJORITY).get(congress, "?")
        cross = sum(1 for c in cosponsors if _sponsor_party([c]) not in ("?", sponsor_party))
        window_start, window_end = congress_window(congress)
        span = max(1, (window_end - window_start).days)
        journeys.append(
            BillJourney(
                bill_key=key,
                congress=congress,
                chamber=chamber,
                policy_area=str(r.get("policy_area") or ""),
                sponsor_party=sponsor_party,
                sponsor_in_majority=sponsor_party == majority,
                cosponsor_count=len(cosponsors),
                bipartisan_share=cross / len(cosponsors) if cosponsors else 0.0,
                committee_count=len(list(r.get("committees") or [])),
                intro_phase=min(1.0, max(0.0, (introduced - window_start).days / span)),
                duration_days=duration,
                event_observed=event,
            )
        )
    return journeys


@dataclass(frozen=True)
class JourneyFeaturizer:
    """Deterministic journey -> feature vector, with the one-hot vocab fit on train."""

    policy_areas: tuple[str, ...]
    feature_names: tuple[str, ...]

    @classmethod
    def fit(cls, train: list[BillJourney], *, max_policy_areas: int = 32) -> JourneyFeaturizer:
        counts: dict[str, int] = {}
        for j in train:
            if j.policy_area:
                counts[j.policy_area] = counts.get(j.policy_area, 0) + 1
        vocab = tuple(sorted(sorted(counts), key=lambda p: -counts[p])[:max_policy_areas])
        names = (
            "is_house",
            "sponsor_in_majority",
            "log1p_cosponsors",
            "bipartisan_share",
            "committee_count",
            "intro_phase",
            *(f"policy:{p}" for p in vocab),
        )
        return cls(policy_areas=vocab, feature_names=names)

    def transform(self, journeys: list[BillJourney]) -> np.ndarray:
        index = {p: i for i, p in enumerate(self.policy_areas)}
        x = np.zeros((len(journeys), len(self.feature_names)), dtype=np.float64)
        for row, j in enumerate(journeys):
            x[row, 0] = 1.0 if j.chamber == "house" else 0.0
            x[row, 1] = 1.0 if j.sponsor_in_majority else 0.0
            x[row, 2] = float(np.log1p(j.cosponsor_count))
            x[row, 3] = j.bipartisan_share
            x[row, 4] = float(j.committee_count)
            x[row, 5] = j.intro_phase
            pos = index.get(j.policy_area)
            if pos is not None:
                x[row, 6 + pos] = 1.0
        return x
