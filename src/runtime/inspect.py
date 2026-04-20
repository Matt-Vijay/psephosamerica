from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.export.contracts import EvidenceCardPayload, MemberProfilePayload, ZipFeedPayload
from src.export.local_store import (
    load_current_member_lookup,
    load_evidence_card,
    load_history_bootstrap,
    load_history_preset_range,
    load_homepage_bootstrap,
    load_homepage_feed,
    load_latest_manifest,
    load_latest_snapshot_metadata,
    load_manifest,
    load_member_change_summary,
    load_member_history_chart,
    load_member_history,
    load_member_history_page,
    load_member_page,
    load_member_preset_compare,
    load_snapshot_preset_compare,
    load_member_trend_summary,
    load_movement_window,
    load_member_profile,
    load_snapshot_index,
    list_snapshot_ids,
    load_zip_entry,
    load_zip_feed,
)
from src.export.manifest import SnapshotManifest
from src.homepage.contracts import HomepageFeedPayload, MovementWindowPayload, SnapshotComparePayload
from src.identity.current_member_lookup import (
    CurrentMemberLookupPayload,
    search_current_member_lookup,
)
from src.export.contracts import MemberHistoryPayload
from src.runtime.paths import local_publish_root

if TYPE_CHECKING:
    from src.api.contracts import HistoryBootstrapPayload
    from src.api.contracts import HistoryPresetRangePayload
    from src.api.contracts import HomepageBootstrapPayload
    from src.api.contracts import MemberHistoryPagePayload
    from src.api.contracts import MemberPagePayload
    from src.api.contracts import MemberWindowComparePayload
    from src.api.contracts import SnapshotIndexPayload
    from src.api.contracts import ZipEntryPayload
    from src.export.contracts import MemberChangeSummaryPayload
    from src.export.contracts import MemberHistoryChartPayload
    from src.export.contracts import MemberTrendSummaryPayload


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


def load_local_member_history(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberHistoryPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_member_history(root, slug)


def load_local_member_change_summary(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberChangeSummaryPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    summary = load_member_change_summary(root, slug)
    return summary


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
    snapshot_root: Path | None = None,
) -> MovementWindowPayload:
    root = snapshot_root if snapshot_root is not None else local_publish_root()
    return load_movement_window(root, name)


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
