from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.path_safety import safe_join_confined
from src.identity.current_member_lookup import (
    CurrentMemberLookupPayload,
    validate_current_member_lookup,
)
from src.ontology.agent_tools import OntologyAgentToolManifestPayload
from src.ontology.contracts import (
    OntologyGraphPayload,
    OntologyIndexPayload,
    OntologyMemberFeaturesPayload,
    OntologyMemberGraphPayload,
)
from src.ontology.frontend_contracts import OntologyFrontendContractPayload
from src.ontology.static_schema import OntologyFrontendIndexPayload, OntologyStaticSchemaPayload
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

from .contracts import (
    EvidenceCardPayload,
    MemberHistoryPayload,
    MemberProfilePayload,
    ZipFeedPayload,
)
from .manifest import SnapshotManifest
from .writer import (
    current_member_lookup_path,
    evidence_path,
    history_bootstrap_path,
    history_coverage_path,
    history_event_page_path,
    history_event_path,
    history_preset_range_path,
    homepage_bootstrap_path,
    manifest_path,
    member_change_summary_path,
    member_history_chart_path,
    member_history_coverage_index_path,
    member_history_coverage_path,
    member_history_page_path,
    member_history_path,
    member_page_payload_path,
    member_path,
    member_preset_compare_path,
    member_timeline_dimension_path,
    member_timeline_index_path,
    member_timeline_page_path,
    member_timeline_year_path,
    member_trend_summary_path,
    movement_window_path,
    ontology_agent_tools_path,
    ontology_edges_path,
    ontology_frontend_client_path,
    ontology_frontend_contract_path,
    ontology_frontend_index_path,
    ontology_frontend_types_path,
    ontology_index_path,
    ontology_member_edges_path,
    ontology_member_features_path,
    ontology_schema_path,
    prediction_bootstrap_path,
    prediction_committee_context_path,
    prediction_committee_readiness_path,
    prediction_member_context_path,
    prediction_member_readiness_path,
    prediction_readiness_index_path,
    prediction_readiness_path,
    prediction_sector_context_path,
    prediction_sector_readiness_path,
    prediction_source_context_path,
    prediction_source_index_path,
    prediction_topology_path,
    snapshot_index_path,
    snapshot_preset_compare_path,
    zip_entry_path,
    zip_path,
)

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
    from src.homepage.contracts import (
        HomepageFeedPayload,
        MovementWindowPayload,
        SnapshotComparePayload,
    )


HOMEPAGE_FEED_PATH = "homepage/feed.json"
_SNAPSHOT_PRESET_KEYS = frozenset({"latest", "4w", "12w", "cycle"})


# ── Path safety ────────────────────────────────────────────────────


def _safe_subpath(root: Path, relative: str) -> Path:
    """Resolve *relative* under *root* and reject any path that escapes it.

    Raises ``ValueError`` on traversal attempts (``..``), null bytes, or
    absolute segments that would land outside the snapshot root.
    """
    if "\x00" in relative:
        raise ValueError(f"Path segment contains null byte: {relative!r}")
    try:
        return safe_join_confined(root, relative, label="artifact path")
    except ValueError as exc:
        raise ValueError(f"Path escapes snapshot root or is not confined: {relative!r}") from exc


# ── Internal loader ────────────────────────────────────────────────


def _load_json(file: Path) -> object:
    if not file.exists():
        raise FileNotFoundError(f"Artifact not found: {file}")
    try:
        return json.loads(file.read_bytes())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {file}: {exc}") from exc


# ── Content artifact loaders ──────────────────────────────────────


def load_member_profile(snapshot_root: Path, slug: str) -> MemberProfilePayload:
    file = _safe_subpath(snapshot_root, member_path(slug))
    data = _load_json(file)
    return MemberProfilePayload.model_validate(data)


def load_member_page(
    snapshot_root: Path,
    slug: str,
) -> MemberPagePayload:
    from src.api.contracts import MemberPagePayload

    file = _safe_subpath(snapshot_root, member_page_payload_path(slug))
    data = _load_json(file)
    return MemberPagePayload.model_validate(data)


def load_evidence_card(snapshot_root: Path, evidence_card_id: str) -> EvidenceCardPayload:
    file = _safe_subpath(snapshot_root, evidence_path(evidence_card_id))
    data = _load_json(file)
    return EvidenceCardPayload.model_validate(data)


def load_ontology_edges(snapshot_root: Path) -> OntologyGraphPayload:
    file = _safe_subpath(snapshot_root, ontology_edges_path())
    data = _load_json(file)
    return OntologyGraphPayload.model_validate(data)


def load_ontology_agent_tools(snapshot_root: Path) -> OntologyAgentToolManifestPayload:
    file = _safe_subpath(snapshot_root, ontology_agent_tools_path())
    data = _load_json(file)
    return OntologyAgentToolManifestPayload.model_validate(data)


def load_ontology_static_schema(snapshot_root: Path) -> OntologyStaticSchemaPayload:
    file = _safe_subpath(snapshot_root, ontology_schema_path())
    data = _load_json(file)
    return OntologyStaticSchemaPayload.model_validate(data)


def load_ontology_frontend_contract(snapshot_root: Path) -> OntologyFrontendContractPayload:
    file = _safe_subpath(snapshot_root, ontology_frontend_contract_path())
    data = _load_json(file)
    return OntologyFrontendContractPayload.model_validate(data)


def load_ontology_frontend_types(snapshot_root: Path) -> str:
    file = _safe_subpath(snapshot_root, ontology_frontend_types_path())
    if not file.exists():
        raise FileNotFoundError(f"Artifact not found: {file}")
    return file.read_text(encoding="utf-8")


def load_ontology_frontend_client(snapshot_root: Path) -> str:
    file = _safe_subpath(snapshot_root, ontology_frontend_client_path())
    if not file.exists():
        raise FileNotFoundError(f"Artifact not found: {file}")
    return file.read_text(encoding="utf-8")


def load_ontology_index(snapshot_root: Path) -> OntologyIndexPayload:
    file = _safe_subpath(snapshot_root, ontology_index_path())
    data = _load_json(file)
    return OntologyIndexPayload.model_validate(data)


def load_ontology_frontend_index(snapshot_root: Path) -> OntologyFrontendIndexPayload:
    file = _safe_subpath(snapshot_root, ontology_frontend_index_path())
    data = _load_json(file)
    return OntologyFrontendIndexPayload.model_validate(data)


def load_ontology_member_edges(
    snapshot_root: Path,
    member_bioguide_id: str,
) -> OntologyMemberGraphPayload:
    file = _safe_subpath(snapshot_root, ontology_member_edges_path(member_bioguide_id))
    data = _load_json(file)
    return OntologyMemberGraphPayload.model_validate(data)


def load_ontology_member_features(
    snapshot_root: Path,
    member_bioguide_id: str,
) -> OntologyMemberFeaturesPayload:
    file = _safe_subpath(snapshot_root, ontology_member_features_path(member_bioguide_id))
    data = _load_json(file)
    return OntologyMemberFeaturesPayload.model_validate(data)


def load_prediction_readiness(snapshot_root: Path) -> PredictionReadinessPayload:
    file = _safe_subpath(snapshot_root, prediction_readiness_path())
    data = _load_json(file)
    return PredictionReadinessPayload.model_validate(data)


def load_prediction_bootstrap(snapshot_root: Path) -> PredictionBootstrapPayload:
    file = _safe_subpath(snapshot_root, prediction_bootstrap_path())
    data = _load_json(file)
    return PredictionBootstrapPayload.model_validate(data)


def load_prediction_topology(snapshot_root: Path) -> PredictionTopologyPayload:
    file = _safe_subpath(snapshot_root, prediction_topology_path())
    data = _load_json(file)
    return PredictionTopologyPayload.model_validate(data)


def load_prediction_readiness_index(snapshot_root: Path) -> PredictionReadinessIndexPayload:
    file = _safe_subpath(snapshot_root, prediction_readiness_index_path())
    data = _load_json(file)
    return PredictionReadinessIndexPayload.model_validate(data)


def load_prediction_source_index(snapshot_root: Path) -> PredictionSourceIndexPayload:
    file = _safe_subpath(snapshot_root, prediction_source_index_path())
    data = _load_json(file)
    return PredictionSourceIndexPayload.model_validate(data)


def load_prediction_source_context(
    snapshot_root: Path,
    source_key: str,
) -> PredictionSourceContextPayload:
    file = _safe_subpath(snapshot_root, prediction_source_context_path(source_key))
    data = _load_json(file)
    payload = PredictionSourceContextPayload.model_validate(data)
    if payload.source.source_key != source_key:
        raise ValueError("prediction source context source_key does not match requested key")
    return payload


def load_prediction_sector_readiness(snapshot_root: Path) -> PredictionSectorReadinessPayload:
    file = _safe_subpath(snapshot_root, prediction_sector_readiness_path())
    data = _load_json(file)
    return PredictionSectorReadinessPayload.model_validate(data)


def load_prediction_sector_context(
    snapshot_root: Path,
    sector_id: str,
) -> PredictionSectorContextPayload:
    file = _safe_subpath(snapshot_root, prediction_sector_context_path(sector_id))
    data = _load_json(file)
    payload = PredictionSectorContextPayload.model_validate(data)
    if payload.sector.sector_id != sector_id:
        raise ValueError("prediction sector context sector_id does not match requested id")
    return payload


def load_prediction_committee_readiness(
    snapshot_root: Path,
) -> PredictionCommitteeReadinessPayload:
    file = _safe_subpath(snapshot_root, prediction_committee_readiness_path())
    data = _load_json(file)
    return PredictionCommitteeReadinessPayload.model_validate(data)


def load_prediction_committee_context(
    snapshot_root: Path,
    committee_id: str,
) -> PredictionCommitteeContextPayload:
    file = _safe_subpath(snapshot_root, prediction_committee_context_path(committee_id))
    data = _load_json(file)
    payload = PredictionCommitteeContextPayload.model_validate(data)
    if payload.committee.committee_id != committee_id:
        raise ValueError("prediction committee context committee_id does not match requested id")
    return payload


def load_prediction_member_readiness(
    snapshot_root: Path,
    member_bioguide_id: str,
) -> PredictionMemberReadinessPayload:
    file = _safe_subpath(snapshot_root, prediction_member_readiness_path(member_bioguide_id))
    data = _load_json(file)
    payload = PredictionMemberReadinessPayload.model_validate(data)
    if payload.bioguide_id != member_bioguide_id:
        raise ValueError("prediction member readiness bioguide_id does not match requested id")
    return payload


def load_prediction_member_context(
    snapshot_root: Path,
    member_bioguide_id: str,
) -> PredictionMemberContextPayload:
    file = _safe_subpath(snapshot_root, prediction_member_context_path(member_bioguide_id))
    data = _load_json(file)
    payload = PredictionMemberContextPayload.model_validate(data)
    if payload.member_bioguide_id != member_bioguide_id:
        raise ValueError("prediction member context bioguide_id does not match requested id")
    return payload


def load_member_history(snapshot_root: Path, slug: str) -> MemberHistoryPayload:
    file = _safe_subpath(snapshot_root, member_history_path(slug))
    data = _load_json(file)
    return MemberHistoryPayload.model_validate(data)


def load_member_timeline_index(
    snapshot_root: Path,
    slug: str,
) -> MemberTimelineIndexPayload:
    from src.export.contracts import MemberTimelineIndexPayload

    file = _safe_subpath(snapshot_root, member_timeline_index_path(slug))
    data = _load_json(file)
    return MemberTimelineIndexPayload.model_validate(data)


def load_member_timeline_page(
    snapshot_root: Path,
    slug: str,
    page: int,
) -> MemberTimelinePagePayload:
    from src.export.contracts import MemberTimelinePagePayload

    file = _safe_subpath(snapshot_root, member_timeline_page_path(slug, page))
    data = _load_json(file)
    return MemberTimelinePagePayload.model_validate(data)


def load_member_timeline_dimension(
    snapshot_root: Path,
    slug: str,
    dimension: str,
) -> MemberTimelineDimensionPayload:
    from src.export.contracts import MemberTimelineDimensionPayload

    file = _safe_subpath(snapshot_root, member_timeline_dimension_path(slug, dimension))
    data = _load_json(file)
    return MemberTimelineDimensionPayload.model_validate(data)


def load_member_timeline_year(
    snapshot_root: Path,
    slug: str,
    year: int,
) -> MemberTimelineYearPayload:
    from src.export.contracts import MemberTimelineYearPayload

    file = _safe_subpath(snapshot_root, member_timeline_year_path(slug, year))
    data = _load_json(file)
    return MemberTimelineYearPayload.model_validate(data)


def load_history_event(
    snapshot_root: Path,
    event_id: str,
) -> MemberTimelineEventPayload:
    from src.export.contracts import MemberTimelineEventPayload

    file = _safe_subpath(snapshot_root, history_event_path(event_id))
    data = _load_json(file)
    return MemberTimelineEventPayload.model_validate(data)


def load_history_event_page(
    snapshot_root: Path,
    event_id: str,
) -> HistoryEventPagePayload:
    from src.api.contracts import HistoryEventPagePayload

    file = _safe_subpath(snapshot_root, history_event_page_path(event_id))
    data = _load_json(file)
    return HistoryEventPagePayload.model_validate(data)


def load_history_coverage(snapshot_root: Path) -> HistoryCoveragePayload:
    from src.export.contracts import HistoryCoveragePayload

    file = _safe_subpath(snapshot_root, history_coverage_path())
    data = _load_json(file)
    return HistoryCoveragePayload.model_validate(data)


def load_member_change_summary(
    snapshot_root: Path,
    slug: str,
) -> MemberChangeSummaryPayload:
    from src.export.contracts import MemberChangeSummaryPayload

    file = _safe_subpath(snapshot_root, member_change_summary_path(slug))
    data = _load_json(file)
    return MemberChangeSummaryPayload.model_validate(data)


def load_member_history_coverage(
    snapshot_root: Path,
    slug: str,
) -> MemberHistoryCoveragePayload:
    from src.export.contracts import MemberHistoryCoveragePayload

    file = _safe_subpath(snapshot_root, member_history_coverage_path(slug))
    data = _load_json(file)
    return MemberHistoryCoveragePayload.model_validate(data)


def load_member_history_coverage_index(
    snapshot_root: Path,
) -> MemberHistoryCoverageIndexPayload:
    from src.export.contracts import MemberHistoryCoverageIndexPayload

    file = _safe_subpath(snapshot_root, member_history_coverage_index_path())
    data = _load_json(file)
    return MemberHistoryCoverageIndexPayload.model_validate(data)


def load_member_history_chart(
    snapshot_root: Path,
    slug: str,
) -> MemberHistoryChartPayload:
    from src.export.contracts import MemberHistoryChartPayload

    file = _safe_subpath(snapshot_root, member_history_chart_path(slug))
    data = _load_json(file)
    return MemberHistoryChartPayload.model_validate(data)


def load_member_history_page(
    snapshot_root: Path,
    slug: str,
) -> MemberHistoryPagePayload:
    from src.api.contracts import MemberHistoryPagePayload

    file = _safe_subpath(snapshot_root, member_history_page_path(slug))
    data = _load_json(file)
    return MemberHistoryPagePayload.model_validate(data)


def load_member_preset_compare(
    snapshot_root: Path,
    slug: str,
    preset_key: str,
) -> MemberWindowComparePayload:
    from src.api.contracts import MemberWindowComparePayload

    file = _safe_subpath(snapshot_root, member_preset_compare_path(slug, preset_key))
    data = _load_json(file)
    return MemberWindowComparePayload.model_validate(data)


def load_member_trend_summary(
    snapshot_root: Path,
    slug: str,
) -> MemberTrendSummaryPayload:
    from src.export.contracts import MemberTrendSummaryPayload

    file = _safe_subpath(snapshot_root, member_trend_summary_path(slug))
    data = _load_json(file)
    return MemberTrendSummaryPayload.model_validate(data)


def load_snapshot_index(snapshot_root: Path) -> SnapshotIndexPayload:
    from src.api.contracts import SnapshotIndexPayload

    file = _safe_subpath(snapshot_root, snapshot_index_path())
    data = _load_json(file)
    return SnapshotIndexPayload.model_validate(data)


def load_history_bootstrap(snapshot_root: Path) -> HistoryBootstrapPayload:
    from src.api.contracts import HistoryBootstrapPayload

    file = _safe_subpath(snapshot_root, history_bootstrap_path())
    data = _load_json(file)
    return HistoryBootstrapPayload.model_validate(data)


def load_history_preset_range(
    snapshot_root: Path,
    preset_key: str,
) -> HistoryPresetRangePayload:
    from src.api.contracts import HistoryPresetRangePayload

    file = _safe_subpath(snapshot_root, history_preset_range_path(preset_key))
    data = _load_json(file)
    return HistoryPresetRangePayload.model_validate(data)


def load_homepage_bootstrap(snapshot_root: Path) -> HomepageBootstrapPayload:
    from src.api.contracts import HomepageBootstrapPayload

    file = _safe_subpath(snapshot_root, homepage_bootstrap_path())
    data = _load_json(file)
    return HomepageBootstrapPayload.model_validate(_normalize_homepage_bootstrap(data))


def load_snapshot_preset_compare(
    snapshot_root: Path,
    preset_key: str,
) -> SnapshotComparePayload:
    from src.homepage.contracts import SnapshotComparePayload

    if preset_key not in _SNAPSHOT_PRESET_KEYS:
        raise ValueError(f"Unknown snapshot preset key: {preset_key}")
    file = _safe_subpath(snapshot_root, snapshot_preset_compare_path(preset_key))
    data = _load_json(file)
    return SnapshotComparePayload.model_validate(data)


def load_movement_window(
    snapshot_root: Path,
    name: str = "latest",
    *,
    dimension: str | None = None,
) -> MovementWindowPayload:
    from src.homepage.contracts import MovementWindowPayload

    file = _safe_subpath(snapshot_root, movement_window_path(name, dimension=dimension))
    data = _load_json(file)
    return MovementWindowPayload.model_validate(data)


def load_zip_feed(snapshot_root: Path, zip_code: str) -> ZipFeedPayload:
    file = _safe_subpath(snapshot_root, zip_path(zip_code))
    data = _load_json(file)
    return ZipFeedPayload.model_validate(data)


def load_zip_entry(snapshot_root: Path, zip_code: str) -> ZipEntryPayload:
    from src.api.contracts import ZipEntryPayload

    file = _safe_subpath(snapshot_root, zip_entry_path(zip_code))
    data = _load_json(file)
    return ZipEntryPayload.model_validate(_normalize_zip_entry(data))


def load_homepage_feed(root: Path) -> HomepageFeedPayload:
    from src.homepage.contracts import HomepageFeedPayload

    """Load the pre-rendered homepage feed artifact from the publish tree."""
    file = _safe_subpath(root, HOMEPAGE_FEED_PATH)
    data = _load_json(file)
    return HomepageFeedPayload.model_validate(data)


def load_current_member_lookup(root: Path) -> CurrentMemberLookupPayload:
    """Load the pre-rendered current-member lookup artifact from the publish tree."""
    file = _safe_subpath(root, current_member_lookup_path())
    data = _load_json(file)
    payload = CurrentMemberLookupPayload.model_validate(_normalize_current_member_lookup(data))
    return validate_current_member_lookup(payload)


def _normalize_current_member_lookup_entries(
    members: object,
) -> object:
    if not isinstance(members, list):
        return members

    normalized_members: list[object] = []
    for member in members:
        if not isinstance(member, dict):
            normalized_members.append(member)
            continue
        if not any(key in member for key in ("b", "s", "n", "q", "st", "d", "c")):
            normalized_members.append(member)
            continue
        normalized_members.append(
            {
                "bioguide_id": member.get("b"),
                "slug": member.get("s"),
                "name": member.get("n"),
                "search_name": member.get("q"),
                "state": member.get("st"),
                "district": member.get("d"),
                "chamber": member.get("c"),
            }
        )
    return normalized_members


def _normalize_current_member_lookup(data: object) -> object:
    """Map the compact on-disk aliases back to the model field names.

    The writer serialises the lookup artifact using aliases for compactness.
    ``CurrentMemberLookupPayload`` itself does not enable alias-based input
    population, so we translate the published JSON shape back to the model's
    field names here before validation.
    """
    if not isinstance(data, dict):
        return data

    if "v" not in data and "sd" not in data and "m" not in data:
        return data

    return {
        "version": data.get("v"),
        "snapshot_date": data.get("sd"),
        "members": _normalize_current_member_lookup_entries(data.get("m", [])),
    }


def _normalize_homepage_bootstrap(data: object) -> object:
    if not isinstance(data, dict):
        return data
    featured_lookup_entries = data.get("featured_lookup_entries")
    if not isinstance(featured_lookup_entries, list):
        return data
    return {
        **data,
        "featured_lookup_entries": _normalize_current_member_lookup_entries(
            featured_lookup_entries
        ),
    }


def _normalize_zip_entry(data: object) -> object:
    if not isinstance(data, dict):
        return data
    member_lookup_entries = data.get("member_lookup_entries")
    if not isinstance(member_lookup_entries, list):
        return data
    return {
        **data,
        "member_lookup_entries": _normalize_current_member_lookup_entries(member_lookup_entries),
    }


# ── Manifest loaders ──────────────────────────────────────────────


def load_manifest(snapshot_root: Path, snapshot_id: str) -> SnapshotManifest:
    file = _safe_subpath(snapshot_root, manifest_path(snapshot_id))
    data = _load_json(file)
    return SnapshotManifest.model_validate(data)


def list_artifact_paths(manifest: SnapshotManifest) -> list[str]:
    return [entry.path for entry in manifest.entries]


def manifest_snapshot_date(manifest: SnapshotManifest) -> date:
    """Resolve a stable snapshot date from manifest metadata."""
    try:
        return date.fromisoformat(manifest.snapshot_id)
    except ValueError:
        return manifest.created_at.date()


def manifest_published_at(manifest: SnapshotManifest) -> datetime:
    """Resolve the manifest publication timestamp for API metadata."""
    return manifest.created_at


# ── Snapshot resolution ───────────────────────────────────────────


def latest_snapshot_id(root: Path) -> str:
    """Return the snapshot_id of the most recent published snapshot.

    Snapshot directories live at ``<root>/snapshots/<snapshot_id>/``.
    ``YYYY-MM-DD`` lexicographic order equals chronological order.

    Raises ``FileNotFoundError`` when no snapshots exist yet.
    """
    snapshots_dir = root / "snapshots"
    if not snapshots_dir.exists():
        raise FileNotFoundError(f"No snapshots directory found at: {snapshots_dir}")

    candidates = sorted(d.name for d in snapshots_dir.iterdir() if d.is_dir())
    if not candidates:
        raise FileNotFoundError(f"No snapshot directories found in: {snapshots_dir}")

    return candidates[-1]


def list_snapshot_ids(root: Path) -> list[str]:
    """Return all published snapshot ids sorted ascending."""
    snapshots_dir = root / "snapshots"
    if not snapshots_dir.exists():
        return []
    return sorted(d.name for d in snapshots_dir.iterdir() if d.is_dir())


def load_latest_manifest(root: Path) -> SnapshotManifest:
    """Load the manifest for the most recent published snapshot."""
    snapshot_id = latest_snapshot_id(root)
    return load_manifest(root, snapshot_id)


def load_latest_snapshot_metadata(root: Path) -> tuple[date, datetime]:
    """Return stable batch metadata for the most recent published snapshot."""
    manifest = load_latest_manifest(root)
    return (manifest_snapshot_date(manifest), manifest_published_at(manifest))
