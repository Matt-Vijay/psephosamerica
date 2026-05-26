"""DB-backed publish snapshot run.

Linear orchestration: fetch → assemble → plan → write.
All DB reads complete before the publish pipeline starts so the planner
closure is a pure data-capture callable with no live I/O.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.api.contracts import (
    ArtifactCounts,
    HomepageBootstrapPayload,
    MovementFeedPayload,
    SnapshotSummaryPayload,
    ZipEntryPayload,
)
from src.export.contracts import (
    EvidenceCardPayload,
    MemberHistoryPayload,
    MemberProfilePayload,
    SourceAnchor,
    ZipFeedPayload,
)
from src.export.manifest import SnapshotManifest
from src.export.writer import (
    PlannedFile,
    current_member_lookup_path,
    homepage_bootstrap_path,
    plan_snapshot,
    serialize_payload,
    zip_entry_path,
)
from src.homepage.builders import build_featured_lookup_entries
from src.homepage.contracts import HomepageFeedPayload
from src.identity.current_member_lookup import build_current_member_lookup
from src.identity.current_member_lookup import CurrentMemberLookupPayload
from src.ontology.contracts import OntologyEdgePayload, OntologyNodeRef
from src.ontology.member_features import build_member_feature_slices
from src.pipeline.publish_pipeline import Planner, PublishConfig, PublishResult, run_publish
from src.prediction.contracts import PredictionReadinessPayload
from src.prediction.readiness import build_prediction_readiness
from src.query.evidence_card import assemble_evidence_card
from src.query.homepage_feed import assemble_homepage_payload
from src.query.member_history import assemble_member_history
from src.query.member_profile import assemble_member_profile
from src.query.published_rows import (
    fetch_all_evidence_card_rows,
    fetch_all_ontology_edge_rows,
    fetch_current_member_slugs,
    fetch_homepage_feed_rows,
    fetch_member_committee_rows,
    fetch_member_row_by_slug,
    fetch_member_rule_fire_rows,
    fetch_member_score_snapshot_rows,
    fetch_vote_prediction_readiness_rows,
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


def _build_ontology_edges(conn: Any) -> list[OntologyEdgePayload]:
    return [_ontology_edge_from_row(row) for row in fetch_all_ontology_edge_rows(conn)]


def _build_prediction_readiness(
    conn: Any,
    *,
    snapshot_id: str,
    snapshot_date: date,
    member_profiles: list[MemberProfilePayload],
    ontology_edges: list[OntologyEdgePayload],
) -> PredictionReadinessPayload:
    return build_prediction_readiness(
        snapshot_id=snapshot_id,
        snapshot_date=snapshot_date,
        member_profiles=member_profiles,
        ontology_features=build_member_feature_slices(snapshot_id, ontology_edges),
        vote_rows=fetch_vote_prediction_readiness_rows(conn),
    )


def _ontology_edge_from_row(row: dict[str, Any]) -> OntologyEdgePayload:
    return OntologyEdgePayload(
        edge_id=row["edge_id"],
        edge_type=row["edge_type"],
        subject=OntologyNodeRef(
            node_type=row["subject_node_type"],
            node_id=row["subject_node_id"],
            label=row.get("subject_node_label"),
        ),
        object=OntologyNodeRef(
            node_type=row["object_node_type"],
            node_id=row["object_node_id"],
            label=row.get("object_node_label"),
        ),
        source_anchors=[
            SourceAnchor.model_validate(anchor) for anchor in row.get("source_anchors", [])
        ],
        confidence=row.get("confidence", "high"),
        attributes=row.get("attributes") or {},
    )


def _build_member_histories(
    conn: Any,
    member_slugs: list[str],
) -> list[MemberHistoryPayload]:
    histories: list[MemberHistoryPayload] = []
    for slug in member_slugs:
        member_row = fetch_member_row_by_slug(conn, slug)
        if member_row is None:
            continue
        member_id: int = member_row["id"]
        snapshot_rows = fetch_member_score_snapshot_rows(conn, member_id)
        fire_rows = fetch_member_rule_fire_rows(conn, member_id)
        committee_rows = fetch_member_committee_rows(conn, member_id)
        histories.append(
            assemble_member_history(member_row, snapshot_rows, fire_rows, committee_rows)
        )
    return histories


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
    payload = _build_homepage_payload(conn, snapshot_date)
    return _build_homepage_file_from_payload(payload)


def _build_homepage_payload(conn: Any, snapshot_date: date) -> HomepageFeedPayload:
    feed_rows = fetch_homepage_feed_rows(conn, limit=_HOMEPAGE_FEED_LIMIT)
    return assemble_homepage_payload(feed_rows, snapshot_date=snapshot_date)


def _build_homepage_file_from_payload(payload: HomepageFeedPayload) -> PlannedFile:
    return PlannedFile.from_bytes(_HOMEPAGE_PATH, serialize_payload(payload))


def _build_current_member_lookup_payload(
    member_profiles: list[MemberProfilePayload],
    snapshot_date: date,
) -> CurrentMemberLookupPayload:
    return build_current_member_lookup(member_profiles, snapshot_date=snapshot_date)


def _build_current_member_lookup_file(
    member_profiles: list[MemberProfilePayload],
    snapshot_date: date,
) -> PlannedFile:
    payload = _build_current_member_lookup_payload(member_profiles, snapshot_date=snapshot_date)
    return PlannedFile.from_bytes(current_member_lookup_path(), serialize_payload(payload))


def _build_homepage_bootstrap_file(
    homepage_payload: HomepageFeedPayload,
    current_member_lookup_payload: CurrentMemberLookupPayload,
    manifest: SnapshotManifest,
) -> PlannedFile:
    payload = HomepageBootstrapPayload(
        snapshot=_build_snapshot_summary_payload(
            manifest,
            snapshot_date=homepage_payload.snapshot_date,
        ),
        movement=MovementFeedPayload(
            snapshot_date=homepage_payload.snapshot_date,
            top_changes=homepage_payload.top_changes,
            recent_events=homepage_payload.recent_events,
            recent_evidence_card_ids=homepage_payload.recent_evidence_card_ids,
        ),
        featured_lookup_entries=build_featured_lookup_entries(
            homepage_payload.top_changes,
            homepage_payload.recent_events,
            current_member_lookup_payload.members,
        ),
    )
    return PlannedFile.from_bytes(homepage_bootstrap_path(), serialize_payload(payload))


def _build_artifact_counts(manifest: SnapshotManifest) -> ArtifactCounts:
    return ArtifactCounts(
        members=sum(1 for entry in manifest.entries if entry.path.startswith("members/")),
        evidence=sum(1 for entry in manifest.entries if entry.path.startswith("evidence/")),
        ontology_edges=sum(1 for entry in manifest.entries if entry.path == "ontology/edges.json"),
        ontology_member_graphs=sum(
            1 for entry in manifest.entries if entry.path.startswith("ontology/members/")
        ),
        zip_feeds=sum(1 for entry in manifest.entries if entry.path.startswith("zip/")),
        homepage_feeds=1,
        current_member_lookups=sum(
            1 for entry in manifest.entries if entry.path == "identity/current-member-lookup.json"
        ),
    )


def _build_snapshot_summary_payload(
    manifest: SnapshotManifest,
    *,
    snapshot_date: date,
) -> SnapshotSummaryPayload:
    return SnapshotSummaryPayload(
        snapshot_id=manifest.snapshot_id,
        snapshot_date=snapshot_date,
        published_at=manifest.created_at,
        root_sha256=manifest.root_sha256,
        total_files=manifest.total_files,
        total_bytes=manifest.total_bytes,
        artifact_counts=_build_artifact_counts(manifest),
    )


def _build_zip_entry_file(
    zip_feed: ZipFeedPayload,
    current_member_lookup_payload: CurrentMemberLookupPayload,
    manifest: SnapshotManifest,
) -> PlannedFile:
    lookup_by_bioguide_id = {
        entry.bioguide_id: entry for entry in current_member_lookup_payload.members
    }
    zip_entry = ZipEntryPayload(
        zip_feed=zip_feed,
        member_lookup_entries=[
            lookup_by_bioguide_id[member.bioguide_id]
            for member in zip_feed.members
            if member.bioguide_id in lookup_by_bioguide_id
        ],
        snapshot=_build_snapshot_summary_payload(
            manifest,
            snapshot_date=zip_feed.snapshot_date,
        ),
    )
    return PlannedFile.from_bytes(zip_entry_path(zip_feed.zip_code), serialize_payload(zip_entry))


# ---------------------------------------------------------------------------
# Planner closure
# ---------------------------------------------------------------------------


def _make_planner(
    snapshot_id: str,
    member_profiles: list[MemberProfilePayload],
    member_histories: list[MemberHistoryPayload],
    zip_feeds: list[ZipFeedPayload],
    evidence_cards: list[EvidenceCardPayload],
    ontology_edges: list[OntologyEdgePayload],
    current_member_lookup_file: PlannedFile,
    homepage_file: PlannedFile,
    prediction_readiness: PredictionReadinessPayload,
    *,
    current_member_lookup_payload: CurrentMemberLookupPayload | None = None,
    homepage_payload: HomepageFeedPayload | None = None,
) -> Planner:
    """Capture pre-assembled payloads; return a zero-arg planner for run_publish.

    plan_snapshot produces the integrity manifest covering member profiles, ZIP
    feeds, and evidence cards.  The homepage file is appended after the manifest;
    it is a derived view and intentionally excluded from the snapshot manifest.
    """

    def planner() -> list[PlannedFile]:
        snapshot_files = plan_snapshot(
            snapshot_id,
            member_profiles,
            zip_feeds,
            evidence_cards,
            member_histories=member_histories,
            ontology_edges=ontology_edges,
            current_member_lookup_file=current_member_lookup_file,
            prediction_readiness=prediction_readiness,
        )
        feed_payload = (
            homepage_payload
            if homepage_payload is not None
            else HomepageFeedPayload.model_validate(json.loads(homepage_file.content))
        )
        lookup_payload = (
            current_member_lookup_payload
            if current_member_lookup_payload is not None
            else _build_current_member_lookup_payload(member_profiles, feed_payload.snapshot_date)
        )
        manifest = SnapshotManifest.model_validate(json.loads(snapshot_files[-1].content))
        zip_entry_files = [
            _build_zip_entry_file(feed, lookup_payload, manifest) for feed in zip_feeds
        ]
        homepage_bootstrap_file = _build_homepage_bootstrap_file(
            feed_payload,
            lookup_payload,
            manifest,
        )
        return snapshot_files + [homepage_file, *zip_entry_files, homepage_bootstrap_file]

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
    member_histories = _build_member_histories(conn, member_slugs)
    evidence_cards = _build_evidence_cards(conn)
    ontology_edges = _build_ontology_edges(conn)
    prediction_readiness = _build_prediction_readiness(
        conn,
        snapshot_id=snapshot_id,
        snapshot_date=snapshot_date,
        member_profiles=member_profiles,
        ontology_edges=ontology_edges,
    )
    zip_feeds = _build_zip_feeds(conn, zip_bundle_inputs, snapshot_date)
    current_member_lookup_payload = _build_current_member_lookup_payload(
        member_profiles,
        snapshot_date,
    )
    current_member_lookup_file = PlannedFile.from_bytes(
        current_member_lookup_path(),
        serialize_payload(current_member_lookup_payload),
    )
    homepage_payload = _build_homepage_payload(conn, snapshot_date)
    homepage_file = _build_homepage_file_from_payload(homepage_payload)

    planner = _make_planner(
        snapshot_id,
        member_profiles,
        member_histories,
        zip_feeds,
        evidence_cards,
        ontology_edges,
        current_member_lookup_file,
        homepage_file,
        prediction_readiness,
        current_member_lookup_payload=current_member_lookup_payload,
        homepage_payload=homepage_payload,
    )
    config = PublishConfig(snapshot_id=snapshot_id, target_dir=target_dir)
    return run_publish(config, planner)
