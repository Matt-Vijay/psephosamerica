"""Integrated stacked ensemble forecaster.

OVERALL_GOAL.md treats the LLM forecaster as an independent ensemble member,
stacked with the transformer on a held-out window. This module wires concrete
member forecasters -- a transformer/per-member-backed one and an LLM-backed one
-- through the held-out logistic stack (``ensemble.fit_stacked_ensemble``) into
one combined forecaster.

Each member is a :class:`~src.prediction.ensemble.Forecaster`: a callable from
``(canonical_person_id, canonical_bill_id)`` to a yea probability. The actual
frontier-model call (or transformer forward pass) lives inside that callable at
the edge, so this layer stays offline, deterministic, and testable while still
being the real integration point. Stacking weights are fit on held-out votes,
never the training window, preserving the no-leakage discipline.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.prediction.ensemble import (
    EnsembleExample,
    Forecaster,
    StackedEnsemble,
    fit_stacked_ensemble,
)


@dataclass(frozen=True)
class NamedForecaster:
    """An ensemble member: a name plus its forecaster."""

    name: str
    forecaster: Forecaster


@dataclass(frozen=True)
class HeldoutVote:
    """A held-out (person, bill) outcome used to fit the stack."""

    canonical_person_id: str
    canonical_bill_id: str
    is_yea: bool


@dataclass(frozen=True)
class StackedForecaster:
    """A fitted stack plus the member forecasters it combines."""

    members: list[NamedForecaster]
    stack: StackedEnsemble

    def forecast(self, *, canonical_person_id: str, canonical_bill_id: str) -> float:
        """Combine the members' forecasts for one (person, bill) pair."""
        forecasts = {
            member.name: member.forecaster.forecast(
                canonical_person_id=canonical_person_id,
                canonical_bill_id=canonical_bill_id,
            )
            for member in self.members
        }
        return self.stack.combine(forecasts)

    def with_members(self, members: list[NamedForecaster]) -> StackedForecaster:
        """Reuse the fitted stack weights with a different set of member forecasters.

        The member names must match the fitted stack; only the backing
        forecasters change (e.g. swapping fit-window members for serving-time
        ones). Useful for evaluating the same stack on a fresh window.
        """
        expected = {member.name for member in self.members}
        if {member.name for member in members} != expected:
            raise ValueError("member names must match the fitted stack")
        return StackedForecaster(members=members, stack=self.stack)


def fit_stacked_forecaster(
    members: list[NamedForecaster],
    holdout: list[HeldoutVote],
    *,
    epochs: int = 400,
    learning_rate: float = 0.3,
) -> StackedForecaster:
    """Query each member on the held-out votes and fit the logistic stack over them."""
    if not members:
        raise ValueError("members must not be empty")
    if not holdout:
        raise ValueError("holdout must not be empty")
    member_names = [member.name for member in members]
    examples = [
        EnsembleExample(
            forecasts={
                member.name: member.forecaster.forecast(
                    canonical_person_id=vote.canonical_person_id,
                    canonical_bill_id=vote.canonical_bill_id,
                )
                for member in members
            },
            is_yea=vote.is_yea,
        )
        for vote in holdout
    ]
    stack = fit_stacked_ensemble(
        examples,
        member_names=member_names,
        epochs=epochs,
        learning_rate=learning_rate,
    )
    return StackedForecaster(members=members, stack=stack)
