"""Aggregate many per-snapshot publish roots into one history-serving root."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from src.api.contracts import (
    HistoryBootstrapPayload,
    HistoryPresetRangePayload,
    MemberHistoryPagePayload,
    MemberPagePayload,
    MemberWindowComparePayload,
    SnapshotIndexEntry,
    SnapshotIndexPayload,
)
from src.homepage.contracts import MovementWindowPayload
from src.export.contracts import (
    EvidenceCardPayload,
    HistoricalCommitteeMembership,
    MemberChangeSummaryPayload,
    MemberHistoryChartPayload,
    MemberHistoryEvent,
    MemberHistoryPayload,
    MemberHistorySnapshot,
    MemberTrendSummaryPayload,
)
from src.export.filesystem import write_planned_files
from src.export.local_store import (
    load_evidence_card,
    load_latest_manifest,
    load_member_history,
    load_member_profile,
    manifest_published_at,
    manifest_snapshot_date,
)
from src.export.writer import (
    PlannedFile,
    evidence_path,
    history_bootstrap_path,
    history_preset_range_path,
    homepage_bootstrap_path,
    member_change_summary_path,
    member_history_chart_path,
    member_history_page_path,
    manifest_path,
    member_history_path,
    member_preset_compare_path,
    member_trend_summary_path,
    movement_window_path,
    serialize_payload,
    snapshot_preset_compare_path,
    snapshot_index_path,
)
from src.query.history_products import (
    build_movement_window,
    build_latest_movement_window,
    build_member_change_summary,
    build_member_history_chart,
    build_member_window_change_summary,
    build_member_trend_summary,
    build_snapshot_compare_payload,
    build_snapshot_compare_presets,
)


@dataclass(frozen=True)
class HistoryAggregateResult:
    latest_snapshot_id: str
    snapshot_index: SnapshotIndexPayload
    member_history_count: int
    member_change_summary_count: int
    member_history_chart_count: int
    member_trend_summary_count: int
    target_root: Path


def _snapshot_index_entry(root: Path) -> SnapshotIndexEntry:
    manifest = load_latest_manifest(root)
    return SnapshotIndexEntry(
        snapshot_id=manifest.snapshot_id,
        snapshot_date=manifest_snapshot_date(manifest),
        published_at=manifest_published_at(manifest),
        root_sha256=manifest.root_sha256,
        total_files=manifest.total_files,
        total_bytes=manifest.total_bytes,
    )


def build_snapshot_index(source_roots: list[Path]) -> SnapshotIndexPayload:
    if not source_roots:
        raise ValueError("source_roots must not be empty")

    entries = sorted(
        (_snapshot_index_entry(root) for root in source_roots),
        key=lambda entry: (entry.snapshot_date, entry.snapshot_id),
    )
    return SnapshotIndexPayload(
        latest_snapshot_id=entries[-1].snapshot_id,
        snapshots=entries,
    )


def _latest_snapshot_date(payload: MemberHistoryPayload) -> dt.date:
    if payload.snapshots:
        return max(snapshot.snapshot_date for snapshot in payload.snapshots)
    return dt.date.min


def merge_member_history_payloads(histories: list[MemberHistoryPayload]) -> MemberHistoryPayload:
    if not histories:
        raise ValueError("histories must not be empty")

    latest_payload = max(histories, key=_latest_snapshot_date)

    snapshots_by_date: dict[dt.date, MemberHistorySnapshot] = {}
    for payload in histories:
        for snapshot in payload.snapshots:
            snapshots_by_date[snapshot.snapshot_date] = snapshot

    event_map: dict[
        tuple[str, str, str, str, dt.date | None, dt.datetime | None, str, float],
        MemberHistoryEvent,
    ] = {}
    for payload in histories:
        for event in payload.events:
            event_key = (
                event.rule_id,
                event.dimension,
                event.severity,
                event.evidence_card_id or "",
                event.snapshot_date,
                event.fired_at,
                event.short_explanation,
                event.score_delta,
            )
            event_map[event_key] = event

    committee_map: dict[
        tuple[str, str | None, dt.date | None, dt.date | None, bool, str, str],
        HistoricalCommitteeMembership,
    ] = {}
    for payload in histories:
        for membership in payload.committee_history:
            committee_key = (
                membership.committee_name,
                membership.role,
                membership.start_date,
                membership.end_date,
                membership.is_current,
                membership.chamber,
                membership.committee_type,
            )
            committee_map[committee_key] = membership

    return MemberHistoryPayload(
        bioguide_id=latest_payload.bioguide_id,
        name=latest_payload.name,
        slug=latest_payload.slug,
        state=latest_payload.state,
        district=latest_payload.district,
        chamber=latest_payload.chamber,
        party=latest_payload.party,
        snapshots=[
            snapshots_by_date[snapshot_date]
            for snapshot_date in sorted(snapshots_by_date)
        ],
        events=sorted(
            event_map.values(),
            key=lambda event: (
                event.fired_at is None,
                event.fired_at or dt.datetime.min.replace(tzinfo=dt.UTC),
                event.snapshot_date or dt.date.min,
                event.evidence_card_id or "",
            ),
            reverse=True,
        ),
        committee_history=sorted(
            committee_map.values(),
            key=lambda membership: (
                membership.start_date or dt.date.min,
                membership.committee_name,
                membership.role or "",
            ),
        ),
    )


def build_aggregated_member_histories(source_roots: list[Path]) -> list[MemberHistoryPayload]:
    if not source_roots:
        raise ValueError("source_roots must not be empty")

    histories_by_slug: dict[str, list[MemberHistoryPayload]] = {}
    for root in source_roots:
        history_dir = root / "history" / "members"
        if not history_dir.exists():
            continue
        for file in sorted(history_dir.glob("*.json")):
            slug = file.stem
            histories_by_slug.setdefault(slug, []).append(load_member_history(root, slug))

    return [
        merge_member_history_payloads(histories)
        for _slug, histories in sorted(histories_by_slug.items())
    ]


def _latest_root(source_roots: list[Path]) -> Path:
    return max(
        source_roots,
        key=lambda root: (
            _snapshot_index_entry(root).snapshot_date,
            _snapshot_index_entry(root).snapshot_id,
        ),
    )


def _copy_latest_artifacts(source_root: Path) -> list[PlannedFile]:
    planned: list[PlannedFile] = []
    for relative_dir in ("members", "member-pages", "evidence", "zip", "zip-entry", "identity"):
        directory = source_root / relative_dir
        if not directory.exists():
            continue
        for file in sorted(path for path in directory.rglob("*") if path.is_file()):
            planned.append(
                PlannedFile.from_bytes(
                    file.relative_to(source_root).as_posix(),
                    file.read_bytes(),
                )
            )

    homepage_file = source_root / "homepage" / "feed.json"
    if homepage_file.exists():
        planned.append(
            PlannedFile.from_bytes(
                homepage_file.relative_to(source_root).as_posix(),
                homepage_file.read_bytes(),
            )
        )
    homepage_bootstrap_file = source_root / homepage_bootstrap_path()
    if homepage_bootstrap_file.exists():
        planned.append(
            PlannedFile.from_bytes(
                homepage_bootstrap_file.relative_to(source_root).as_posix(),
                homepage_bootstrap_file.read_bytes(),
            )
        )
    return planned


def _history_evidence_card_ids(
    member_histories: list[MemberHistoryPayload],
) -> list[str]:
    seen_ids: set[str] = set()
    evidence_card_ids: list[str] = []
    for history in member_histories:
        for event in history.events:
            card_id = event.evidence_card_id
            if not card_id or card_id in seen_ids:
                continue
            seen_ids.add(card_id)
            evidence_card_ids.append(card_id)
    return evidence_card_ids


def _copy_historical_evidence_artifacts(
    source_roots: list[Path],
    evidence_card_ids: list[str],
    *,
    existing_paths: set[str],
) -> list[PlannedFile]:
    ordered_roots = sorted(
        source_roots,
        key=lambda root: (
            _snapshot_index_entry(root).snapshot_date,
            _snapshot_index_entry(root).snapshot_id,
        ),
        reverse=True,
    )
    planned: list[PlannedFile] = []
    for evidence_card_id in evidence_card_ids:
        relative_path = evidence_path(evidence_card_id)
        if relative_path in existing_paths:
            continue
        for root in ordered_roots:
            file = root / relative_path
            if not file.exists():
                continue
            planned.append(
                PlannedFile.from_bytes(
                    relative_path,
                    file.read_bytes(),
                )
            )
            existing_paths.add(relative_path)
            break
    return planned


def _load_historical_evidence_cards(
    source_roots: list[Path],
    evidence_card_ids: list[str],
) -> list[EvidenceCardPayload]:
    ordered_roots = sorted(
        source_roots,
        key=lambda root: (
            _snapshot_index_entry(root).snapshot_date,
            _snapshot_index_entry(root).snapshot_id,
        ),
        reverse=True,
    )
    cards: list[EvidenceCardPayload] = []
    seen_ids: set[str] = set()
    for evidence_card_id in evidence_card_ids:
        if not evidence_card_id or evidence_card_id in seen_ids:
            continue
        seen_ids.add(evidence_card_id)
        for root in ordered_roots:
            try:
                cards.append(load_evidence_card(root, evidence_card_id))
            except FileNotFoundError:
                continue
            break
    return cards


def _build_member_history_page_payload(
    *,
    history: MemberHistoryPayload,
    recent_change: MemberChangeSummaryPayload,
    chart: MemberHistoryChartPayload,
    trend_summary: MemberTrendSummaryPayload,
    default_window_compare: MemberWindowComparePayload,
    snapshot_index: SnapshotIndexPayload,
    latest_root: Path,
    source_roots: list[Path],
    top_cards_limit: int = 3,
    recent_cards_limit: int = 5,
    history_cards_limit: int = 25,
) -> MemberHistoryPagePayload:
    profile = load_member_profile(latest_root, history.slug)
    top_card_ids = profile.top_evidence_card_ids[:top_cards_limit]
    recent_card_ids = [
        fire.evidence_card_id
        for fire in profile.recent_rule_fires[:recent_cards_limit]
        if fire.evidence_card_id
    ]

    history_card_ids: list[str] = []
    seen_history_ids: set[str] = set()
    for event in history.events:
        card_id = event.evidence_card_id
        if not card_id or card_id in seen_history_ids:
            continue
        seen_history_ids.add(card_id)
        history_card_ids.append(card_id)
        if len(history_card_ids) >= history_cards_limit:
            break

    return MemberHistoryPagePayload(
        member_page=MemberPagePayload(
            profile=profile,
            top_evidence_cards=_load_historical_evidence_cards(source_roots, top_card_ids),
            recent_evidence_cards=_load_historical_evidence_cards(
                source_roots,
                recent_card_ids,
            ),
        ),
        history=history,
        recent_change=recent_change,
        chart=chart,
        default_window_compare=default_window_compare,
        trend_summary=trend_summary,
        history_evidence_cards=_load_historical_evidence_cards(
            source_roots,
            history_card_ids,
        ),
        snapshot_index=snapshot_index,
    )


def write_history_aggregate(
    source_roots: list[Path],
    target_root: Path,
) -> HistoryAggregateResult:
    if not source_roots:
        raise ValueError("source_roots must not be empty")

    snapshot_index = build_snapshot_index(source_roots)
    member_histories = build_aggregated_member_histories(source_roots)
    member_change_summaries = [
        build_member_change_summary(history)
        for history in member_histories
    ]
    snapshot_ids_by_date = {
        entry.snapshot_date: entry.snapshot_id for entry in snapshot_index.snapshots
    }
    member_history_charts = [
        build_member_history_chart(
            history,
            snapshot_ids_by_date=snapshot_ids_by_date,
        )
        for history in member_histories
    ]
    member_preset_compares: list[tuple[str, MemberWindowComparePayload]] = []
    for history, chart in zip(member_histories, member_history_charts, strict=True):
        for preset in chart.compare_presets:
            summary = build_member_window_change_summary(
                history,
                start_snapshot_date=preset.start_snapshot_date,
                end_snapshot_date=preset.end_snapshot_date,
            )
            if summary is None:
                continue
            member_preset_compares.append(
                (
                    preset.preset_key,
                    MemberWindowComparePayload(
                        bioguide_id=history.bioguide_id,
                        name=history.name,
                        slug=history.slug,
                        state=history.state,
                        district=history.district,
                        chamber=history.chamber,
                        party=history.party,
                        start_snapshot_id=preset.start_snapshot_id,
                        start_snapshot_date=preset.start_snapshot_date,
                        end_snapshot_id=preset.end_snapshot_id,
                        end_snapshot_date=preset.end_snapshot_date,
                        summary=summary,
                        evidence_cards=_load_historical_evidence_cards(
                            source_roots,
                            summary.top_evidence_card_ids,
                        ),
                    ),
                )
            )
    member_trend_summaries = [
        build_member_trend_summary(history)
        for history in member_histories
    ]
    previous_snapshot = (
        snapshot_index.snapshots[-2]
        if len(snapshot_index.snapshots) > 1
        else None
    )
    movement_window = build_latest_movement_window(
        member_histories,
        latest_snapshot_id=snapshot_index.latest_snapshot_id,
        latest_snapshot_date=snapshot_index.snapshots[-1].snapshot_date,
        previous_snapshot_id=previous_snapshot.snapshot_id if previous_snapshot is not None else None,
        previous_snapshot_date=(
            previous_snapshot.snapshot_date if previous_snapshot is not None else None
        ),
    )
    compare_preset_set = build_snapshot_compare_presets(
        [(entry.snapshot_id, entry.snapshot_date) for entry in snapshot_index.snapshots]
    )
    latest_root = _latest_root(source_roots)
    summary_by_slug = {summary.slug: summary for summary in member_change_summaries}
    featured_member_changes = [
        summary_by_slug[change.slug]
        for change in movement_window.top_changes
        if change.slug in summary_by_slug
    ][:5]
    history_bootstrap = HistoryBootstrapPayload(
        snapshot_index=snapshot_index,
        movement_window=movement_window,
        default_compare_preset_key=compare_preset_set.default_preset_key,
        compare_presets=compare_preset_set.presets,
        featured_member_changes=featured_member_changes,
    )
    snapshot_preset_compares = [
        build_snapshot_compare_payload(
            member_histories,
            start_snapshot_id=preset.start_snapshot_id,
            start_snapshot_date=preset.start_snapshot_date,
            end_snapshot_id=preset.end_snapshot_id,
            end_snapshot_date=preset.end_snapshot_date,
        )
        for preset in compare_preset_set.presets
    ]
    movement_windows: dict[str, MovementWindowPayload] = {"latest": movement_window}
    for snapshot_preset in compare_preset_set.presets:
        if snapshot_preset.preset_key == "latest":
            continue
        movement_windows[snapshot_preset.preset_key] = build_movement_window(
            member_histories,
            window_key=snapshot_preset.preset_key,
            latest_snapshot_id=snapshot_preset.end_snapshot_id,
            latest_snapshot_date=snapshot_preset.end_snapshot_date,
            previous_snapshot_id=snapshot_preset.start_snapshot_id,
            previous_snapshot_date=snapshot_preset.start_snapshot_date,
            has_full_window=snapshot_preset.has_full_window,
        )

    planned = _copy_latest_artifacts(latest_root)
    planned_paths = {file.path for file in planned}
    default_compare_by_slug: dict[str, MemberWindowComparePayload] = {}
    for chart in member_history_charts:
        default_compare = next(
            (
                compare
                for preset_key, compare in member_preset_compares
                if compare.slug == chart.slug and preset_key == chart.default_preset_key
            ),
            None,
        )
        if default_compare is None:
            raise ValueError(
                f"Missing default member preset compare for {chart.slug}:{chart.default_preset_key}"
            )
        default_compare_by_slug[chart.slug] = default_compare

    for root in source_roots:
        manifest = load_latest_manifest(root)
        file = root / manifest_path(manifest.snapshot_id)
        planned.append(
            PlannedFile.from_bytes(
                manifest_path(manifest.snapshot_id),
                file.read_bytes(),
            )
        )
    for history in member_histories:
        planned.append(
            PlannedFile.from_bytes(
                member_history_path(history.slug),
                serialize_payload(history),
            )
        )
    for summary in member_change_summaries:
        planned.append(
            PlannedFile.from_bytes(
                member_change_summary_path(summary.slug),
                serialize_payload(summary),
            )
        )
    for chart in member_history_charts:
        planned.append(
            PlannedFile.from_bytes(
                member_history_chart_path(chart.slug),
                serialize_payload(chart),
            )
        )
    for history, recent_change, chart, trend_summary in zip(
        member_histories,
        member_change_summaries,
        member_history_charts,
        member_trend_summaries,
        strict=True,
    ):
        try:
            page_payload = _build_member_history_page_payload(
                history=history,
                recent_change=recent_change,
                chart=chart,
                trend_summary=trend_summary,
                default_window_compare=default_compare_by_slug[history.slug],
                snapshot_index=snapshot_index,
                latest_root=latest_root,
                source_roots=source_roots,
            )
        except FileNotFoundError:
            continue
        planned.append(
            PlannedFile.from_bytes(
                member_history_page_path(history.slug),
                serialize_payload(page_payload),
            )
        )
    for preset_key, compare in member_preset_compares:
        planned.append(
            PlannedFile.from_bytes(
                member_preset_compare_path(compare.slug, preset_key),
                serialize_payload(compare),
            )
        )
    for trend_summary in member_trend_summaries:
        planned.append(
            PlannedFile.from_bytes(
                member_trend_summary_path(trend_summary.slug),
                serialize_payload(trend_summary),
            )
        )
    planned.append(
        PlannedFile.from_bytes(
            snapshot_index_path(),
            serialize_payload(snapshot_index),
        )
    )
    for window_key, window in movement_windows.items():
        planned.append(
            PlannedFile.from_bytes(
                movement_window_path(window_key),
                serialize_payload(window),
            )
        )
    planned.append(
        PlannedFile.from_bytes(
            history_bootstrap_path(),
            serialize_payload(history_bootstrap),
        )
    )
    for snapshot_preset, snapshot_compare in zip(
        compare_preset_set.presets,
        snapshot_preset_compares,
        strict=True,
    ):
        planned.append(
            PlannedFile.from_bytes(
                history_preset_range_path(snapshot_preset.preset_key),
                serialize_payload(
                    HistoryPresetRangePayload(
                        preset=snapshot_preset,
                        movement_window=movement_windows[snapshot_preset.preset_key],
                        snapshot_compare=snapshot_compare,
                    )
                ),
            )
        )
        planned.append(
            PlannedFile.from_bytes(
                snapshot_preset_compare_path(snapshot_preset.preset_key),
                serialize_payload(snapshot_compare),
            )
        )
    planned.extend(
        _copy_historical_evidence_artifacts(
            source_roots,
            _history_evidence_card_ids(member_histories),
            existing_paths=planned_paths,
        )
    )

    write_planned_files(planned, target_root)
    return HistoryAggregateResult(
        latest_snapshot_id=snapshot_index.latest_snapshot_id,
        snapshot_index=snapshot_index,
        member_history_count=len(member_histories),
        member_change_summary_count=len(member_change_summaries),
        member_history_chart_count=len(member_history_charts),
        member_trend_summary_count=len(member_trend_summaries),
        target_root=target_root,
    )
