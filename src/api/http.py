"""Framework-agnostic HTTP adapter for published read-model endpoints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from src.api.contracts import (
    CurrentMemberLookupResponse,
    EvidenceResponse,
    HistoryBootstrapResponse,
    HistoryPresetRangeResponse,
    HomepageBootstrapResponse,
    HomepageResponse,
    LastUpdatedResponse,
    MemberChangeSummaryResponse,
    MemberHistoryChartResponse,
    MemberTrendSummaryResponse,
    MemberHistoryResponse,
    MemberCompareResponse,
    MemberHistoryPageResponse,
    MemberWindowCompareResponse,
    MemberPageResponse,
    MovementFeedResponse,
    MovementWindowResponse,
    MemberResponse,
    NotFoundBody,
    SearchSessionResponse,
    SnapshotCompareResponse,
    SnapshotIndexResponse,
    SnapshotSummaryResponse,
    ZipEntryResponse,
    ZipResponse,
)
from src.api.read_api import make_headers
from src.api.read_service import (
    get_current_member_lookup,
    get_evidence,
    get_history_bootstrap,
    get_history_preset_range,
    get_homepage_bootstrap,
    get_homepage,
    get_last_updated,
    get_member_change_summary,
    get_member_history_chart,
    get_member_trend_summary,
    get_member_history,
    get_member_compare,
    get_member_history_page,
    get_member_preset_compare,
    get_member_window_compare,
    get_member_page,
    get_member,
    get_movement_feed,
    get_movement_window,
    get_search_session,
    get_snapshot_compare,
    get_snapshot_preset_compare,
    get_snapshot_index,
    get_snapshot_summary,
    get_zip_entry,
    get_zip,
    search_current_member_lookup,
)
from src.export.builders import sha256_hex
from src.export.writer import (
    current_member_lookup_path,
    evidence_path,
    history_bootstrap_path,
    history_preset_range_path,
    member_change_summary_path,
    member_history_chart_path,
    member_preset_compare_path,
    member_history_path,
    member_path,
    member_trend_summary_path,
    movement_window_path,
    snapshot_preset_compare_path,
    zip_path,
)
from src.runtime.inspect import load_latest_local_manifest

_JSON_CT = "application/json; charset=utf-8"


@dataclass(frozen=True)
class JsonHttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


def _json_bytes(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")


def _error_headers() -> dict[str, str]:
    return {
        "Content-Type": _JSON_CT,
        "Cache-Control": "no-store",
    }


def _normalize_etag_value(etag: str) -> str:
    value = etag.strip()
    if value.startswith("W/"):
        value = value[2:].strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return value


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    candidate = _normalize_etag_value(etag)
    for raw_token in if_none_match.split(","):
        token = raw_token.strip()
        if token == "*":
            return True
        if _normalize_etag_value(token) == candidate:
            return True
    return False


def _manifest_entry_etag(
    artifact_path: str,
    *,
    snapshot_root: Path | None = None,
) -> str | None:
    try:
        manifest = load_latest_local_manifest(snapshot_root=snapshot_root)
    except FileNotFoundError:
        return None
    for entry in manifest.entries:
        if entry.path == artifact_path:
            return sha256_hex(f"{manifest.root_sha256}:{entry.sha256}".encode("utf-8"))
    return None


def _manifest_root_etag(
    *,
    snapshot_root: Path | None = None,
) -> str | None:
    try:
        manifest = load_latest_local_manifest(snapshot_root=snapshot_root)
    except FileNotFoundError:
        return None
    return manifest.root_sha256


def _response_from_success(
    payload: HomepageResponse
    | ZipResponse
    | MemberResponse
    | MemberCompareResponse
    | EvidenceResponse
    | HomepageBootstrapResponse
    | CurrentMemberLookupResponse
    | LastUpdatedResponse
    | MemberChangeSummaryResponse
    | MemberHistoryChartResponse
    | MemberTrendSummaryResponse
    | HistoryBootstrapResponse
    | HistoryPresetRangeResponse
    | MemberHistoryResponse
    | MemberPageResponse
    | MemberHistoryPageResponse
    | MemberWindowCompareResponse
    | MovementFeedResponse
    | MovementWindowResponse
    | SnapshotSummaryResponse
    | SnapshotIndexResponse
    | SearchSessionResponse
    | SnapshotCompareResponse
    | ZipEntryResponse,
    *,
    etag: str | None,
    if_none_match: str | None,
) -> JsonHttpResponse:
    headers = make_headers(payload.meta.snapshot_date, etag=etag)
    if etag is not None and _etag_matches(if_none_match, headers["ETag"]):
        return JsonHttpResponse(status_code=304, headers=headers, body=b"")
    return JsonHttpResponse(
        status_code=200,
        headers=headers,
        body=_json_bytes(payload),
    )


def _response_from_not_found(payload: NotFoundBody) -> JsonHttpResponse:
    status_code = (
        503
        if payload.resource_type == "snapshot" and payload.identifier == "latest"
        else 404
    )
    return JsonHttpResponse(
        status_code=status_code,
        headers=_error_headers(),
        body=_json_bytes(payload),
    )


def _dynamic_etag(payload: BaseModel) -> str:
    return sha256_hex(_json_bytes(payload))


def serve_homepage(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_homepage(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag("homepage/feed.json", snapshot_root=snapshot_root)
    if etag is None:
        etag = _dynamic_etag(payload)
    return _response_from_success(payload, etag=etag, if_none_match=if_none_match)


def serve_homepage_bootstrap(
    *,
    snapshot_root: Path | None = None,
    top_changes_limit: int | None = None,
    recent_events_limit: int | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_homepage_bootstrap(
        snapshot_root=snapshot_root,
        top_changes_limit=top_changes_limit,
        recent_events_limit=recent_events_limit,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_history_bootstrap(
    *,
    snapshot_root: Path | None = None,
    top_changes_limit: int | None = None,
    featured_limit: int = 5,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_history_bootstrap(
        snapshot_root=snapshot_root,
        top_changes_limit=top_changes_limit,
        featured_limit=featured_limit,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    if top_changes_limit is None and featured_limit == 5:
        etag = _manifest_entry_etag(
            history_bootstrap_path(),
            snapshot_root=snapshot_root,
        )
        if etag is None:
            etag = _dynamic_etag(payload)
    else:
        etag = _dynamic_etag(payload)
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )


def serve_zip(
    zip_code: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_zip(zip_code, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(zip_path(zip_code), snapshot_root=snapshot_root),
        if_none_match=if_none_match,
    )


def serve_zip_entry(
    zip_code: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_zip_entry(zip_code, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_member(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member(slug, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(member_path(slug), snapshot_root=snapshot_root),
        if_none_match=if_none_match,
    )


def serve_member_page(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    top_cards_limit: int = 3,
    recent_cards_limit: int = 5,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_page(
        slug,
        snapshot_root=snapshot_root,
        top_cards_limit=top_cards_limit,
        recent_cards_limit=recent_cards_limit,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_member_history(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_history(slug, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            member_history_path(slug),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_member_change_summary(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_change_summary(slug, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        member_change_summary_path(slug),
        snapshot_root=snapshot_root,
    )
    if etag is None:
        etag = _dynamic_etag(payload)
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )


def serve_member_history_chart(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_history_chart(slug, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        member_history_chart_path(slug),
        snapshot_root=snapshot_root,
    )
    if etag is None:
        etag = _dynamic_etag(payload)
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )


def serve_member_window_compare(
    slug: str,
    start_snapshot_id: str,
    end_snapshot_id: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_window_compare(
        slug,
        start_snapshot_id,
        end_snapshot_id,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_member_preset_compare(
    slug: str,
    preset_key: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_preset_compare(
        slug,
        preset_key,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        member_preset_compare_path(slug, preset_key),
        snapshot_root=snapshot_root,
    )
    if etag is None:
        etag = _dynamic_etag(payload)
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )


def serve_member_trend_summary(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_trend_summary(slug, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        member_trend_summary_path(slug),
        snapshot_root=snapshot_root,
    )
    if etag is None:
        etag = _dynamic_etag(payload)
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )


def serve_member_history_page(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    top_cards_limit: int = 3,
    recent_cards_limit: int = 5,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_history_page(
        slug,
        snapshot_root=snapshot_root,
        top_cards_limit=top_cards_limit,
        recent_cards_limit=recent_cards_limit,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_member_compare(
    left_slug: str,
    right_slug: str,
    *,
    snapshot_root: Path | None = None,
    top_cards_limit: int = 3,
    recent_cards_limit: int = 5,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_compare(
        left_slug,
        right_slug,
        snapshot_root=snapshot_root,
        top_cards_limit=top_cards_limit,
        recent_cards_limit=recent_cards_limit,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_evidence(
    evidence_card_id: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_evidence(evidence_card_id, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            evidence_path(evidence_card_id),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_current_member_lookup(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_current_member_lookup(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            current_member_lookup_path(),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_current_member_lookup_search(
    query: str,
    *,
    limit: int = 8,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = search_current_member_lookup(
        query,
        limit=limit,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_search_session(
    query: str,
    *,
    snapshot_root: Path | None = None,
    limit: int = 8,
    preview_limit: int = 3,
    top_cards_limit: int = 1,
    recent_cards_limit: int = 2,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_search_session(
        query,
        snapshot_root=snapshot_root,
        limit=limit,
        preview_limit=preview_limit,
        top_cards_limit=top_cards_limit,
        recent_cards_limit=recent_cards_limit,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_last_updated(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_last_updated(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_root_etag(snapshot_root=snapshot_root)
    if etag is None:
        etag = _dynamic_etag(payload)
    return _response_from_success(payload, etag=etag, if_none_match=if_none_match)


def serve_movement_feed(
    *,
    snapshot_root: Path | None = None,
    top_changes_limit: int | None = None,
    recent_events_limit: int | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_movement_feed(
        snapshot_root=snapshot_root,
        top_changes_limit=top_changes_limit,
        recent_events_limit=recent_events_limit,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_movement_window(
    *,
    name: str = "latest",
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_movement_window(name=name, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        movement_window_path(name),
        snapshot_root=snapshot_root,
    )
    if etag is None:
        etag = _dynamic_etag(payload)
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )


def serve_snapshot_summary(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_snapshot_summary(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_root_etag(snapshot_root=snapshot_root) or _dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_snapshot_index(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_snapshot_index(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_snapshot_compare(
    start_snapshot_id: str,
    end_snapshot_id: str,
    *,
    snapshot_root: Path | None = None,
    top_n: int = 10,
    recent_n: int = 20,
    featured_n: int = 5,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_snapshot_compare(
        start_snapshot_id,
        end_snapshot_id,
        snapshot_root=snapshot_root,
        top_n=top_n,
        recent_n=recent_n,
        featured_n=featured_n,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_dynamic_etag(payload),
        if_none_match=if_none_match,
    )


def serve_snapshot_preset_compare(
    preset_key: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_snapshot_preset_compare(
        preset_key,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        snapshot_preset_compare_path(preset_key),
        snapshot_root=snapshot_root,
    )
    if etag is None:
        etag = _dynamic_etag(payload)
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )


def serve_history_preset_range(
    preset_key: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_history_preset_range(
        preset_key,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        history_preset_range_path(preset_key),
        snapshot_root=snapshot_root,
    )
    if etag is None:
        etag = _dynamic_etag(payload)
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )
