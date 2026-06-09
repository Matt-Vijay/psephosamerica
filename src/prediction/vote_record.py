"""The domain ``VoteRecord``: one realized member vote with its ex-ante signals.

This is a pure foundation/domain type (no orchestration dependency) so the
``prediction`` layer can share it without importing ``runtime`` -- the layering
the architecture test enforces. ``runtime.cross_pressured_experiment`` builds
these from real roll-calls and re-exports the type for its own consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class VoteRecord:
    """One realized member vote with the signals knowable at the cutoff.

    ``party_alignment`` is +1 when the member's party leaned yea on the bill,
    else -1 (the party's majority direction, not the member's own choice).
    ``is_cross_pressured`` is the realized defection label (voted against that
    lean) -- used only to *score*, never to define a feature.
    """

    member: str
    party: str
    state: str
    vote_date: date
    is_yea: bool
    party_alignment: float
    sectors: tuple[str, ...]
    is_cross_pressured: bool
