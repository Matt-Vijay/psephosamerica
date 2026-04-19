"""Immutable options type for the load-congress runtime command.

No execution logic lives here — only the contract callers pass in.
"""

from __future__ import annotations

import datetime as dt
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


@dataclass(frozen=True)
class CongressVoteCoverage:
    """Effective vote coverage for a live Congress load."""

    house_vote_year: int | None
    senate_session: int | None
    explicit_request: bool


def current_congress_for_date(today: dt.date | None = None) -> int:
    """Return the active Congress for a calendar date.

    Congress turns over on January 3 of odd-numbered years. Treat January 1-2
    of odd years as part of the previous Congress so operator defaults remain
    truthful during the turnover window.
    """
    current = today if today is not None else dt.date.today()
    effective_year = current.year
    if current.year % 2 == 1 and (current.month, current.day) < (1, 3):
        effective_year -= 1
    return ((effective_year - 1789) // 2) + 1


def resolve_congress_vote_coverage(
    congress: int,
    *,
    house_vote_year: int | None = None,
    senate_session: int | None = None,
    today: dt.date | None = None,
) -> CongressVoteCoverage:
    """Resolve the vote scope for a live Congress load.

    Explicit chamber arguments are preserved so operators can request a
    single chamber or a deliberately empty scope. When no vote scope is
    provided, derive the most sensible year/session pair from the target
    Congress and the current date.
    """
    explicit_request = house_vote_year is not None or senate_session is not None
    if explicit_request:
        return CongressVoteCoverage(
            house_vote_year=house_vote_year,
            senate_session=senate_session,
            explicit_request=True,
        )

    start_year = 1789 + ((congress - 1) * 2)
    end_year = start_year + 1
    current = today if today is not None else dt.date.today()
    current_congress = current_congress_for_date(current)

    if congress < current_congress:
        return CongressVoteCoverage(
            house_vote_year=end_year,
            senate_session=2,
            explicit_request=False,
        )

    if congress > current_congress:
        return CongressVoteCoverage(
            house_vote_year=start_year,
            senate_session=1,
            explicit_request=False,
        )

    if current.year <= start_year:
        return CongressVoteCoverage(
            house_vote_year=start_year,
            senate_session=1,
            explicit_request=False,
        )

    return CongressVoteCoverage(
        house_vote_year=end_year,
        senate_session=2,
        explicit_request=False,
    )
