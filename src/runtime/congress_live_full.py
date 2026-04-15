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
from src.ingest.congress.live_bill_details import fetch_primary_sponsor_specs
from src.ingest.congress.live_member_details import (
    fetch_member_detail_specs,
)
from src.pipeline.congress_load_run import CongressIngestInputs
from src.runtime.congress import CongressLoadResult, run_congress_load_runtime
from src.runtime.congress_votes import fetch_congress_vote_records


def run_live_congress_load_full(
    conn: Any,
    settings: Settings,
    *,
    congress: int,
    include_votes: bool = False,
    house_vote_year: int | None = None,
    senate_session: int | None = None,
) -> CongressLoadResult:
    with CongressAPIClient(settings.congress_api_key) as client:
        members = fetch_members(client, congress)
        member_terms, memberships = fetch_member_detail_specs(client, members)
        committees = fetch_committees(client, congress)
        bills = fetch_bills(client, congress)
        primary_sponsors = fetch_primary_sponsor_specs(client, bills)
        cosponsors = fetch_cosponsors_for_bills(client, bills)

    if include_votes:
        vote_result = fetch_congress_vote_records(
            congress=congress,
            house_vote_year=house_vote_year,
            senate_session=senate_session,
        )
        vote_events = vote_result.vote_events
        vote_casts = vote_result.vote_casts
    else:
        vote_events = []
        vote_casts = []

    inputs = CongressIngestInputs(
        members=members,
        member_terms=member_terms,
        committees=committees,
        memberships=memberships,
        bills=bills,
        primary_sponsors=primary_sponsors,
        cosponsors=cosponsors,
        vote_events=vote_events,
        vote_casts=vote_casts,
    )

    return run_congress_load_runtime(conn, inputs)
