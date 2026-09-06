from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.export.contracts import (
    EvidenceCardPayload,
    MemberHistoryPayload,
    MemberProfilePayload,
    ZipFeedPayload,
)
from src.export.local_store import (
    list_snapshot_ids,
    load_current_member_lookup,
    load_evidence_card,
    load_history_bootstrap,
    load_history_coverage,
    load_history_event,
    load_history_event_page,
    load_history_preset_range,
    load_homepage_bootstrap,
    load_homepage_feed,
    load_latest_manifest,
    load_latest_snapshot_metadata,
    load_manifest,
    load_member_change_summary,
    load_member_history,
    load_member_history_chart,
    load_member_history_coverage,
    load_member_history_coverage_index,
    load_member_history_page,
    load_member_page,
    load_member_preset_compare,
    load_member_profile,
    load_member_timeline_dimension,
    load_member_timeline_index,
    load_member_timeline_page,
    load_member_timeline_year,
    load_member_trend_summary,
    load_movement_window,
    load_ontology_edges,
    load_ontology_index,
    load_ontology_member_edges,
    load_ontology_member_features,
    load_prediction_bootstrap,
    load_prediction_committee_context,
    load_prediction_committee_readiness,
    load_prediction_member_context,
    load_prediction_member_readiness,
    load_prediction_readiness,
    load_prediction_readiness_index,
    load_prediction_sector_context,
    load_prediction_sector_readiness,
    load_prediction_source_context,
    load_prediction_source_index,
    load_prediction_topology,
    load_snapshot_index,
    load_snapshot_preset_compare,
    load_zip_entry,
    load_zip_feed,
)
from src.export.manifest import SnapshotManifest
from src.homepage.contracts import (
    HomepageFeedPayload,
    MovementWindowPayload,
    SnapshotComparePayload,
)
from src.identity.current_member_lookup import (
    CurrentMemberLookupPayload,
    search_current_member_lookup,
)
from src.ontology.contracts import (
    OntologyGraphPayload,
    OntologyIndexPayload,
    OntologyMemberFeaturesPayload,
    OntologyMemberGraphPayload,
)
from src.prediction.contracts import (
    PredictionBootstrapPayload,
    PredictionCommitteeContextPayload,
    PredictionCommitteeReadinessPayload,
    PredictionMemberContextPayload,
    PredictionMemberReadinessPayload,
    PredictionReadinessIndexPayload,
    PredictionReadinessPayload,
    PredictionSectorContextPayload,
    PredictionSectorReadinessPayload,
    PredictionSourceContextPayload,
    PredictionSourceIndexPayload,
    PredictionTopologyPayload,
)
from src.runtime.paths import local_publish_root

if TYPE_CHECKING:
    from src.api.contracts import (
        HistoryBootstrapPayload,
        HistoryEventPagePayload,
        HistoryPresetRangePayload,
        HomepageBootstrapPayload,
        MemberHistoryPagePayload,
        MemberPagePayload,
        MemberWindowComparePayload,
        SnapshotIndexPayload,
        ZipEntryPayload,
    )
    from src.export.contracts import (
        HistoryCoveragePayload,
        MemberChangeSummaryPayload,
        MemberHistoryChartPayload,
        MemberHistoryCoverageIndexPayload,
        MemberHistoryCoveragePayload,
        MemberTimelineDimensionPayload,
        MemberTimelineEventPayload,
        MemberTimelineIndexPayload,
        MemberTimelinePagePayload,
        MemberTimelineYearPayload,
        MemberTrendSummaryPayload,
    )
    from src.runtime.history_backfill_types import HistoryBackfillReportPayload


# ── Content artifact loaders ──────────────────────────────────────


def load_local_member_profile(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberProfilePayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_member_profile(root, slug)


def load_local_member_page(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberPagePayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_member_page(root, slug)


def load_local_evidence_card(
    evidence_card_id: str,
    *,
    snapshot_root: Path | None = None,
) -> EvidenceCardPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_evidence_card(root, evidence_card_id)


def load_local_ontology_edges(
    *,
    snapshot_root: Path | None = None,
) -> OntologyGraphPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_ontology_edges(root)


def load_local_ontology_index(
    *,
    snapshot_root: Path | None = None,
) -> OntologyIndexPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_ontology_index(root)


def load_local_ontology_member_edges(
    member_bioguide_id: str,
    *,
    snapshot_root: Path | None = None,
) -> OntologyMemberGraphPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_ontology_member_edges(root, member_bioguide_id)


def load_local_ontology_member_features(
    member_bioguide_id: str,
    *,
    snapshot_root: Path | None = None,
) -> OntologyMemberFeaturesPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_ontology_member_features(root, member_bioguide_id)


def load_local_prediction_readiness(
    *,
    snapshot_root: Path | None = None,
) -> PredictionReadinessPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_readiness(root)


def load_local_prediction_bootstrap(
    *,
    snapshot_root: Path | None = None,
) -> PredictionBootstrapPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_bootstrap(root)


def load_local_prediction_topology(
    *,
    snapshot_root: Path | None = None,
) -> PredictionTopologyPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_topology(root)


def load_local_prediction_readiness_index(
    *,
    snapshot_root: Path | None = None,
) -> PredictionReadinessIndexPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_readiness_index(root)


def load_local_prediction_source_index(
    *,
    snapshot_root: Path | None = None,
) -> PredictionSourceIndexPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_source_index(root)


def load_local_prediction_source_context(
    source_key: str,
    *,
    snapshot_root: Path | None = None,
) -> PredictionSourceContextPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_source_context(root, source_key)


def load_local_prediction_sector_readiness(
    *,
    snapshot_root: Path | None = None,
) -> PredictionSectorReadinessPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_sector_readiness(root)


def load_local_prediction_sector_context(
    sector_id: str,
    *,
    snapshot_root: Path | None = None,
) -> PredictionSectorContextPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_sector_context(root, sector_id)


def load_local_prediction_committee_readiness(
    *,
    snapshot_root: Path | None = None,
) -> PredictionCommitteeReadinessPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_committee_readiness(root)


def load_local_prediction_committee_context(
    committee_id: str,
    *,
    snapshot_root: Path | None = None,
) -> PredictionCommitteeContextPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_committee_context(root, committee_id)


def load_local_prediction_member_readiness(
    member_bioguide_id: str,
    *,
    snapshot_root: Path | None = None,
) -> PredictionMemberReadinessPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_member_readiness(root, member_bioguide_id)


def load_local_prediction_member_context(
    member_bioguide_id: str,
    *,
    snapshot_root: Path | None = None,
) -> PredictionMemberContextPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_prediction_member_context(root, member_bioguide_id)


def load_local_member_history(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberHistoryPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_member_history(root, slug)


def load_local_member_timeline_index(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberTimelineIndexPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_member_timeline_index(root, slug)


def load_local_member_timeline_page(
    slug: str,
    page: int,
    *,
    snapshot_root: Path | None = None,
) -> MemberTimelinePagePayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_member_timeline_page(root, slug, page)


def load_local_member_timeline_dimension(
    slug: str,
    dimension: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberTimelineDimensionPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_member_timeline_dimension(root, slug, dimension)


def load_local_member_timeline_year(
    slug: str,
    year: int,
    *,
    snapshot_root: Path | None = None,
) -> MemberTimelineYearPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_member_timeline_year(root, slug, year)


def load_local_history_backfill_report(
    target_root: Path,
) -> HistoryBackfillReportPayload:
    from src.runtime.history_backfill import load_history_backfill_report

    return load_history_backfill_report(target_root)


def load_local_history_event(
    event_id: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberTimelineEventPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_history_event(root, event_id)


def load_local_history_event_page(
    event_id: str,
    *,
    snapshot_root: Path | None = None,
) -> HistoryEventPagePayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_history_event_page(root, event_id)


def load_local_member_change_summary(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberChangeSummaryPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    summary = load_member_change_summary(root, slug)
    return summary


def load_local_member_history_coverage(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberHistoryCoveragePayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    coverage = load_member_history_coverage(root, slug)
    return coverage


def load_local_member_history_coverage_index(
    *,
    snapshot_root: Path | None = None,
) -> MemberHistoryCoverageIndexPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_member_history_coverage_index(root)


def load_local_member_history_chart(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberHistoryChartPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    chart = load_member_history_chart(root, slug)
    return chart


def load_local_member_history_page(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberHistoryPagePayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_member_history_page(root, slug)


def load_local_member_preset_compare(
    slug: str,
    preset_key: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberWindowComparePayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    compare = load_member_preset_compare(root, slug, preset_key)
    return compare


def load_local_member_trend_summary(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberTrendSummaryPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    summary = load_member_trend_summary(root, slug)
    return summary


def load_local_zip_feed(
    zip_code: str,
    *,
    snapshot_root: Path | None = None,
) -> ZipFeedPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_zip_feed(root, zip_code)


def load_local_zip_entry(
    zip_code: str,
    *,
    snapshot_root: Path | None = None,
) -> ZipEntryPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_zip_entry(root, zip_code)


def load_local_homepage_feed(
    *,
    snapshot_root: Path | None = None,
) -> HomepageFeedPayload:
    """Load the pre-rendered homepage feed artifact from the publish tree."""
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_homepage_feed(root)


def load_local_current_member_lookup(
    *,
    snapshot_root: Path | None = None,
) -> CurrentMemberLookupPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_current_member_lookup(root)


def search_local_current_member_lookup(
    query: str,
    *,
    limit: int = 8,
    snapshot_root: Path | None = None,
) -> CurrentMemberLookupPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    payload = load_current_member_lookup(root)
    return search_current_member_lookup(payload, query, limit=limit)


def load_local_snapshot_index(
    *,
    snapshot_root: Path | None = None,
) -> SnapshotIndexPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_snapshot_index(root)


def load_local_history_bootstrap(
    *,
    snapshot_root: Path | None = None,
) -> HistoryBootstrapPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_history_bootstrap(root)


def load_local_history_coverage(
    *,
    snapshot_root: Path | None = None,
) -> HistoryCoveragePayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_history_coverage(root)


def load_local_history_preset_range(
    preset_key: str,
    *,
    snapshot_root: Path | None = None,
) -> HistoryPresetRangePayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_history_preset_range(root, preset_key)


def load_local_homepage_bootstrap(
    *,
    snapshot_root: Path | None = None,
) -> HomepageBootstrapPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_homepage_bootstrap(root)


def load_local_snapshot_preset_compare(
    preset_key: str,
    *,
    snapshot_root: Path | None = None,
) -> SnapshotComparePayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_snapshot_preset_compare(root, preset_key)


def load_local_movement_window(
    name: str = "latest",
    *,
    dimension: str | None = None,
    snapshot_root: Path | None = None,
) -> MovementWindowPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_movement_window(root, name, dimension=dimension)


# ── Manifest and snapshot resolution ──────────────────────────────


def load_local_manifest(
    snapshot_id: str,
    *,
    snapshot_root: Path | None = None,
) -> SnapshotManifest:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_manifest(root, snapshot_id)


def load_latest_local_manifest(
    *,
    snapshot_root: Path | None = None,
) -> SnapshotManifest:
    """Return the manifest for the latest published snapshot."""
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_latest_manifest(root)


def load_latest_local_snapshot_metadata(
    *,
    snapshot_root: Path | None = None,
) -> tuple[date, datetime]:
    """Return snapshot date and published-at metadata for the latest snapshot."""
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_latest_snapshot_metadata(root)


def list_local_snapshot_ids(
    *,
    snapshot_root: Path | None = None,
) -> list[str]:
    """Return all locally published snapshot ids sorted ascending."""
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return list_snapshot_ids(root)
