"""Tests for the served defection prediction object."""

from __future__ import annotations

from datetime import date

from src.prediction.defection import build_party_profiles
from src.prediction.defection_bayesian import bootstrap_seed_ensemble
from src.prediction.defection_head import train_defection_head
from src.prediction.served_defection import assemble_served_defection
from src.prediction.vote_record import VoteRecord


def _record(member: str, *, is_yea: bool, lean_yea: bool, sectors, day: int) -> VoteRecord:
    return VoteRecord(
        member=member, party="R", state="TX", vote_date=date(2024, 1, day),
        is_yea=is_yea, party_alignment=1.0 if lean_yea else -1.0, sectors=sectors,
        is_cross_pressured=is_yea != lean_yea,
    )


def _corpus():
    out = []
    for day in range(1, 21):
        out.append(_record("D", is_yea=True, lean_yea=False, sectors=("energy_utilities",), day=day))
        out.append(_record("L1", is_yea=False, lean_yea=False, sectors=("energy_utilities",), day=day))
        out.append(_record("L2", is_yea=False, lean_yea=False, sectors=("health",), day=day))
    return out


def test_served_object_has_all_fields() -> None:
    corpus = _corpus()
    profiles = build_party_profiles(corpus)
    head = train_defection_head(corpus, profiles)
    ensemble = bootstrap_seed_ensemble(corpus, profiles, n_seeds=5)
    served = assemble_served_defection(
        corpus[0], "us_congress:118:hr-1", head, profiles, temperature=1.2, ensemble=ensemble,
    )
    assert 0.0 <= served.probability_defect <= 1.0
    assert served.interval_lower <= served.interval_upper
    assert served.evidence and served.explanation and served.counterfactual
    # every served field is provenance-carrying
    assert any(e["kind"] == "rollcall_bill" for e in served.evidence)
    payload = served.to_dict()
    assert set(payload) >= {"probability_defect", "interval_lower", "interval_upper", "evidence"}


def test_temperature_changes_probability() -> None:
    corpus = _corpus()
    profiles = build_party_profiles(corpus)
    head = train_defection_head(corpus, profiles)
    s1 = assemble_served_defection(corpus[0], "b", head, profiles, temperature=1.0)
    s2 = assemble_served_defection(corpus[0], "b", head, profiles, temperature=3.0)
    # higher temperature pulls probability toward 0.5
    assert abs(s2.probability_defect - 0.5) <= abs(s1.probability_defect - 0.5) + 1e-9
