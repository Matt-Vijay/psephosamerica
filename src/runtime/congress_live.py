"""Live Congress load: fetch from the API, then delegate to the runtime load.

One public entry point:

    run_live_congress_load(conn, settings, *, congress)

Fetch sequence:
  1. members   — iter_members(congress)
  2. committees — iter_committees(congress)
  3. bills      — iter_bills(congress)
  4. cosponsors — iter_cosponsors per bill

Votes are deferred: vote_events and vote_casts are passed as empty lists.
member_terms, memberships, and primary_sponsors require separate detail calls
that are not part of this initial live load path; they are also empty here.
"""

from __future__ import annotations

from typing import Any

from src.core.settings import Settings
from src.ingest.congress.congress_api import CongressAPIClient
from src.ingest.congress.live_api import (
    fetch_bills,
    fetch_committees,
    fetch_cosponsors_for_bills,
    fetch_members,
)
from src.pipeline.congress_load_run import CongressIngestInputs
from src.runtime.congress import CongressLoadResult, run_congress_load_runtime


def run_live_congress_load(
    conn: Any,
    settings: Settings,
    *,
    congress: int,
) -> CongressLoadResult:
    """Fetch Congress data from the API and load it into the canonical DB.

    Creates a CongressAPIClient, fetches the four available record types for
    the given congress number, then delegates to run_congress_load_runtime.

    Votes are explicitly deferred — vote_events and vote_casts are empty.
    """
    with CongressAPIClient(settings.congress_api_key) as client:
        members = fetch_members(client, congress)
        committees = fetch_committees(client, congress)
        bills = fetch_bills(client, congress)
        cosponsors = fetch_cosponsors_for_bills(client, bills)

    inputs = CongressIngestInputs(
        members=members,
        member_terms=[],
        committees=committees,
        memberships=[],
        bills=bills,
        primary_sponsors=[],
        cosponsors=cosponsors,
        vote_events=[],  # deferred: fetched via house/senate vote ingest paths
        vote_casts=[],  # deferred: fetched via house/senate vote ingest paths
    )

    return run_congress_load_runtime(conn, inputs)
