"""Local verification of a history aggregate publish root."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from src.api.contracts import (
    ArtifactCounts,
    HistoryBootstrapPayload,
    HistoryPresetRangePayload,
    HomepageBootstrapPayload,
    MemberHistoryPagePayload,
    MemberWindowComparePayload,
    MovementFeedPayload,
    SnapshotIndexPayload,
    SnapshotSummaryPayload,
    ZipEntryPayload,
)
from src.export.contracts import (
    EvidenceCardPayload,
    MemberChangeSummaryPayload,
    MemberHistoryChartPayload,
    MemberHistoryPayload,
    MemberTrendSummaryPayload,
)
from src.export.local_store import (
    list_artifact_paths,
    load_current_member_lookup,
    load_evidence_card,
    load_history_bootstrap,
    load_history_preset_range,
    load_manifest,
    load_homepage_bootstrap,
    load_homepage_feed,
    load_member_change_summary,
    load_member_history,
    load_member_history_chart,
    load_member_history_page,
    load_member_page,
    load_member_preset_compare,
    load_member_profile,
    load_member_trend_summary,
    load_movement_window,
    load_snapshot_index,
    load_snapshot_preset_compare,
    load_zip_entry,
    load_zip_feed,
    load_latest_manifest,
    manifest_published_at,
    manifest_snapshot_date,
)
from src.export.writer import (
    current_member_lookup_path,
    history_bootstrap_path,
    history_preset_range_path,
    homepage_bootstrap_path,
    member_change_summary_path,
    member_history_chart_path,
    member_history_page_path,
    member_page_payload_path,
    member_history_path,
    member_preset_compare_path,
    member_trend_summary_path,
    movement_window_path,
    snapshot_index_path,
    snapshot_preset_compare_path,
    zip_entry_path,
)
from src.export.writer import build_member_page_payload
from src.homepage.builders import build_featured_lookup_entries
from src.homepage.contracts import MovementWindowPayload, SnapshotComparePayload
from src.identity.current_member_lookup import CurrentMemberLookupEntry, build_current_member_lookup
from src.query.history_products import (
    build_movement_window,
    build_latest_movement_window,
    build_member_change_summary,
    build_member_history_chart,
    build_member_trend_summary,
    build_member_window_change_summary,
    build_snapshot_compare_payload,
    build_snapshot_compare_presets,
)
from src.runtime.history_verify_types import (
    HISTORY_VERIFY_STAGES,
    HistoryVerifyIssue,
    HistoryVerifyResult,
    HistoryVerifyStageResult,
)


def _issue(
    stage: str,
    message: str,
    *,
    path: str | None = None,
) -> HistoryVerifyIssue:
    return HistoryVerifyIssue(
        stage=stage,
        message=message,
        severity="error",
        path=path,
    )


def _unavailable_stage(stage: str, reason: str) -> HistoryVerifyStageResult:
    return HistoryVerifyStageResult(
        stage=stage,
        checked=0,
        issues=(
            _issue(stage, f"skipped: {reason}"),
        ),
    )


def _snapshot_entry_count(snapshot_index: SnapshotIndexPayload) -> int:
    return len(snapshot_index.snapshots)


def _verify_snapshot_index(
    root: Path,
) -> tuple[HistoryVerifyStageResult, SnapshotIndexPayload | None]:
    stage = "snapshot_index"
    try:
        payload = load_snapshot_index(root)
    except Exception as exc:
        return HistoryVerifyStageResult(
            stage=stage,
            checked=0,
            issues=(_issue(stage, f"failed to load snapshot index: {exc}", path=snapshot_index_path()),),
        ), None

    issues: list[HistoryVerifyIssue] = []
    if not payload.snapshots:
        issues.append(_issue(stage, "snapshot index is empty", path=snapshot_index_path()))
    else:
        if payload.latest_snapshot_id != payload.snapshots[-1].snapshot_id:
            issues.append(
                _issue(
                    stage,
                    "latest_snapshot_id does not match final snapshot index entry",
                    path=snapshot_index_path(),
                )
            )
        for entry in payload.snapshots:
            manifest_rel = f"snapshots/{entry.snapshot_id}/manifest.json"
            try:
                manifest = load_manifest(root, entry.snapshot_id)
            except Exception as exc:
                issues.append(
                    _issue(
                        stage,
                        f"failed to load manifest for {entry.snapshot_id}: {exc}",
                        path=manifest_rel,
                    )
                )
                continue
            if manifest.snapshot_id != entry.snapshot_id:
                issues.append(
                    _issue(
                        stage,
                        f"manifest snapshot_id mismatch for {entry.snapshot_id}",
                        path=manifest_rel,
                    )
                )
            if manifest_snapshot_date(manifest) != entry.snapshot_date:
                issues.append(
                    _issue(
                        stage,
                        f"snapshot_date mismatch for {entry.snapshot_id}",
                        path=manifest_rel,
                    )
                )
            if manifest_published_at(manifest) != entry.published_at:
                issues.append(
                    _issue(
                        stage,
                        f"published_at mismatch for {entry.snapshot_id}",
                        path=manifest_rel,
                    )
                )
            if manifest.root_sha256 != entry.root_sha256:
                issues.append(
                    _issue(
                        stage,
                        f"root_sha256 mismatch for {entry.snapshot_id}",
                        path=manifest_rel,
                    )
                )
            if manifest.total_files != entry.total_files:
                issues.append(
                    _issue(
                        stage,
                        f"total_files mismatch for {entry.snapshot_id}",
                        path=manifest_rel,
                    )
                )
            if manifest.total_bytes != entry.total_bytes:
                issues.append(
                    _issue(
                        stage,
                        f"total_bytes mismatch for {entry.snapshot_id}",
                        path=manifest_rel,
                    )
                )

    return (
        HistoryVerifyStageResult(
            stage=stage,
            checked=_snapshot_entry_count(payload),
            issues=tuple(issues),
        ),
        payload,
    )


def _snapshot_ids_by_date(snapshot_index: SnapshotIndexPayload) -> dict[dt.date, str]:
    return {entry.snapshot_date: entry.snapshot_id for entry in snapshot_index.snapshots}


def _member_histories(root: Path) -> list[MemberHistoryPayload]:
    history_dir = root / "history" / "members"
    if not history_dir.exists():
        return []
    histories: list[MemberHistoryPayload] = []
    for file in sorted(history_dir.glob("*.json")):
        histories.append(load_member_history(root, file.stem))
    return histories


def _member_change_summaries(
    histories: list[MemberHistoryPayload],
) -> dict[str, MemberChangeSummaryPayload]:
    return {history.slug: build_member_change_summary(history) for history in histories}


def _expected_movement_window(
    snapshot_index: SnapshotIndexPayload,
    histories: list[MemberHistoryPayload],
) -> MovementWindowPayload:
    previous = snapshot_index.snapshots[-2] if len(snapshot_index.snapshots) > 1 else None
    latest = snapshot_index.snapshots[-1]
    return build_latest_movement_window(
        histories,
        latest_snapshot_id=latest.snapshot_id,
        latest_snapshot_date=latest.snapshot_date,
        previous_snapshot_id=previous.snapshot_id if previous is not None else None,
        previous_snapshot_date=previous.snapshot_date if previous is not None else None,
    )


def _expected_history_bootstrap(
    snapshot_index: SnapshotIndexPayload,
    histories: list[MemberHistoryPayload],
) -> HistoryBootstrapPayload:
    movement_window = _expected_movement_window(snapshot_index, histories)
    summaries = _member_change_summaries(histories)
    preset_set = build_snapshot_compare_presets(
        [(entry.snapshot_id, entry.snapshot_date) for entry in snapshot_index.snapshots]
    )
    featured_member_changes = [
        summaries[change.slug]
        for change in movement_window.top_changes
        if change.slug in summaries
    ][:5]
    return HistoryBootstrapPayload(
        snapshot_index=snapshot_index,
        movement_window=movement_window,
        default_compare_preset_key=preset_set.default_preset_key,
        compare_presets=preset_set.presets,
        featured_member_changes=featured_member_changes,
    )


def _verify_bootstrap(
    root: Path,
    snapshot_index: SnapshotIndexPayload,
    histories: list[MemberHistoryPayload],
) -> HistoryVerifyStageResult:
    stage = "bootstrap"
    issues: list[HistoryVerifyIssue] = []
    try:
        bootstrap = load_history_bootstrap(root)
    except Exception as exc:
        return HistoryVerifyStageResult(
            stage=stage,
            checked=0,
            issues=(_issue(stage, f"failed to load history bootstrap: {exc}", path=history_bootstrap_path()),),
        )
    try:
        movement_window = load_movement_window(root)
    except Exception as exc:
        return HistoryVerifyStageResult(
            stage=stage,
            checked=1,
            issues=(
                _issue(stage, f"failed to load movement window: {exc}", path=movement_window_path()),
            ),
        )

    expected_bootstrap = _expected_history_bootstrap(snapshot_index, histories)
    expected_movement = expected_bootstrap.movement_window
    if bootstrap != expected_bootstrap:
        issues.append(_issue(stage, "history bootstrap mismatch", path=history_bootstrap_path()))
    if movement_window != expected_movement:
        issues.append(_issue(stage, "movement window mismatch", path=movement_window_path()))
    return HistoryVerifyStageResult(stage=stage, checked=2, issues=tuple(issues))


def _current_snapshot_summary(root: Path) -> SnapshotSummaryPayload:
    manifest = load_latest_manifest(root)
    artifact_paths = list_artifact_paths(manifest)
    homepage_feed_count = 1 if (root / "homepage" / "feed.json").is_file() else 0
    return SnapshotSummaryPayload(
        snapshot_id=manifest.snapshot_id,
        snapshot_date=manifest_snapshot_date(manifest),
        published_at=manifest_published_at(manifest),
        root_sha256=manifest.root_sha256,
        total_files=manifest.total_files,
        total_bytes=manifest.total_bytes,
        artifact_counts=ArtifactCounts(
            members=sum(1 for path in artifact_paths if path.startswith("members/")),
            evidence=sum(1 for path in artifact_paths if path.startswith("evidence/")),
            zip_feeds=sum(1 for path in artifact_paths if path.startswith("zip/")),
            homepage_feeds=homepage_feed_count,
            current_member_lookups=sum(
                1 for path in artifact_paths if path == "identity/current-member-lookup.json"
            ),
        ),
    )


def _current_member_profiles(root: Path) -> list[Any]:
    members_dir = root / "members"
    if not members_dir.exists():
        return []
    profiles = [
        load_member_profile(root, file.stem)
        for file in sorted(members_dir.glob("*.json"))
    ]
    return profiles


def _expected_current_member_lookup(root: Path) -> Any:
    profiles = _current_member_profiles(root)
    snapshot_date = _current_snapshot_summary(root).snapshot_date
    return build_current_member_lookup(profiles, snapshot_date=snapshot_date)


def _expected_homepage_bootstrap(
    root: Path,
    lookup_entries: list[CurrentMemberLookupEntry],
) -> HomepageBootstrapPayload:
    snapshot = _current_snapshot_summary(root)
    feed = load_homepage_feed(root)
    return HomepageBootstrapPayload(
        snapshot=snapshot,
        movement=MovementFeedPayload(
            snapshot_date=feed.snapshot_date,
            top_changes=feed.top_changes,
            recent_events=feed.recent_events,
            recent_evidence_card_ids=feed.recent_evidence_card_ids,
        ),
        featured_lookup_entries=build_featured_lookup_entries(
            feed.top_changes,
            feed.recent_events,
            lookup_entries,
        ),
    )


def _expected_zip_entry(
    root: Path,
    zip_code: str,
    lookup_entries: list[CurrentMemberLookupEntry],
) -> ZipEntryPayload:
    zip_feed = load_zip_feed(root, zip_code)
    snapshot = _current_snapshot_summary(root)
    lookup_by_bioguide_id = {
        entry.bioguide_id: entry
        for entry in lookup_entries
    }
    return ZipEntryPayload(
        zip_feed=zip_feed,
        member_lookup_entries=[
            lookup_by_bioguide_id[member.bioguide_id]
            for member in zip_feed.members
            if member.bioguide_id in lookup_by_bioguide_id
        ],
        snapshot=snapshot,
    )


def _expected_current_member_page(root: Path, slug: str) -> Any:
    profile = load_member_profile(root, slug)
    candidate_ids = profile.top_evidence_card_ids + [
        fire.evidence_card_id
        for fire in profile.recent_rule_fires
        if fire.evidence_card_id
    ]
    evidence_cards: list[EvidenceCardPayload] = []
    seen_ids: set[str] = set()
    for candidate_id in candidate_ids:
        if candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)
        try:
            evidence_cards.append(load_evidence_card(root, candidate_id))
        except FileNotFoundError:
            continue
    return build_member_page_payload(
        profile,
        {card.evidence_card_id: card for card in evidence_cards},
    )


def _verify_current_aggregates(root: Path) -> HistoryVerifyStageResult:
    stage = "current_aggregates"
    issues: list[HistoryVerifyIssue] = []
    checked = 0

    members_dir = root / "members"
    member_pages_dir = root / "member-pages"
    member_slugs = sorted(file.stem for file in members_dir.glob("*.json")) if members_dir.exists() else []
    member_page_slugs = (
        sorted(file.stem for file in member_pages_dir.glob("*.json"))
        if member_pages_dir.exists()
        else []
    )
    member_slug_set = set(member_slugs)
    member_page_slug_set = set(member_page_slugs)

    for slug in member_page_slugs:
        if slug not in member_slug_set:
            issues.append(
                _issue(
                    stage,
                    f"member page present without matching member profile for {slug}",
                    path=member_page_payload_path(slug),
                )
            )

    expected_lookup_entries: list[CurrentMemberLookupEntry] = []
    lookup_path = current_member_lookup_path()
    lookup_file = root / lookup_path
    if member_slugs or lookup_file.is_file():
        if not lookup_file.is_file():
            issues.append(
                _issue(
                    stage,
                    "current member lookup missing",
                    path=lookup_path,
                )
            )
        else:
            try:
                lookup_payload = load_current_member_lookup(root)
                checked += 1
            except Exception as exc:
                issues.append(
                    _issue(
                        stage,
                        f"failed to load current member lookup: {exc}",
                        path=lookup_path,
                    )
                )
            else:
                try:
                    expected_lookup = _expected_current_member_lookup(root)
                except Exception as exc:
                    issues.append(
                        _issue(
                            stage,
                            f"failed to rebuild current member lookup: {exc}",
                            path=lookup_path,
                        )
                    )
                else:
                    expected_lookup_entries = expected_lookup.members
                    if lookup_payload != expected_lookup:
                        issues.append(
                            _issue(
                                stage,
                                "current member lookup mismatch",
                                path=lookup_path,
                            )
                        )

    for slug in member_slugs:
        page_path = member_page_payload_path(slug)
        if slug not in member_page_slug_set:
            issues.append(
                _issue(
                    stage,
                    f"member page missing for {slug}",
                    path=page_path,
                )
            )
            continue
        try:
            member_page = load_member_page(root, slug)
            checked += 1
        except Exception as exc:
            issues.append(
                _issue(
                    stage,
                    f"failed to load current member page for {slug}: {exc}",
                    path=page_path,
                )
            )
            continue
        try:
            expected_member_page = _expected_current_member_page(root, slug)
        except Exception as exc:
            issues.append(
                _issue(
                    stage,
                    f"failed to rebuild current member page for {slug}: {exc}",
                    path=page_path,
                )
            )
            continue
        if member_page != expected_member_page:
            issues.append(
                _issue(
                    stage,
                    f"current member page mismatch for {slug}",
                    path=page_path,
                )
            )

    homepage_feed_file = root / "homepage" / "feed.json"
    homepage_bootstrap_file = root / homepage_bootstrap_path()
    if homepage_feed_file.is_file() or homepage_bootstrap_file.is_file():
        if not homepage_feed_file.is_file():
            issues.append(
                _issue(
                    stage,
                    "homepage bootstrap present without homepage/feed.json",
                    path=homepage_bootstrap_path(),
                )
            )
        elif not homepage_bootstrap_file.is_file():
            issues.append(
                _issue(
                    stage,
                    "homepage bootstrap missing",
                    path=homepage_bootstrap_path(),
                )
            )
        else:
            try:
                bootstrap_payload = load_homepage_bootstrap(root)
                checked += 1
            except Exception as exc:
                issues.append(
                    _issue(stage, f"failed to load homepage bootstrap: {exc}", path=homepage_bootstrap_path())
                )
            else:
                expected_bootstrap = _expected_homepage_bootstrap(root, expected_lookup_entries)
                if bootstrap_payload != expected_bootstrap:
                    issues.append(
                        _issue(stage, "homepage bootstrap mismatch", path=homepage_bootstrap_path())
                    )

    zip_dir = root / "zip"
    zip_entry_dir = root / "zip-entry"
    zip_codes = sorted(file.stem for file in zip_dir.glob("*.json")) if zip_dir.exists() else []
    zip_entry_codes = sorted(file.stem for file in zip_entry_dir.glob("*.json")) if zip_entry_dir.exists() else []

    for zip_code in zip_entry_codes:
        if zip_code not in zip_codes:
            issues.append(
                _issue(
                    stage,
                    f"zip-entry present without matching zip feed for {zip_code}",
                    path=zip_entry_path(zip_code),
                )
            )

    for zip_code in zip_codes:
        path = zip_entry_path(zip_code)
        if zip_code not in zip_entry_codes:
            issues.append(
                _issue(
                    stage,
                    f"zip-entry missing for {zip_code}",
                    path=path,
                )
            )
            continue
        try:
            zip_entry_payload = load_zip_entry(root, zip_code)
            checked += 1
        except Exception as exc:
            issues.append(_issue(stage, f"failed to load zip entry for {zip_code}: {exc}", path=path))
            continue
        expected_zip_entry = _expected_zip_entry(root, zip_code, expected_lookup_entries)
        if zip_entry_payload != expected_zip_entry:
            issues.append(_issue(stage, f"zip-entry mismatch for {zip_code}", path=path))

    return HistoryVerifyStageResult(stage=stage, checked=checked, issues=tuple(issues))


def _expected_snapshot_compare_payload(
    histories: list[MemberHistoryPayload],
    *,
    preset_key: str,
    snapshot_index: SnapshotIndexPayload,
) -> SnapshotComparePayload:
    preset_set = build_snapshot_compare_presets(
        [(entry.snapshot_id, entry.snapshot_date) for entry in snapshot_index.snapshots]
    )
    preset = next(candidate for candidate in preset_set.presets if candidate.preset_key == preset_key)
    return build_snapshot_compare_payload(
        histories,
        start_snapshot_id=preset.start_snapshot_id,
        start_snapshot_date=preset.start_snapshot_date,
        end_snapshot_id=preset.end_snapshot_id,
        end_snapshot_date=preset.end_snapshot_date,
    )


def _expected_preset_movement_window(
    histories: list[MemberHistoryPayload],
    *,
    preset_key: str,
    snapshot_index: SnapshotIndexPayload,
) -> MovementWindowPayload:
    preset_set = build_snapshot_compare_presets(
        [(entry.snapshot_id, entry.snapshot_date) for entry in snapshot_index.snapshots]
    )
    preset = next(candidate for candidate in preset_set.presets if candidate.preset_key == preset_key)
    return build_movement_window(
        histories,
        window_key=preset.preset_key,
        latest_snapshot_id=preset.end_snapshot_id,
        latest_snapshot_date=preset.end_snapshot_date,
        previous_snapshot_id=preset.start_snapshot_id,
        previous_snapshot_date=preset.start_snapshot_date,
        has_full_window=preset.has_full_window,
    )


def _expected_history_preset_range(
    histories: list[MemberHistoryPayload],
    *,
    preset_key: str,
    snapshot_index: SnapshotIndexPayload,
) -> HistoryPresetRangePayload:
    preset_set = build_snapshot_compare_presets(
        [(entry.snapshot_id, entry.snapshot_date) for entry in snapshot_index.snapshots]
    )
    preset = next(candidate for candidate in preset_set.presets if candidate.preset_key == preset_key)
    return HistoryPresetRangePayload(
        preset=preset,
        movement_window=_expected_preset_movement_window(
            histories,
            preset_key=preset_key,
            snapshot_index=snapshot_index,
        ),
        snapshot_compare=_expected_snapshot_compare_payload(
            histories,
            preset_key=preset_key,
            snapshot_index=snapshot_index,
        ),
    )


def _verify_snapshot_presets(
    root: Path,
    snapshot_index: SnapshotIndexPayload,
    histories: list[MemberHistoryPayload],
) -> HistoryVerifyStageResult:
    stage = "snapshot_presets"
    issues: list[HistoryVerifyIssue] = []
    checked = 0
    preset_set = build_snapshot_compare_presets(
        [(entry.snapshot_id, entry.snapshot_date) for entry in snapshot_index.snapshots]
    )
    for preset in preset_set.presets:
        range_path = history_preset_range_path(preset.preset_key)
        try:
            range_payload = load_history_preset_range(root, preset.preset_key)
        except Exception as exc:
            issues.append(
                _issue(
                    stage,
                    f"failed to load history preset range: {exc}",
                    path=range_path,
                )
            )
            continue
        expected_range = _expected_history_preset_range(
            histories,
            preset_key=preset.preset_key,
            snapshot_index=snapshot_index,
        )
        checked += 1
        if range_payload != expected_range:
            issues.append(
                _issue(
                    stage,
                    f"history preset range mismatch for {preset.preset_key}",
                    path=range_path,
                )
            )
        path = snapshot_preset_compare_path(preset.preset_key)
        try:
            payload = load_snapshot_preset_compare(root, preset.preset_key)
        except Exception as exc:
            issues.append(_issue(stage, f"failed to load snapshot preset compare: {exc}", path=path))
            continue
        expected = _expected_snapshot_compare_payload(
            histories,
            preset_key=preset.preset_key,
            snapshot_index=snapshot_index,
        )
        checked += 1
        if payload != expected:
            issues.append(_issue(stage, f"snapshot preset compare mismatch for {preset.preset_key}", path=path))
        if preset.preset_key == "latest":
            continue
        movement_path = movement_window_path(preset.preset_key)
        try:
            movement_payload = load_movement_window(root, preset.preset_key)
        except Exception as exc:
            issues.append(
                _issue(
                    stage,
                    f"failed to load preset movement window: {exc}",
                    path=movement_path,
                )
            )
            continue
        expected_movement = _expected_preset_movement_window(
            histories,
            preset_key=preset.preset_key,
            snapshot_index=snapshot_index,
        )
        checked += 1
        if movement_payload != expected_movement:
            issues.append(
                _issue(
                    stage,
                    f"preset movement window mismatch for {preset.preset_key}",
                    path=movement_path,
                )
            )
    return HistoryVerifyStageResult(stage=stage, checked=checked, issues=tuple(issues))


def _load_cards(root: Path, card_ids: list[str]) -> list[EvidenceCardPayload]:
    return [load_evidence_card(root, card_id) for card_id in card_ids]


def _expected_member_preset_compare(
    root: Path,
    history: MemberHistoryPayload,
    *,
    chart: MemberHistoryChartPayload,
    preset_key: str,
) -> MemberWindowComparePayload:
    preset = next(candidate for candidate in chart.compare_presets if candidate.preset_key == preset_key)
    summary = build_member_window_change_summary(
        history,
        start_snapshot_date=preset.start_snapshot_date,
        end_snapshot_date=preset.end_snapshot_date,
    )
    assert summary is not None
    return MemberWindowComparePayload(
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
        evidence_cards=_load_cards(root, summary.top_evidence_card_ids),
    )


def _verify_members(
    root: Path,
    snapshot_index: SnapshotIndexPayload,
    histories: list[MemberHistoryPayload],
) -> tuple[HistoryVerifyStageResult, dict[str, MemberHistoryChartPayload]]:
    stage = "members"
    issues: list[HistoryVerifyIssue] = []
    checked = 0
    charts: dict[str, MemberHistoryChartPayload] = {}
    snapshot_ids_by_date = _snapshot_ids_by_date(snapshot_index)

    for history in histories:
        slug = history.slug
        expected_summary = build_member_change_summary(history)
        expected_chart = build_member_history_chart(
            history,
            snapshot_ids_by_date=snapshot_ids_by_date,
        )
        expected_trend = build_member_trend_summary(history)

        try:
            summary = load_member_change_summary(root, slug)
            checked += 1
            if summary != expected_summary:
                issues.append(_issue(stage, f"member change summary mismatch for {slug}", path=member_change_summary_path(slug)))
        except Exception as exc:
            issues.append(_issue(stage, f"failed to load member change summary for {slug}: {exc}", path=member_change_summary_path(slug)))

        try:
            chart = load_member_history_chart(root, slug)
            checked += 1
            charts[slug] = chart
            if chart != expected_chart:
                issues.append(_issue(stage, f"member history chart mismatch for {slug}", path=member_history_chart_path(slug)))
        except Exception as exc:
            charts[slug] = expected_chart
            issues.append(_issue(stage, f"failed to load member history chart for {slug}: {exc}", path=member_history_chart_path(slug)))

        try:
            trend = load_member_trend_summary(root, slug)
            checked += 1
            if trend != expected_trend:
                issues.append(_issue(stage, f"member trend summary mismatch for {slug}", path=member_trend_summary_path(slug)))
        except Exception as exc:
            issues.append(_issue(stage, f"failed to load member trend summary for {slug}: {exc}", path=member_trend_summary_path(slug)))

        chart_for_presets = charts[slug]
        for preset in chart_for_presets.compare_presets:
            path = member_preset_compare_path(slug, preset.preset_key)
            try:
                compare = load_member_preset_compare(root, slug, preset.preset_key)
                checked += 1
            except Exception as exc:
                issues.append(_issue(stage, f"failed to load member preset compare for {slug}:{preset.preset_key}: {exc}", path=path))
                continue
            expected_compare = _expected_member_preset_compare(
                root,
                history,
                chart=expected_chart,
                preset_key=preset.preset_key,
            )
            if compare != expected_compare:
                issues.append(_issue(stage, f"member preset compare mismatch for {slug}:{preset.preset_key}", path=path))

    return HistoryVerifyStageResult(stage=stage, checked=checked, issues=tuple(issues)), charts


def _history_page_expected_card_ids(history: MemberHistoryPayload, *, limit: int = 25) -> list[str]:
    card_ids: list[str] = []
    seen: set[str] = set()
    for event in history.events:
        card_id = event.evidence_card_id
        if not card_id or card_id in seen:
            continue
        seen.add(card_id)
        card_ids.append(card_id)
        if len(card_ids) >= limit:
            break
    return card_ids


def _verify_member_pages(
    root: Path,
    snapshot_index: SnapshotIndexPayload,
    histories: list[MemberHistoryPayload],
    charts: dict[str, MemberHistoryChartPayload],
) -> HistoryVerifyStageResult:
    stage = "member_pages"
    issues: list[HistoryVerifyIssue] = []
    checked = 0
    for history in histories:
        slug = history.slug
        try:
            load_member_profile(root, slug)
        except FileNotFoundError:
            continue
        page_path = member_history_page_path(slug)
        try:
            page = load_member_history_page(root, slug)
            checked += 1
        except Exception as exc:
            issues.append(_issue(stage, f"failed to load member history page for {slug}: {exc}", path=page_path))
            continue

        try:
            member_page = load_member_page(root, slug)
        except Exception as exc:
            issues.append(_issue(stage, f"failed to load current member page for {slug}: {exc}", path=page_path))
            continue

        try:
            summary = load_member_change_summary(root, slug)
            trend = load_member_trend_summary(root, slug)
            chart = charts[slug]
            default_compare = load_member_preset_compare(root, slug, chart.default_preset_key)
        except Exception as exc:
            issues.append(_issue(stage, f"failed to load member page dependency for {slug}: {exc}", path=page_path))
            continue

        if page.member_page != member_page:
            issues.append(_issue(stage, f"embedded member page mismatch for {slug}", path=page_path))
        if page.history != history:
            issues.append(_issue(stage, f"embedded member history mismatch for {slug}", path=page_path))
        if page.recent_change != summary:
            issues.append(_issue(stage, f"embedded member change summary mismatch for {slug}", path=page_path))
        if page.chart != chart:
            issues.append(_issue(stage, f"embedded member history chart mismatch for {slug}", path=page_path))
        if page.trend_summary != trend:
            issues.append(_issue(stage, f"embedded member trend summary mismatch for {slug}", path=page_path))
        if page.default_window_compare != default_compare:
            issues.append(_issue(stage, f"embedded default compare mismatch for {slug}", path=page_path))
        if page.snapshot_index != snapshot_index:
            issues.append(_issue(stage, f"embedded snapshot index mismatch for {slug}", path=page_path))

        expected_history_cards = _load_cards(root, _history_page_expected_card_ids(history))
        if page.history_evidence_cards != expected_history_cards:
            issues.append(_issue(stage, f"history evidence cards mismatch for {slug}", path=page_path))

    return HistoryVerifyStageResult(stage=stage, checked=checked, issues=tuple(issues))


def verify_history_aggregate_local(root: Path) -> HistoryVerifyResult:
    snapshot_stage, snapshot_index = _verify_snapshot_index(root)
    if snapshot_index is None:
        return HistoryVerifyResult(
            stages=(
                snapshot_stage,
                _unavailable_stage("bootstrap", "snapshot index could not be loaded"),
                _unavailable_stage("snapshot_presets", "snapshot index could not be loaded"),
                _unavailable_stage("members", "snapshot index could not be loaded"),
                _unavailable_stage("member_pages", "snapshot index could not be loaded"),
            )
        )

    histories = _member_histories(root)
    bootstrap_stage = _verify_bootstrap(root, snapshot_index, histories)
    current_aggregates_stage = _verify_current_aggregates(root)
    snapshot_presets_stage = _verify_snapshot_presets(root, snapshot_index, histories)
    members_stage, charts = _verify_members(root, snapshot_index, histories)
    member_pages_stage = _verify_member_pages(root, snapshot_index, histories, charts)

    return HistoryVerifyResult(
        stages=(
            snapshot_stage,
            bootstrap_stage,
            current_aggregates_stage,
            snapshot_presets_stage,
            members_stage,
            member_pages_stage,
        )
    )


verify_local_history_aggregate = verify_history_aggregate_local
