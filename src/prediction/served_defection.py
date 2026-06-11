"""Served defection prediction: calibrated probability + uncertainty + evidence (DoD).

OVERALL_GOAL.md's served object, for the defection product: for a (member, bill)
pair, emit a temperature-calibrated P(defect), a Bayesian uncertainty band (the
10-seed ensemble spread), the cited ex-ante evidence with signed contributions, a
plain-language explanation, and the counterfactual that would most change it.
Every field is derived from pre-cutoff signals (strict cutoff) and carries its
citation -- nothing served without provenance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.prediction.calibration import apply_temperature
from src.prediction.defection import PartyProfiles, defection_features
from src.prediction.defection_bayesian import EnsemblePrediction, ensemble_predict
from src.prediction.defection_head import DefectionHead
from src.prediction.vote_record import VoteRecord

_FACTOR_LABEL = {
    "loyalty_gap": "breaks with party overall",
    "sector_divergence": "breaks with party on this policy area",
}


@dataclass(frozen=True)
class ServedDefection:
    member: str
    party: str
    state: str
    bill_id: str
    probability_defect: float  # temperature-calibrated
    interval_lower: float
    interval_upper: float
    explanation: str
    counterfactual: str
    evidence: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _explanation(member: str, party: str, p: float, factors: list[tuple[str, float]]) -> str:
    lead = factors[0][0] if factors else ""
    label = _FACTOR_LABEL.get(lead, lead) or "their party-loyalty profile"
    likelihood = "likely" if p >= 0.5 else "unlikely" if p < 0.25 else "at some risk"
    return (
        f"{member} ({party}) is {likelihood} to break with the party on this bill, "
        f"driven mainly by: {label}."
    )


def assemble_served_defection(
    record: VoteRecord,
    bill_id: str,
    head: DefectionHead,
    profiles: PartyProfiles,
    *,
    temperature: float = 1.0,
    ensemble: list[DefectionHead] | None = None,
) -> ServedDefection:
    """Compose the calibrated, uncertainty-banded, cited, explained served object."""
    features = defection_features(record, profiles)
    raw = head.probability(features)
    calibrated = apply_temperature(raw, temperature)

    if ensemble:
        band: EnsemblePrediction = ensemble_predict(ensemble, features)
        lower = max(0.0, band.mean - band.std)
        upper = min(1.0, band.mean + band.std)
    else:
        lower = upper = calibrated

    contributions = head.contributions(features)
    ordered = sorted(contributions.items(), key=lambda kv: abs(kv[1]), reverse=True)
    top = max(contributions.items(), key=lambda kv: kv[1]) if contributions else ("", 0.0)
    counterfactual = (
        f"if the member no longer {_FACTOR_LABEL.get(top[0], top[0])}, P(defect) drops most"
        if top[1] > 0
        else "already at the party line on every factor"
    )
    evidence = [
        {"kind": "rollcall_bill", "ref": bill_id, "detail": "bill under vote"},
        {
            "kind": "member_history",
            "ref": record.member,
            "detail": "; ".join(
                f"{_FACTOR_LABEL.get(n, n)} contribution {v:+.3f}" for n, v in ordered
            ),
        },
    ]
    if record.sectors:
        evidence.append(
            {
                "kind": "policy_sectors",
                "ref": ",".join(record.sectors),
                "detail": "bill policy areas",
            }
        )
    return ServedDefection(
        member=record.member,
        party=record.party,
        state=record.state,
        bill_id=bill_id,
        probability_defect=calibrated,
        interval_lower=lower,
        interval_upper=upper,
        explanation=_explanation(record.member, record.party, calibrated, ordered),
        counterfactual=counterfactual,
        evidence=evidence,
    )
