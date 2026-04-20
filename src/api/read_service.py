"""Published-artifact read service for product-facing API surfaces.

This layer sits above :mod:`src.runtime.inspect` and below any web framework.
It loads the latest published artifacts, attaches consistent batch metadata,
and normalizes missing-resource responses into the shared ``not_found`` shape.
"""

from __future__ import annotations

from pathlib import Path

from src.export.contracts import (
    EvidenceCardPayload,
    MemberChangeSummaryPayload,
    MemberHistoryChartPayload,
    MemberHistoryPayload,
    MemberTrendSummaryPayload,
)
from src.export.local_store import manifest_published_at, manifest_snapshot_date
from src.homepage.contracts import SnapshotComparePayload
from src.homepage.contracts import MovementWindowPayload
from src.api.contracts import (
    ArtifactCounts,
    BatchMeta,
    CurrentMemberLookupResponse,
    EvidenceResponse,
    HistoryBootstrapPayload,
    HistoryBootstrapResponse,
    HistoryPresetRangePayload,
    HistoryPresetRangeResponse,
    HomepageBootstrapPayload,
    HomepageBootstrapResponse,
    HomepageResponse,
    LastUpdatedResponse,
    MemberChangeSummaryResponse,
    MemberHistoryChartResponse,
    MemberHistoryResponse,
    MemberComparePayload,
    MemberCompareResponse,
    MemberHistoryPagePayload,
    MemberHistoryPageResponse,
    MemberWindowComparePayload,
    MemberWindowCompareResponse,
    MemberCompareScoreRow,
    MemberPagePayload,
    MemberPageResponse,
    MovementFeedPayload,
    MovementFeedResponse,
    MemberTrendSummaryResponse,
    MovementWindowResponse,
    MemberResponse,
    NotFoundBody,
    SearchSessionPayload,
    SearchSessionResponse,
    SearchSessionResult,
    SnapshotCompareResponse,
    SnapshotIndexEntry,
    SnapshotIndexPayload,
    SnapshotIndexResponse,
    SnapshotSummaryPayload,
    SnapshotSummaryResponse,
    ZipEntryPayload,
    ZipEntryResponse,
    ZipResponse,
)
from src.api.read_api import (
    make_batch_meta,
    not_found,
    wrap_current_member_lookup,
    wrap_evidence,
    wrap_homepage,
    wrap_history_bootstrap,
    wrap_history_preset_range,
    wrap_homepage_bootstrap,
    wrap_last_updated,
    wrap_member_history,
    wrap_member_history_chart,
    wrap_member_history_page,
    wrap_member_change_summary,
    wrap_member_compare,
    wrap_member_window_compare,
    wrap_member,
    wrap_member_page,
    wrap_member_trend_summary,
    wrap_movement_feed,
    wrap_movement_window,
    wrap_search_session,
    wrap_snapshot_compare,
    wrap_snapshot_index,
    wrap_snapshot_summary,
    wrap_zip_entry,
    wrap_zip,
)
from src.identity.current_member_lookup import (
    CurrentMemberLookupEntry,
    search_current_member_lookup as _search_current_member_lookup,
)
from src.export.writer import build_member_page_payload
from src.runtime.inspect import (
    load_latest_local_manifest,
    load_latest_local_snapshot_metadata,
    load_local_current_member_lookup,
    load_local_evidence_card,
    load_local_history_bootstrap,
    load_local_history_preset_range,
    load_local_homepage_bootstrap,
    load_local_homepage_feed,
    load_local_manifest,
    load_local_member_change_summary,
    load_local_member_history_chart,
    load_local_member_history,
    load_local_member_history_page,
    load_local_member_page,
    load_local_member_preset_compare,
    load_local_member_trend_summary,
    load_local_movement_window,
    load_local_member_profile,
    load_local_snapshot_preset_compare,
    load_local_snapshot_index,
    load_local_zip_entry,
    load_local_zip_feed,
    list_local_snapshot_ids,
)
from src.runtime.paths import local_publish_root
from src.query.history_products import (
    build_movement_window,
    build_latest_movement_window,
    build_member_change_summary,
    build_member_history_chart,
    build_member_window_change_summary,
    build_snapshot_compare_presets,
    build_member_trend_summary,
    build_snapshot_compare_payload,
)
from src.homepage.builders import build_featured_lookup_entries

_SNAPSHOT_PRESET_KEYS = frozenset({"latest", "4w", "12w", "cycle"})


def _load_current_lookup_entries(*, snapshot_root: Path | None = None) -> list[CurrentMemberLookupEntry]:
    try:
        return load_local_current_member_lookup(snapshot_root=snapshot_root).members
    except FileNotFoundError:
        return []


def _slice_movement_feed_payload(
    payload: MovementFeedPayload,
    *,
    top_changes_limit: int | None = None,
    recent_events_limit: int | None = None,
) -> MovementFeedPayload:
    recent_events = (
        payload.recent_events[:recent_events_limit]
        if recent_events_limit is not None
        else payload.recent_events
    )
    top_changes = (
        payload.top_changes[:top_changes_limit]
        if top_changes_limit is not None
        else payload.top_changes
    )
    seen_ids: set[str] = set()
    recent_evidence_card_ids: list[str] = []
    for event in recent_events:
        if event.evidence_card_id and event.evidence_card_id not in seen_ids:
            seen_ids.add(event.evidence_card_id)
            recent_evidence_card_ids.append(event.evidence_card_id)
    return payload.model_copy(
        update={
            "top_changes": top_changes,
            "recent_events": recent_events,
            "recent_evidence_card_ids": recent_evidence_card_ids,
        }
    )


def _snapshot_summaries_match(
    left: SnapshotSummaryPayload,
    right: SnapshotSummaryPayload,
) -> bool:
    return left.model_dump(mode="json") == right.model_dump(mode="json")


def _meta_or_not_found(
    *,
    snapshot_root: Path | None = None,
) -> BatchMeta | NotFoundBody:
    try:
        snapshot_date, published_at = load_latest_local_snapshot_metadata(
            snapshot_root=snapshot_root
        )
    except FileNotFoundError:
        return not_found("snapshot", "latest")
    return make_batch_meta(snapshot_date, published_at)


def _published_root(snapshot_root: Path | None = None) -> Path:
    return snapshot_root if snapshot_root is not None else local_publish_root()


def get_homepage(
    *,
    snapshot_root: Path | None = None,
) -> HomepageResponse | NotFoundBody:
    try:
        payload = load_local_homepage_feed(snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("homepage", "current")
    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_homepage(payload, meta)


def get_homepage_bootstrap(
    *,
    snapshot_root: Path | None = None,
    top_changes_limit: int | None = None,
    recent_events_limit: int | None = None,
) -> HomepageBootstrapResponse | NotFoundBody:
    snapshot = get_snapshot_summary(snapshot_root=snapshot_root)
    if isinstance(snapshot, NotFoundBody):
        return snapshot
    lookup_entries = _load_current_lookup_entries(snapshot_root=snapshot_root)

    try:
        artifact_payload = load_local_homepage_bootstrap(snapshot_root=snapshot_root)
    except FileNotFoundError:
        artifact_payload = None
    else:
        assert artifact_payload is not None
        if not _snapshot_summaries_match(artifact_payload.snapshot, snapshot.data):
            artifact_payload = None

    if artifact_payload is None:
        movement_response = get_movement_feed(
            snapshot_root=snapshot_root,
            top_changes_limit=top_changes_limit,
            recent_events_limit=recent_events_limit,
        )
        if isinstance(movement_response, NotFoundBody):
            return not_found("homepage_bootstrap", "current")
        bootstrap_payload = HomepageBootstrapPayload(
            snapshot=snapshot.data,
            movement=movement_response.data,
            featured_lookup_entries=build_featured_lookup_entries(
                movement_response.data.top_changes,
                movement_response.data.recent_events,
                lookup_entries,
            ),
        )
    else:
        movement_data = _slice_movement_feed_payload(
            artifact_payload.movement,
            top_changes_limit=top_changes_limit,
            recent_events_limit=recent_events_limit,
        )
        bootstrap_payload = HomepageBootstrapPayload(
            snapshot=artifact_payload.snapshot,
            movement=movement_data,
            featured_lookup_entries=build_featured_lookup_entries(
                movement_data.top_changes,
                movement_data.recent_events,
                lookup_entries or artifact_payload.featured_lookup_entries,
            ),
        )

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta

    return wrap_homepage_bootstrap(bootstrap_payload, meta)


def get_history_bootstrap(
    *,
    snapshot_root: Path | None = None,
    top_changes_limit: int | None = None,
    featured_limit: int = 5,
) -> HistoryBootstrapResponse | NotFoundBody:
    try:
        payload = load_local_history_bootstrap(snapshot_root=snapshot_root)
    except FileNotFoundError:
        payload = None

    if payload is not None:
        movement_window = payload.movement_window.model_copy(
            update={
                "top_changes": (
                    payload.movement_window.top_changes[:top_changes_limit]
                    if top_changes_limit is not None
                    else payload.movement_window.top_changes
                )
            }
        )
        summary_by_slug = {
            summary.slug: summary for summary in payload.featured_member_changes
        }
        featured_member_changes = [
            summary_by_slug[change.slug]
            for change in movement_window.top_changes
            if change.slug in summary_by_slug
        ][:featured_limit]
        meta = _meta_or_not_found(snapshot_root=snapshot_root)
        if isinstance(meta, NotFoundBody):
            return meta
        return wrap_history_bootstrap(
            HistoryBootstrapPayload(
                snapshot_index=payload.snapshot_index,
                movement_window=movement_window,
                default_compare_preset_key=payload.default_compare_preset_key,
                compare_presets=payload.compare_presets,
                featured_member_changes=featured_member_changes,
            ),
            meta,
        )

    snapshot_index = get_snapshot_index(snapshot_root=snapshot_root)
    if isinstance(snapshot_index, NotFoundBody):
        return snapshot_index
    preset_set = build_snapshot_compare_presets(
        [
            (entry.snapshot_id, entry.snapshot_date)
            for entry in snapshot_index.data.snapshots
        ]
    )

    movement_window_response = get_movement_window(snapshot_root=snapshot_root)
    if isinstance(movement_window_response, NotFoundBody):
        return movement_window_response

    featured_member_changes = []
    seen_slugs: set[str] = set()
    top_changes = (
        movement_window_response.data.top_changes[:top_changes_limit]
        if top_changes_limit is not None
        else movement_window_response.data.top_changes
    )
    for change in top_changes:
        if change.slug in seen_slugs:
            continue
        seen_slugs.add(change.slug)
        try:
            featured_member_changes.append(
                _load_member_change_summary_payload(
                    change.slug,
                    snapshot_root=snapshot_root,
                )
            )
        except FileNotFoundError:
            continue
        if len(featured_member_changes) >= featured_limit:
            break

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta

    return wrap_history_bootstrap(
        HistoryBootstrapPayload(
            snapshot_index=snapshot_index.data,
            movement_window=movement_window_response.data,
            default_compare_preset_key=preset_set.default_preset_key,
            compare_presets=preset_set.presets,
            featured_member_changes=featured_member_changes,
        ),
        meta,
    )


def get_zip(
    zip_code: str,
    *,
    snapshot_root: Path | None = None,
) -> ZipResponse | NotFoundBody:
    try:
        payload = load_local_zip_feed(zip_code, snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("zip", zip_code)
    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_zip(payload, meta)


def get_member(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberResponse | NotFoundBody:
    try:
        payload = load_local_member_profile(slug, snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("member", slug)
    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_member(payload, meta)


def get_member_history(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberHistoryResponse | NotFoundBody:
    try:
        payload = load_local_member_history(slug, snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("member_history", slug)
    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_member_history(payload, meta)


def _load_evidence_cards(
    evidence_card_ids: list[str],
    *,
    snapshot_root: Path | None = None,
) -> list[EvidenceCardPayload]:
    cards = []
    seen_ids: set[str] = set()
    for evidence_card_id in evidence_card_ids:
        if not evidence_card_id or evidence_card_id in seen_ids:
            continue
        seen_ids.add(evidence_card_id)
        try:
            cards.append(
                load_local_evidence_card(
                    evidence_card_id,
                    snapshot_root=snapshot_root,
                )
            )
        except FileNotFoundError:
            continue
    return cards


def _load_member_change_summary_payload(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberChangeSummaryPayload:
    try:
        return load_local_member_change_summary(slug, snapshot_root=snapshot_root)
    except FileNotFoundError:
        history = load_local_member_history(slug, snapshot_root=snapshot_root)
        return build_member_change_summary(history)


def _load_member_trend_summary_payload(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberTrendSummaryPayload:
    try:
        return load_local_member_trend_summary(slug, snapshot_root=snapshot_root)
    except FileNotFoundError:
        history = load_local_member_history(slug, snapshot_root=snapshot_root)
        return build_member_trend_summary(history)


def _load_member_history_chart_payload(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberHistoryChartPayload:
    try:
        return load_local_member_history_chart(slug, snapshot_root=snapshot_root)
    except FileNotFoundError:
        history = load_local_member_history(slug, snapshot_root=snapshot_root)
        snapshot_index = _snapshot_index_payload_or_not_found(snapshot_root=snapshot_root)
        if isinstance(snapshot_index, NotFoundBody):
            raise FileNotFoundError(slug)
        return build_member_history_chart(
            history,
            snapshot_ids_by_date={
                entry.snapshot_date: entry.snapshot_id for entry in snapshot_index.snapshots
            },
        )


def _load_member_preset_compare_payload(
    slug: str,
    preset_key: str,
    *,
    snapshot_root: Path | None = None,
    evidence_cards_limit: int = 10,
) -> MemberWindowComparePayload | NotFoundBody:
    try:
        return load_local_member_preset_compare(
            slug,
            preset_key,
            snapshot_root=snapshot_root,
        )
    except FileNotFoundError:
        pass

    try:
        chart = _load_member_history_chart_payload(slug, snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("member_history", slug)

    preset = next(
        (candidate for candidate in chart.compare_presets if candidate.preset_key == preset_key),
        None,
    )
    if preset is None:
        return not_found("member_preset_compare", f"{slug}:{preset_key}")

    return _build_member_window_compare_payload(
        slug,
        preset.start_snapshot_id,
        preset.end_snapshot_id,
        snapshot_root=snapshot_root,
        evidence_cards_limit=evidence_cards_limit,
    )


def _load_all_member_histories(
    *,
    snapshot_root: Path | None = None,
) -> list[MemberHistoryPayload]:
    root = _published_root(snapshot_root)
    history_dir = root / "history" / "members"
    if not history_dir.exists():
        return []

    histories: list[MemberHistoryPayload] = []
    for file in sorted(history_dir.glob("*.json")):
        try:
            histories.append(load_local_member_history(file.stem, snapshot_root=snapshot_root))
        except FileNotFoundError:
            continue
    return histories


def _snapshot_index_payload_or_not_found(
    *,
    snapshot_root: Path | None = None,
) -> SnapshotIndexPayload | NotFoundBody:
    try:
        return load_local_snapshot_index(snapshot_root=snapshot_root)
    except FileNotFoundError:
        snapshot_ids = list_local_snapshot_ids(snapshot_root=snapshot_root)
        if not snapshot_ids:
            return not_found("snapshot", "latest")

        manifests = [
            load_local_manifest(snapshot_id, snapshot_root=snapshot_root)
            for snapshot_id in snapshot_ids
        ]
        manifests.sort(
            key=lambda manifest: (
                manifest_snapshot_date(manifest),
                manifest.snapshot_id,
            )
        )
        latest_manifest = manifests[-1]
        return SnapshotIndexPayload(
            latest_snapshot_id=latest_manifest.snapshot_id,
            snapshots=[
                SnapshotIndexEntry(
                    snapshot_id=manifest.snapshot_id,
                    snapshot_date=manifest_snapshot_date(manifest),
                    published_at=manifest_published_at(manifest),
                    root_sha256=manifest.root_sha256,
                    total_files=manifest.total_files,
                    total_bytes=manifest.total_bytes,
                )
                for manifest in manifests
            ],
        )


def _snapshot_entry_by_id(
    snapshot_index: SnapshotIndexPayload,
    snapshot_id: str,
) -> SnapshotIndexEntry | None:
    for entry in snapshot_index.snapshots:
        if entry.snapshot_id == snapshot_id:
            return entry
    return None


def _load_movement_window_payload(
    *,
    name: str = "latest",
    snapshot_root: Path | None = None,
) -> MovementWindowPayload:
    try:
        return load_local_movement_window(name, snapshot_root=snapshot_root)
    except FileNotFoundError:
        snapshot_index = _snapshot_index_payload_or_not_found(snapshot_root=snapshot_root)
        if isinstance(snapshot_index, NotFoundBody):
            raise FileNotFoundError("No published snapshots available")

        histories = _load_all_member_histories(snapshot_root=snapshot_root)
        if not histories:
            raise FileNotFoundError("No member histories available")

        latest_snapshot = snapshot_index.snapshots[-1]
        if name == "latest":
            previous_snapshot = (
                snapshot_index.snapshots[-2]
                if len(snapshot_index.snapshots) > 1
                else None
            )
            return build_latest_movement_window(
                histories,
                latest_snapshot_id=snapshot_index.latest_snapshot_id,
                latest_snapshot_date=latest_snapshot.snapshot_date,
                previous_snapshot_id=(
                    previous_snapshot.snapshot_id if previous_snapshot is not None else None
                ),
                previous_snapshot_date=(
                    previous_snapshot.snapshot_date if previous_snapshot is not None else None
                ),
            )

        preset_set = build_snapshot_compare_presets(
            [(entry.snapshot_id, entry.snapshot_date) for entry in snapshot_index.snapshots]
        )
        preset = next(
            (candidate for candidate in preset_set.presets if candidate.preset_key == name),
            None,
        )
        if preset is None:
            raise FileNotFoundError(f"Unknown movement window: {name}")
        return build_movement_window(
            histories,
            window_key=preset.preset_key,
            latest_snapshot_id=preset.end_snapshot_id,
            latest_snapshot_date=preset.end_snapshot_date,
            previous_snapshot_id=preset.start_snapshot_id,
            previous_snapshot_date=preset.start_snapshot_date,
            has_full_window=preset.has_full_window,
        )


def _build_member_page_payload(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    top_cards_limit: int = 3,
    recent_cards_limit: int = 5,
) -> MemberPagePayload:
    try:
        payload = load_local_member_page(slug, snapshot_root=snapshot_root)
    except FileNotFoundError:
        profile = load_local_member_profile(slug, snapshot_root=snapshot_root)
        candidate_ids = profile.top_evidence_card_ids + [
            fire.evidence_card_id
            for fire in profile.recent_rule_fires
            if fire.evidence_card_id
        ]
        evidence_cards = _load_evidence_cards(candidate_ids, snapshot_root=snapshot_root)
        return build_member_page_payload(
            profile,
            {card.evidence_card_id: card for card in evidence_cards},
            top_cards_limit=top_cards_limit,
            recent_cards_limit=recent_cards_limit,
        )

    return payload.model_copy(
        update={
            "top_evidence_cards": payload.top_evidence_cards[:top_cards_limit],
            "recent_evidence_cards": payload.recent_evidence_cards[:recent_cards_limit],
        }
    )


def get_member_page(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    top_cards_limit: int = 3,
    recent_cards_limit: int = 5,
) -> MemberPageResponse | NotFoundBody:
    try:
        payload = _build_member_page_payload(
            slug,
            snapshot_root=snapshot_root,
            top_cards_limit=top_cards_limit,
            recent_cards_limit=recent_cards_limit,
        )
    except FileNotFoundError:
        return not_found("member", slug)

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta

    return wrap_member_page(payload, meta)


def get_member_history_page(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    top_cards_limit: int = 3,
    recent_cards_limit: int = 5,
    history_cards_limit: int = 25,
) -> MemberHistoryPageResponse | NotFoundBody:
    try:
        payload = load_local_member_history_page(
            slug,
            snapshot_root=snapshot_root,
        )
    except FileNotFoundError:
        member_page = get_member_page(
            slug,
            snapshot_root=snapshot_root,
            top_cards_limit=top_cards_limit,
            recent_cards_limit=recent_cards_limit,
        )
        if isinstance(member_page, NotFoundBody):
            return member_page

        history = get_member_history(slug, snapshot_root=snapshot_root)
        if isinstance(history, NotFoundBody):
            return history

        snapshot_index_payload = _snapshot_index_payload_or_not_found(snapshot_root=snapshot_root)
        if isinstance(snapshot_index_payload, NotFoundBody):
            return snapshot_index_payload

        try:
            recent_change = _load_member_change_summary_payload(
                slug,
                snapshot_root=snapshot_root,
            )
            chart = _load_member_history_chart_payload(
                slug,
                snapshot_root=snapshot_root,
            )
            trend_summary = _load_member_trend_summary_payload(
                slug,
                snapshot_root=snapshot_root,
            )
        except FileNotFoundError:
            return not_found("member_history", slug)

        default_preset = next(
            (
                preset
                for preset in chart.compare_presets
                if preset.preset_key == chart.default_preset_key
            ),
            None,
        )
        if default_preset is None:
            return not_found("member_window_compare", slug)
        default_window_compare = _load_member_preset_compare_payload(
            slug,
            default_preset.preset_key,
            snapshot_root=snapshot_root,
        )
        if isinstance(default_window_compare, NotFoundBody):
            return (
                not_found("member_window_compare", slug)
                if default_window_compare.resource_type == "member_preset_compare"
                else default_window_compare
            )

        history_card_ids: list[str] = []
        seen_ids: set[str] = set()
        for event in history.data.events:
            card_id = event.evidence_card_id
            if not card_id or card_id in seen_ids:
                continue
            seen_ids.add(card_id)
            history_card_ids.append(card_id)
            if len(history_card_ids) >= history_cards_limit:
                break

        payload = MemberHistoryPagePayload(
            member_page=member_page.data,
            history=history.data,
            recent_change=recent_change,
            chart=chart,
            default_window_compare=default_window_compare,
            trend_summary=trend_summary,
            history_evidence_cards=_load_evidence_cards(
                history_card_ids,
                snapshot_root=snapshot_root,
            ),
            snapshot_index=snapshot_index_payload,
        )

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_member_history_page(payload, meta)


def get_member_change_summary(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberChangeSummaryResponse | NotFoundBody:
    try:
        payload = _load_member_change_summary_payload(slug, snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("member_history", slug)

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_member_change_summary(payload, meta)


def get_member_window_compare(
    slug: str,
    start_snapshot_id: str,
    end_snapshot_id: str,
    *,
    snapshot_root: Path | None = None,
    evidence_cards_limit: int = 10,
) -> MemberWindowCompareResponse | NotFoundBody:
    payload_or_error = _build_member_window_compare_payload(
        slug,
        start_snapshot_id,
        end_snapshot_id,
        snapshot_root=snapshot_root,
        evidence_cards_limit=evidence_cards_limit,
    )
    if isinstance(payload_or_error, NotFoundBody):
        return payload_or_error

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta

    return wrap_member_window_compare(payload_or_error, meta)


def get_member_preset_compare(
    slug: str,
    preset_key: str,
    *,
    snapshot_root: Path | None = None,
    evidence_cards_limit: int = 10,
) -> MemberWindowCompareResponse | NotFoundBody:
    payload_or_error = _load_member_preset_compare_payload(
        slug,
        preset_key,
        snapshot_root=snapshot_root,
        evidence_cards_limit=evidence_cards_limit,
    )
    if isinstance(payload_or_error, NotFoundBody):
        return payload_or_error

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta

    return wrap_member_window_compare(payload_or_error, meta)


def _build_member_window_compare_payload(
    slug: str,
    start_snapshot_id: str,
    end_snapshot_id: str,
    *,
    snapshot_root: Path | None = None,
    evidence_cards_limit: int = 10,
) -> MemberWindowComparePayload | NotFoundBody:
    snapshot_index = _snapshot_index_payload_or_not_found(snapshot_root=snapshot_root)
    if isinstance(snapshot_index, NotFoundBody):
        return snapshot_index

    start_entry = _snapshot_entry_by_id(snapshot_index, start_snapshot_id)
    if start_entry is None:
        return not_found("snapshot", start_snapshot_id)
    end_entry = _snapshot_entry_by_id(snapshot_index, end_snapshot_id)
    if end_entry is None:
        return not_found("snapshot", end_snapshot_id)
    if start_entry.snapshot_date > end_entry.snapshot_date:
        return not_found(
            "member_window_compare",
            f"{slug}:{start_snapshot_id}..{end_snapshot_id}",
        )

    try:
        history = load_local_member_history(slug, snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("member_history", slug)

    summary = build_member_window_change_summary(
        history,
        start_snapshot_date=start_entry.snapshot_date,
        end_snapshot_date=end_entry.snapshot_date,
    )
    if summary is None:
        return not_found(
            "member_window_compare",
            f"{slug}:{start_snapshot_id}..{end_snapshot_id}",
        )

    return MemberWindowComparePayload(
        bioguide_id=history.bioguide_id,
        name=history.name,
        slug=history.slug,
        state=history.state,
        district=history.district,
        chamber=history.chamber,
        party=history.party,
        start_snapshot_id=start_snapshot_id,
        start_snapshot_date=start_entry.snapshot_date,
        end_snapshot_id=end_snapshot_id,
        end_snapshot_date=end_entry.snapshot_date,
        summary=summary,
        evidence_cards=_load_evidence_cards(
            summary.top_evidence_card_ids[:evidence_cards_limit],
            snapshot_root=snapshot_root,
        ),
    )


def get_member_history_chart(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberHistoryChartResponse | NotFoundBody:
    try:
        payload = _load_member_history_chart_payload(slug, snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("member_history", slug)

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_member_history_chart(payload, meta)


def get_member_trend_summary(
    slug: str,
    *,
    snapshot_root: Path | None = None,
) -> MemberTrendSummaryResponse | NotFoundBody:
    try:
        payload = _load_member_trend_summary_payload(slug, snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("member_history", slug)

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_member_trend_summary(payload, meta)


def _score_map(profile: MemberPagePayload) -> dict[str, tuple[float, int]]:
    return {
        score.dimension: (score.current_score, score.rule_fire_count)
        for score in profile.profile.scores
    }


def _committee_names(profile: MemberPagePayload) -> set[str]:
    return {committee.committee_name for committee in profile.profile.committees}


def _build_member_compare_payload(
    left_slug: str,
    right_slug: str,
    *,
    snapshot_root: Path | None = None,
    top_cards_limit: int = 3,
    recent_cards_limit: int = 5,
) -> MemberComparePayload:
    left = _build_member_page_payload(
        left_slug,
        snapshot_root=snapshot_root,
        top_cards_limit=top_cards_limit,
        recent_cards_limit=recent_cards_limit,
    )
    right = _build_member_page_payload(
        right_slug,
        snapshot_root=snapshot_root,
        top_cards_limit=top_cards_limit,
        recent_cards_limit=recent_cards_limit,
    )

    left_scores = _score_map(left)
    right_scores = _score_map(right)
    dimensions = sorted(set(left_scores) | set(right_scores))
    score_comparisons = []
    for dimension in dimensions:
        left_score = left_scores.get(dimension)
        right_score = right_scores.get(dimension)
        score_comparisons.append(
            MemberCompareScoreRow(
                dimension=dimension,
                left_current_score=left_score[0] if left_score is not None else None,
                right_current_score=right_score[0] if right_score is not None else None,
                left_rule_fire_count=left_score[1] if left_score is not None else 0,
                right_rule_fire_count=right_score[1] if right_score is not None else 0,
                score_gap=(
                    (left_score[0] - right_score[0])
                    if left_score is not None and right_score is not None
                    else None
                ),
            )
        )

    left_committees = _committee_names(left)
    right_committees = _committee_names(right)

    return MemberComparePayload(
        left=left,
        right=right,
        score_comparisons=score_comparisons,
        shared_committees=sorted(left_committees & right_committees),
        left_only_committees=sorted(left_committees - right_committees),
        right_only_committees=sorted(right_committees - left_committees),
        same_chamber=left.profile.chamber == right.profile.chamber,
        same_state=left.profile.state == right.profile.state,
    )


def get_member_compare(
    left_slug: str,
    right_slug: str,
    *,
    snapshot_root: Path | None = None,
    top_cards_limit: int = 3,
    recent_cards_limit: int = 5,
) -> MemberCompareResponse | NotFoundBody:
    for slug in (left_slug, right_slug):
        try:
            load_local_member_profile(slug, snapshot_root=snapshot_root)
        except FileNotFoundError:
            return not_found("member", slug)

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta

    return wrap_member_compare(
        _build_member_compare_payload(
            left_slug,
            right_slug,
            snapshot_root=snapshot_root,
            top_cards_limit=top_cards_limit,
            recent_cards_limit=recent_cards_limit,
        ),
        meta,
    )


def get_evidence(
    evidence_card_id: str,
    *,
    snapshot_root: Path | None = None,
) -> EvidenceResponse | NotFoundBody:
    try:
        payload = load_local_evidence_card(
            evidence_card_id,
            snapshot_root=snapshot_root,
        )
    except FileNotFoundError:
        return not_found("evidence", evidence_card_id)
    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_evidence(payload, meta)


def get_current_member_lookup(
    *,
    snapshot_root: Path | None = None,
) -> CurrentMemberLookupResponse | NotFoundBody:
    try:
        payload = load_local_current_member_lookup(snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("current_member_lookup", "current")
    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_current_member_lookup(payload, meta)


def search_current_member_lookup(
    query: str,
    *,
    limit: int = 8,
    snapshot_root: Path | None = None,
) -> CurrentMemberLookupResponse | NotFoundBody:
    try:
        payload = load_local_current_member_lookup(snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("current_member_lookup", "current")
    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_current_member_lookup(
        _search_current_member_lookup(payload, query, limit=limit),
        meta,
    )


def get_search_session(
    query: str,
    *,
    snapshot_root: Path | None = None,
    limit: int = 8,
    preview_limit: int = 3,
    top_cards_limit: int = 1,
    recent_cards_limit: int = 2,
) -> SearchSessionResponse | NotFoundBody:
    try:
        lookup = load_local_current_member_lookup(snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("search_session", "current")

    searched = _search_current_member_lookup(lookup, query, limit=limit)
    snapshot = get_snapshot_summary(snapshot_root=snapshot_root)
    if isinstance(snapshot, NotFoundBody):
        return snapshot

    results: list[SearchSessionResult] = []
    for index, entry in enumerate(searched.members):
        preview = None
        if index < preview_limit:
            try:
                preview = _build_member_page_payload(
                    entry.slug,
                    snapshot_root=snapshot_root,
                    top_cards_limit=top_cards_limit,
                    recent_cards_limit=recent_cards_limit,
                )
            except FileNotFoundError:
                preview = None
        results.append(
            SearchSessionResult(
                lookup_entry=entry,
                member_page_preview=preview,
            )
        )

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta

    return wrap_search_session(
        SearchSessionPayload(
            query=query,
            total_matches=len(searched.members),
            results=results,
            snapshot=snapshot.data,
        ),
        meta,
    )


def get_last_updated(
    *,
    snapshot_root: Path | None = None,
) -> LastUpdatedResponse | NotFoundBody:
    try:
        snapshot_date, published_at = load_latest_local_snapshot_metadata(
            snapshot_root=snapshot_root
        )
    except FileNotFoundError:
        return not_found("snapshot", "latest")
    return wrap_last_updated(snapshot_date, published_at)


def get_movement_feed(
    *,
    snapshot_root: Path | None = None,
    top_changes_limit: int | None = None,
    recent_events_limit: int | None = None,
) -> MovementFeedResponse | NotFoundBody:
    try:
        movement_window = load_local_movement_window(snapshot_root=snapshot_root)
    except FileNotFoundError:
        try:
            homepage_payload = load_local_homepage_feed(snapshot_root=snapshot_root)
        except FileNotFoundError:
            return not_found("movement_feed", "current")
        payload = MovementFeedPayload(
            snapshot_date=homepage_payload.snapshot_date,
            top_changes=homepage_payload.top_changes,
            recent_events=homepage_payload.recent_events,
            recent_evidence_card_ids=homepage_payload.recent_evidence_card_ids,
        )
    else:
        payload = MovementFeedPayload(
            snapshot_date=movement_window.latest_snapshot_date,
            top_changes=movement_window.top_changes,
            recent_events=movement_window.recent_events,
            recent_evidence_card_ids=movement_window.recent_evidence_card_ids,
        )

    sliced_top_changes = (
        payload.top_changes[:top_changes_limit]
        if top_changes_limit is not None
        else payload.top_changes
    )
    sliced_recent_events = (
        payload.recent_events[:recent_events_limit]
        if recent_events_limit is not None
        else payload.recent_events
    )

    recent_evidence_card_ids: list[str] = []
    seen_ids: set[str] = set()
    for event in sliced_recent_events:
        if event.evidence_card_id and event.evidence_card_id not in seen_ids:
            seen_ids.add(event.evidence_card_id)
            recent_evidence_card_ids.append(event.evidence_card_id)

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta

    return wrap_movement_feed(
        MovementFeedPayload(
            snapshot_date=payload.snapshot_date,
            top_changes=sliced_top_changes,
            recent_events=sliced_recent_events,
            recent_evidence_card_ids=recent_evidence_card_ids,
        ),
        meta,
    )


def get_movement_window(
    *,
    name: str = "latest",
    snapshot_root: Path | None = None,
) -> MovementWindowResponse | NotFoundBody:
    try:
        payload = _load_movement_window_payload(name=name, snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("movement_window", name)

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_movement_window(payload, meta)


def get_snapshot_summary(
    *,
    snapshot_root: Path | None = None,
) -> SnapshotSummaryResponse | NotFoundBody:
    try:
        manifest = load_latest_local_manifest(snapshot_root=snapshot_root)
    except FileNotFoundError:
        return not_found("snapshot", "latest")

    root = snapshot_root if snapshot_root is not None else local_publish_root()
    homepage_feed_count = 1 if (root / "homepage" / "feed.json").is_file() else 0

    artifact_counts = ArtifactCounts(
        members=sum(1 for entry in manifest.entries if entry.path.startswith("members/")),
        evidence=sum(1 for entry in manifest.entries if entry.path.startswith("evidence/")),
        zip_feeds=sum(1 for entry in manifest.entries if entry.path.startswith("zip/")),
        homepage_feeds=homepage_feed_count,
        current_member_lookups=sum(
            1 for entry in manifest.entries if entry.path == "identity/current-member-lookup.json"
        ),
    )
    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta

    return wrap_snapshot_summary(
        SnapshotSummaryPayload(
            snapshot_id=manifest.snapshot_id,
            snapshot_date=meta.snapshot_date,
            published_at=meta.published_at,
            root_sha256=manifest.root_sha256,
            total_files=manifest.total_files,
            total_bytes=manifest.total_bytes,
            artifact_counts=artifact_counts,
        ),
        meta,
    )


def get_snapshot_index(
    *,
    snapshot_root: Path | None = None,
) -> SnapshotIndexResponse | NotFoundBody:
    payload = _snapshot_index_payload_or_not_found(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return payload

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_snapshot_index(payload, meta)


def get_snapshot_compare(
    start_snapshot_id: str,
    end_snapshot_id: str,
    *,
    snapshot_root: Path | None = None,
    top_n: int = 10,
    recent_n: int = 20,
    featured_n: int = 5,
) -> SnapshotCompareResponse | NotFoundBody:
    snapshot_index_payload = _snapshot_index_payload_or_not_found(snapshot_root=snapshot_root)
    if isinstance(snapshot_index_payload, NotFoundBody):
        return snapshot_index_payload

    start_entry = _snapshot_entry_by_id(snapshot_index_payload, start_snapshot_id)
    if start_entry is None:
        return not_found("snapshot", start_snapshot_id)
    end_entry = _snapshot_entry_by_id(snapshot_index_payload, end_snapshot_id)
    if end_entry is None:
        return not_found("snapshot", end_snapshot_id)
    if start_entry.snapshot_date >= end_entry.snapshot_date:
        return not_found("snapshot_compare", f"{start_snapshot_id}..{end_snapshot_id}")

    histories = _load_all_member_histories(snapshot_root=snapshot_root)
    payload = build_snapshot_compare_payload(
        histories,
        start_snapshot_id=start_entry.snapshot_id,
        start_snapshot_date=start_entry.snapshot_date,
        end_snapshot_id=end_entry.snapshot_id,
        end_snapshot_date=end_entry.snapshot_date,
        top_n=top_n,
        recent_n=recent_n,
        featured_n=featured_n,
    )
    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_snapshot_compare(payload, meta)


def get_snapshot_preset_compare(
    preset_key: str,
    *,
    snapshot_root: Path | None = None,
) -> SnapshotCompareResponse | NotFoundBody:
    if preset_key not in _SNAPSHOT_PRESET_KEYS:
        return not_found("snapshot_preset_compare", preset_key)

    try:
        payload = load_local_snapshot_preset_compare(
            preset_key,
            snapshot_root=snapshot_root,
        )
    except (FileNotFoundError, ValueError):
        snapshot_index_payload = _snapshot_index_payload_or_not_found(
            snapshot_root=snapshot_root
        )
        if isinstance(snapshot_index_payload, NotFoundBody):
            return snapshot_index_payload
        preset_set = build_snapshot_compare_presets(
            [
                (entry.snapshot_id, entry.snapshot_date)
                for entry in snapshot_index_payload.snapshots
            ]
        )
        preset = next(
            (candidate for candidate in preset_set.presets if candidate.preset_key == preset_key),
            None,
        )
        if preset is None:
            return not_found("snapshot_preset_compare", preset_key)
        histories = _load_all_member_histories(snapshot_root=snapshot_root)
        payload = build_snapshot_compare_payload(
            histories,
            start_snapshot_id=preset.start_snapshot_id,
            start_snapshot_date=preset.start_snapshot_date,
            end_snapshot_id=preset.end_snapshot_id,
            end_snapshot_date=preset.end_snapshot_date,
        )

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_snapshot_compare(payload, meta)


def get_history_preset_range(
    preset_key: str,
    *,
    snapshot_root: Path | None = None,
) -> HistoryPresetRangeResponse | NotFoundBody:
    if preset_key not in _SNAPSHOT_PRESET_KEYS:
        return not_found("history_preset_range", preset_key)

    try:
        payload = load_local_history_preset_range(
            preset_key,
            snapshot_root=snapshot_root,
        )
    except (FileNotFoundError, ValueError):
        preset_compare = get_snapshot_preset_compare(
            preset_key,
            snapshot_root=snapshot_root,
        )
        if isinstance(preset_compare, NotFoundBody):
            return (
                not_found("history_preset_range", preset_key)
                if preset_compare.resource_type == "snapshot_preset_compare"
                else preset_compare
            )
        movement_window = get_movement_window(
            name=preset_key,
            snapshot_root=snapshot_root,
        )
        if isinstance(movement_window, NotFoundBody):
            return (
                not_found("history_preset_range", preset_key)
                if movement_window.resource_type == "movement_window"
                else movement_window
            )
        snapshot_index_payload = _snapshot_index_payload_or_not_found(snapshot_root=snapshot_root)
        if isinstance(snapshot_index_payload, NotFoundBody):
            return snapshot_index_payload
        preset_set = build_snapshot_compare_presets(
            [
                (entry.snapshot_id, entry.snapshot_date)
                for entry in snapshot_index_payload.snapshots
            ]
        )
        preset = next(
            (candidate for candidate in preset_set.presets if candidate.preset_key == preset_key),
            None,
        )
        if preset is None:
            return not_found("history_preset_range", preset_key)
        payload = HistoryPresetRangePayload(
            preset=preset,
            movement_window=movement_window.data,
            snapshot_compare=preset_compare.data,
        )

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta
    return wrap_history_preset_range(payload, meta)


def get_zip_entry(
    zip_code: str,
    *,
    snapshot_root: Path | None = None,
) -> ZipEntryResponse | NotFoundBody:
    snapshot = get_snapshot_summary(snapshot_root=snapshot_root)
    if isinstance(snapshot, NotFoundBody):
        return snapshot
    try:
        artifact_payload = load_local_zip_entry(zip_code, snapshot_root=snapshot_root)
    except FileNotFoundError:
        artifact_payload = None
    else:
        assert artifact_payload is not None
        if not _snapshot_summaries_match(artifact_payload.snapshot, snapshot.data):
            artifact_payload = None

    if artifact_payload is None:
        try:
            zip_feed = load_local_zip_feed(zip_code, snapshot_root=snapshot_root)
        except FileNotFoundError:
            return not_found("zip", zip_code)

        lookup_by_bioguide_id = {
            entry.bioguide_id: entry
            for entry in _load_current_lookup_entries(snapshot_root=snapshot_root)
        }
        payload = ZipEntryPayload(
            zip_feed=zip_feed,
            member_lookup_entries=[
                lookup_by_bioguide_id[member.bioguide_id]
                for member in zip_feed.members
                if member.bioguide_id in lookup_by_bioguide_id
            ],
            snapshot=snapshot.data,
        )
    else:
        payload = artifact_payload

    meta = _meta_or_not_found(snapshot_root=snapshot_root)
    if isinstance(meta, NotFoundBody):
        return meta

    return wrap_zip_entry(payload, meta)
