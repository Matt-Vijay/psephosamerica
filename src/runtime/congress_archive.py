"""Runtime entry point for the Congress local-archive load path.

Builds CongressIngestInputs from a local CongressArchive through the same
enrichment sequence the live runtime uses, then delegates to
run_congress_load_runtime for provenance bookkeeping and DB writes.

One public entry point:

    run_congress_archive_load(conn, archive, options)

*archive* accepts three forms:

    CongressArchive          — path resolver; existing behaviour.
    Path (directory)         — wrapped into CongressArchive(path, congress).
    Path (manifest .json)    — loaded via load_manifest, validated via
                               validate_manifest, then resolved to a
                               CongressArchive from the manifest root.

Fetch sequence (all reads come from the local archive — no network):
  1. members, committees,  — CongressArchiveClient via live_api fetch helpers
     bills, cosponsors       (compatible iter_* interface)
  2. member_terms,         — archive_loader payload maps + member_terms /
     memberships             member_committees spec builders
  3. primary_sponsors      — archive_loader bill-detail map + primary_sponsors
                             spec builder
  4. votes (optional)      — archive_votes.load_house_vote_records and
                             load_senate_vote_records; gated by options
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from src.ingest.congress.archive import CongressArchive, CongressArchiveManifest
from src.ingest.congress.archive_client import CongressArchiveClient
from src.ingest.congress.congress_api import CongressAPIClient
from src.ingest.congress.archive_loader import (
    load_bill_detail_payload_map,
    load_member_detail_payload_map,
)
from src.ingest.congress.archive_manifest import load_manifest
from src.ingest.congress.archive_validate import validate_congress_archive_manifest
from src.ingest.congress.archive_votes import (
    load_house_vote_records,
    load_senate_vote_records,
)
from src.ingest.congress.live_api import (
    fetch_bills,
    fetch_committees,
    fetch_cosponsors_for_bills,
    fetch_members,
)
from src.ingest.congress.member_committees import committee_membership_specs_from_detail
from src.ingest.congress.member_terms import member_term_specs_from_detail
from src.ingest.congress.primary_sponsors import primary_sponsor_spec_from_bill_detail
from src.ingest.congress.models import VoteCastRecord, VoteEventRecord
from src.pipeline.congress_load_run import CongressIngestInputs
from src.runtime.congress import CongressLoadResult, run_congress_load_runtime
from src.runtime.congress_options import CongressLoadOptions


def _archive_from_manifest(manifest: CongressArchiveManifest) -> CongressArchive:
    """Return a CongressArchive whose root is inferred from the manifest."""
    root = manifest.members.path.parent
    return CongressArchive(root, manifest.congress)


def run_congress_archive_load(
    conn: Any,
    archive: CongressArchive | Path,
    options: CongressLoadOptions,
) -> CongressLoadResult:
    """Build CongressIngestInputs from *archive* and load into the canonical DB.

    *archive* may be:

    - a ``CongressArchive`` — used directly; no validation step.
    - a ``Path`` to a directory — wrapped into
      ``CongressArchive(path, options.congress)``; no validation step.
    - a ``Path`` to a JSON manifest file (suffix ``.json``) — the manifest is
      loaded with :func:`load_manifest` then validated with
      :func:`validate_congress_archive_manifest` before the archive root is
      extracted.  Raises ``ValueError`` if any referenced path is missing.

    Uses CongressArchiveClient as a drop-in for CongressAPIClient for list
    fetching (iter_members, iter_committees, iter_bills, iter_cosponsors).
    Detail enrichment for member terms, committee memberships, and primary
    sponsors is done via bulk payload maps from archive_loader together with
    the same spec-builder helpers used by the live enrichment path.

    Vote records are loaded only when options.include_votes is True.
    house_vote_year and senate_session from *options* gate each leg
    independently; a None value skips that chamber.
    """
    if isinstance(archive, Path) and archive.suffix == ".json":
        manifest = load_manifest(archive)
        result = validate_congress_archive_manifest(manifest)
        if not result.valid:
            missing_labels = ", ".join(m.label for m in result.missing)
            raise ValueError(f"archive manifest references missing files: {missing_labels}")
        archive = _archive_from_manifest(manifest)
    elif isinstance(archive, Path):
        archive = CongressArchive(archive, options.congress)

    client = CongressArchiveClient(archive)
    api_like_client = cast(CongressAPIClient, client)

    # ------------------------------------------------------------------
    # List records — compatible with live_api.py fetch helpers
    # ------------------------------------------------------------------
    members = fetch_members(api_like_client, options.congress)
    committees = fetch_committees(api_like_client, options.congress)
    bills = fetch_bills(api_like_client, options.congress)
    cosponsors = fetch_cosponsors_for_bills(api_like_client, bills)

    # ------------------------------------------------------------------
    # Member detail enrichment — same spec builders as the live path
    # ------------------------------------------------------------------
    member_detail_map = load_member_detail_payload_map(archive)
    member_terms = []
    memberships = []
    for member in members:
        detail: dict[str, Any] = member_detail_map.get(member.bioguide_id) or {}
        member_terms.extend(member_term_specs_from_detail(detail, member))
        memberships.extend(committee_membership_specs_from_detail(detail, member))

    # ------------------------------------------------------------------
    # Bill detail enrichment — same spec builder as the live path
    # ------------------------------------------------------------------
    bill_detail_map = load_bill_detail_payload_map(archive)
    primary_sponsors = []
    for bill in bills:
        bill_detail = bill_detail_map.get((bill.congress, bill.bill_type, bill.bill_number))
        if bill_detail is not None:
            spec = primary_sponsor_spec_from_bill_detail(bill_detail, bill)
            if spec is not None:
                primary_sponsors.append(spec)

    # ------------------------------------------------------------------
    # Optional votes — gated by options; each chamber leg is independent
    # ------------------------------------------------------------------
    vote_events: list[VoteEventRecord] = []
    vote_casts: list[VoteCastRecord] = []

    if options.include_votes:
        if options.house_vote_year is not None:
            house = load_house_vote_records(archive.root, options.house_vote_year)
            vote_events.extend(house.events)
            vote_casts.extend(house.casts)

        if options.senate_session is not None:
            senate = load_senate_vote_records(
                archive.root, options.congress, options.senate_session
            )
            vote_events.extend(senate.events)
            vote_casts.extend(senate.casts)

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
