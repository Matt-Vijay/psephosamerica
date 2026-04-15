"""DB-backed publish snapshot run.

Linear orchestration: fetch → assemble → plan → write.
All DB reads complete before the publish pipeline starts so the planner
closure is a pure data-capture callable with no live I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.export.contracts import EvidenceCardPayload, MemberProfilePayload, ZipFeedPayload
from src.export.writer import PlannedFile, plan_snapshot, serialize_payload
from src.pipeline.publish_pipeline import Planner, PublishConfig, PublishResult, run_publish
from src.query.evidence_card import assemble_evidence_card
from src.query.homepage_feed import assemble_homepage_payload
from src.query.member_profile import assemble_member_profile
from src.query.published_rows import (
    fetch_all_evidence_card_rows,
    fetch_current_member_slugs,
    fetch_homepage_feed_rows,
    fetch_member_committee_rows,
    fetch_member_row_by_slug,
    fetch_member_rule_fire_rows,
    fetch_member_score_snapshot_rows,
)
from src.query.zip_feed import assemble_zip_feed
from src.query.zip_rows import (
    fetch_recent_evidence_ids_by_bioguide,
    fetch_zip_member_summary_rows,
)
from src.zip.resolve import (
    DistrictMemberRow,
    SenatorRow,
    ZipDistrictRow,
    assemble_federal_bundle,
)


_HOMEPAGE_PATH = "homepage/feed.json"
_HOMEPAGE_FEED_LIMIT = 20


# ---------------------------------------------------------------------------
# ZIP bundle inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ZipBundleInputs:
    """Pre-loaded crosswalk data for all ZIP codes in this publish run.

    Callers load district and senator rows once and pass this object unchanged
    into publish_snapshot_run.  Decoupled from conn so callers control when
    geographic data is fetched and cached.
    """

    zip5_codes: list[str]
    zip_district_rows: list[ZipDistrictRow]
    district_member_rows: list[DistrictMemberRow]
    senator_rows: list[SenatorRow]


# ---------------------------------------------------------------------------
# Fetch + assemble helpers — one per payload family
# ---------------------------------------------------------------------------


def _build_member_profiles(
    conn: Any,
    member_slugs: list[str],
) -> list[MemberProfilePayload]:
    profiles: list[MemberProfilePayload] = []
    for slug in member_slugs:
        member_row = fetch_member_row_by_slug(conn, slug)
        if member_row is None:
            continue  # stale slug in caller list; skip without aborting run
        member_id: int = member_row["id"]
        snapshot_rows = fetch_member_score_snapshot_rows(conn, member_id)
        fire_rows = fetch_member_rule_fire_rows(conn, member_id)
        committee_rows = fetch_member_committee_rows(conn, member_id)
        profiles.append(
            assemble_member_profile(member_row, snapshot_rows, fire_rows, committee_rows)
        )
    return profiles


def _build_evidence_cards(conn: Any) -> list[EvidenceCardPayload]:
    card_rows = fetch_all_evidence_card_rows(conn)
    return [assemble_evidence_card(row) for row in card_rows]


def _build_zip_feeds(
    conn: Any,
    inputs: ZipBundleInputs,
    snapshot_date: date,
) -> list[ZipFeedPayload]:
    feeds: list[ZipFeedPayload] = []
    for zip5 in inputs.zip5_codes:
        bundle = assemble_federal_bundle(
            zip5,
            inputs.zip_district_rows,
            inputs.district_member_rows,
            inputs.senator_rows,
        )
        if bundle is None:
            continue  # unknown ZIP; not a fatal error

        refs = []
        if bundle.house_member is not None:
            refs.append(bundle.house_member)
        refs.extend(bundle.senators)

        bioguide_ids = [ref.bioguide_id for ref in refs]
        score_rows = fetch_zip_member_summary_rows(conn, bioguide_ids=bioguide_ids)
        evidence_ids = fetch_recent_evidence_ids_by_bioguide(conn, bioguide_ids=bioguide_ids)

        feeds.append(assemble_zip_feed(bundle, score_rows, evidence_ids, snapshot_date))
    return feeds


def _build_homepage_file(conn: Any, snapshot_date: date) -> PlannedFile:
    feed_rows = fetch_homepage_feed_rows(conn, limit=_HOMEPAGE_FEED_LIMIT)
    payload = assemble_homepage_payload(feed_rows, snapshot_date=snapshot_date)
    return PlannedFile.from_bytes(_HOMEPAGE_PATH, serialize_payload(payload))


# ---------------------------------------------------------------------------
# Planner closure
# ---------------------------------------------------------------------------


def _make_planner(
    snapshot_id: str,
    member_profiles: list[MemberProfilePayload],
    zip_feeds: list[ZipFeedPayload],
    evidence_cards: list[EvidenceCardPayload],
    homepage_file: PlannedFile,
) -> Planner:
    """Capture pre-assembled payloads; return a zero-arg planner for run_publish.

    plan_snapshot produces the integrity manifest covering member profiles, ZIP
    feeds, and evidence cards.  The homepage file is appended after the manifest;
    it is a derived view and intentionally excluded from the snapshot manifest.
    """

    def planner() -> list[PlannedFile]:
        snapshot_files = plan_snapshot(snapshot_id, member_profiles, zip_feeds, evidence_cards)
        return snapshot_files + [homepage_file]

    return planner


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def publish_snapshot_run(
    conn: Any,
    snapshot_id: str,
    snapshot_date: date,
    target_dir: Path,
    zip_bundle_inputs: ZipBundleInputs,
) -> PublishResult:
    """Orchestrate a full DB-backed snapshot publish.

    Steps are intentionally linear and named so the call sequence reads as
    documentation.  No DB calls happen inside the planner; all fetching is
    complete before run_publish starts.
    """
    member_slugs = fetch_current_member_slugs(conn)

    member_profiles = _build_member_profiles(conn, member_slugs)
    evidence_cards = _build_evidence_cards(conn)
    zip_feeds = _build_zip_feeds(conn, zip_bundle_inputs, snapshot_date)
    homepage_file = _build_homepage_file(conn, snapshot_date)

    planner = _make_planner(
        snapshot_id,
        member_profiles,
        zip_feeds,
        evidence_cards,
        homepage_file,
    )
    config = PublishConfig(snapshot_id=snapshot_id, target_dir=target_dir)
    return run_publish(config, planner)
