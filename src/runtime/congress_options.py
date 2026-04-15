"""Immutable options type for the load-congress runtime command.

No execution logic lives here — only the contract callers pass in.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CongressLoadOptions:
    """Options controlling a single Congress load invocation.

    Attributes:
        congress:        Congress number (e.g. 119).
        include_votes:   Whether to fetch and load vote records.
        house_vote_year: Calendar year for House votes; None skips House votes.
        senate_session:  Session number (1 or 2) for Senate votes; None skips.
    """

    congress: int
    include_votes: bool = False
    house_vote_year: int | None = None
    senate_session: int | None = None
