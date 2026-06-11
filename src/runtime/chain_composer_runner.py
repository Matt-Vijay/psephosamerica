"""Price P(becomes law by deadline) for real bills, per-link attributed (v7 #3 runner).

Wires the chain composer to the fitted heads and the data-grounded link
constants, then prices a sample of live 119th-congress House bills:

* link 1 -- origin floor arrival: stage-hazard head trained on congresses
  <= 118, conditioned on the bill's age (survived periods are conditioned away);
* link 2 -- origin passage: correlated yes-count PMF at the House median pivot,
  member rates from the origin chamber's pre-deadline roll-calls;
* link 3 -- cross-chamber floor arrival: the empirical fraction of origin-
  passing bills later seen on the Senate floor, measured from the ingested
  113th-118th corpora (cited, congress-pooled);
* link 4/5 -- Senate cloture (>= 60) and passage: count PMF over Senate member
  rates;
* link 6 -- signature: cited historical prior (vetoes are <2% of presented
  bills in the modern sample).

Every link carries its source; the output JSON includes the attribution table
(which link costs the most log-probability). SIGNALS ONLY -- this prices, it
does not trade.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from src.prediction.chain_composer import (
    ChainLink,
    attribution_table,
    chamber_pivot,
    compose_chain,
    passage_probability,
)
from src.prediction.stage_hazard import fit_hazard
from src.prediction.vote_count_pmf import correlated_count_pmf
from src.runtime.bill_journey import (
    BillJourney,
    JourneyFeaturizer,
    load_bill_journeys,
    load_floor_events,
)
from src.runtime.bill_content_experiment import _iter_records
from src.runtime.count_pmf_experiment import _accumulate_rates
from src.runtime.stage_hazard_experiment import _HOUSE_CORPORA, _SENATE_CORPORA, _arrays

_SIGNATURE_PRIOR = 0.98
_SIGNATURE_CITE = (
    "historical prior: of bills presented 2013-2024 (113th-118th), vetoes were "
    "<2% (Senate Historical Office veto counts); override/return cases ignored"
)


def cross_chamber_rate(
    house_first: dict[str, date], senate_first: dict[str, date], *, congresses: range
) -> tuple[float, int, int]:
    """Fraction of House bills (hr/hjres) with a House floor vote that later reach the Senate floor."""
    reached = total = 0
    for key, h_date in house_first.items():
        congress_s, bill_type, _num = key.split(":")
        if int(congress_s) not in congresses or bill_type not in ("hr", "hjres"):
            continue
        total += 1
        s_date = senate_first.get(key)
        if s_date is not None and s_date >= h_date:
            reached += 1
    return (reached / total if total else 0.0), reached, total


def _member_probabilities(
    corpus: Path, *, rates_cutoff: date, lean_majority_yea: bool, minority_lean_yea: bool
) -> tuple[np.ndarray, np.ndarray]:
    """Roster probabilities for a hypothetical future floor vote.

    Uses the latest roll-call at or before the cutoff as the chamber roster and
    each member's pre-cutoff yea-rate conditioned on their party's assumed lean.
    """
    rolls = [r for r in _iter_records(corpus) if r.get("date")]
    rolls.sort(key=lambda r: str(r["date"]))
    past = [r for r in rolls if date.fromisoformat(str(r["date"])) <= rates_cutoff]
    if not past:
        return np.empty(0), np.empty(0)
    member_rates, party_rates = _accumulate_rates(past)
    roster = [v for v in past[-1].get("votes", []) if len(v) >= 4]
    probs: list[float] = []
    signs: list[float] = []
    majority = {"R"}  # 119th: R majority in both chambers
    for member, party, _state, _choice in roster:
        lean = lean_majority_yea if party in majority else minority_lean_yea
        yes, n = member_rates.get((member, lean), (0, 0))
        if n == 0:
            yes, n = party_rates.get((party, lean), (0, 0))
        probs.append((yes + 1.0) / (n + 2.0))
        signs.append(1.0 if party == "D" else -1.0 if party == "R" else 0.0)
    return np.asarray(probs, dtype=np.float64), np.asarray(signs, dtype=np.float64)


def price_bill(
    journey: BillJourney,
    p_floor: float,
    house_members: tuple[np.ndarray, np.ndarray],
    senate_members: tuple[np.ndarray, np.ndarray],
    *,
    sigma_common: float,
    sigma_party: float,
    cross_rate: float,
    cross_cite: str,
) -> dict[str, Any]:
    h_probs, h_signs = house_members
    s_probs, s_signs = senate_members
    house_pmf = correlated_count_pmf(
        h_probs, h_signs, sigma_common=sigma_common, sigma_party=sigma_party
    )
    senate_pmf = correlated_count_pmf(
        s_probs, s_signs, sigma_common=sigma_common, sigma_party=sigma_party
    )
    links = [
        ChainLink(
            "house_floor_by_deadline",
            p_floor,
            "stage_hazard head (trained <=118th)",
            f"conditional on surviving {journey.duration_days:.0f} days since introduction",
        ),
        ChainLink(
            "house_passage",
            passage_probability(house_pmf, chamber_pivot(h_probs.shape[0])),
            "correlated count PMF, House roster",
            f"pivot {chamber_pivot(h_probs.shape[0])} of {h_probs.shape[0]}; "
            f"majority leans yea, minority lean {'yea' if journey.bipartisan_share >= 0.25 else 'nay'}",
        ),
        ChainLink("senate_floor", cross_rate, "empirical cross-chamber rate", cross_cite),
        ChainLink(
            "senate_cloture",
            passage_probability(senate_pmf, 60),
            "correlated count PMF, Senate roster",
            "60-vote cloture pivot",
        ),
        ChainLink(
            "senate_passage",
            passage_probability(senate_pmf, chamber_pivot(s_probs.shape[0])),
            "correlated count PMF, Senate roster",
            f"pivot {chamber_pivot(s_probs.shape[0])} of {s_probs.shape[0]}",
        ),
        ChainLink("signature", _SIGNATURE_PRIOR, "historical prior", _SIGNATURE_CITE),
    ]
    price = compose_chain(links)
    return {
        "bill_key": journey.bill_key,
        "policy_area": journey.policy_area,
        "p_law_by_deadline": price.probability,
        "links": [link.as_dict() for link in price.links],
        "attribution": attribution_table(price),
    }


def run(
    sidecar: Path,
    *,
    as_of: date,
    deadline: date,
    sample: int = 8,
) -> dict[str, Any]:
    house_events = load_floor_events([Path(p) for p in _HOUSE_CORPORA])
    senate_events = load_floor_events([Path(p) for p in _SENATE_CORPORA])
    journeys = load_bill_journeys(sidecar, house_events, senate_events)

    train = [j for j in journeys if j.congress <= 118]
    featurizer = JourneyFeaturizer.fit(train)
    dur, ev = _arrays(train)
    hazard = fit_hazard(featurizer.transform(train), dur, ev, featurizer.feature_names)

    rate, reached, total = cross_chamber_rate(
        house_events[0], senate_events[0], congresses=range(113, 119)
    )
    cross_cite = (
        f"{reached}/{total} House bills (hr/hjres, 113th-118th) with a House floor "
        f"roll-call later reached a Senate floor roll-call in the ingested corpora"
    )

    live = [
        j for j in journeys if j.congress == 119 and j.chamber == "house" and not j.event_observed
    ]
    live.sort(key=lambda j: -j.cosponsor_count)
    sample_bills = live[:sample]
    x = featurizer.transform(sample_bills)
    horizon = float((deadline - as_of).days)
    ages = np.asarray([j.duration_days for j in sample_bills])
    p_floor = np.empty(len(sample_bills))
    for i in range(len(sample_bills)):
        p_floor[i] = float(
            hazard.predict_event_between(x[i : i + 1], float(ages[i]), float(ages[i]) + horizon)[0]
        )

    pmf_pin = json.loads(Path("benchmarks/count_pmf_baseline.json").read_text(encoding="utf-8"))
    sigma_common = float(pmf_pin["sigma_common"])
    sigma_party = float(pmf_pin["sigma_party"])

    house_members = _member_probabilities(
        Path("data/real/house_119_rich.jsonl"),
        rates_cutoff=as_of,
        lean_majority_yea=True,
        minority_lean_yea=False,
    )
    senate_members = _member_probabilities(
        Path("data/real/senate_119_rich.jsonl"),
        rates_cutoff=as_of,
        lean_majority_yea=True,
        minority_lean_yea=False,
    )

    priced = [
        price_bill(
            j,
            float(p_floor[i]),
            house_members,
            senate_members,
            sigma_common=sigma_common,
            sigma_party=sigma_party,
            cross_rate=rate,
            cross_cite=cross_cite,
        )
        for i, j in enumerate(sample_bills)
    ]
    return {
        "as_of": as_of.isoformat(),
        "deadline": deadline.isoformat(),
        "cross_chamber_rate": rate,
        "cross_chamber_evidence": cross_cite,
        "signature_prior": _SIGNATURE_PRIOR,
        "sigma": {"common": sigma_common, "party": sigma_party},
        "bills": priced,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Chain composer: price P(law by deadline)")
    p.add_argument("--sidecar", default="data/exports/govinfo_bills/bill_content_full.jsonl")
    p.add_argument("--as-of", default="2025-09-30")
    p.add_argument("--deadline", default="2027-01-03")
    p.add_argument("--out", default="benchmarks/chain_composer_sample.json")
    args = p.parse_args(argv)

    report = run(
        Path(args.sidecar),
        as_of=date.fromisoformat(args.as_of),
        deadline=date.fromisoformat(args.deadline),
    )
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    for b in report["bills"]:
        worst = max(b["attribution"], key=lambda a: a["log_cost_share"])
        print(
            f"{b['bill_key']:16s} P(law)={b['p_law_by_deadline']:.5f} | "
            f"binding link: {worst['name']} ({worst['probability']:.3f})"
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
