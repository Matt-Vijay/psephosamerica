"""Framework-agnostic HTTP adapter for published read-model endpoints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from src.api.contracts import (
    ApiEnvelope,
    NotFoundBody,
)
from src.api.read_api import make_headers
from src.api.read_service import (
    get_current_member_lookup,
    get_evidence,
    get_history_backfill_bootstrap,
    get_history_backfill_report,
    get_history_bootstrap,
    get_history_event,
    get_history_event_page,
    get_history_preset_range,
    get_homepage,
    get_homepage_bootstrap,
    get_last_updated,
    get_member,
    get_member_change_summary,
    get_member_compare,
    get_member_history,
    get_member_history_chart,
    get_member_history_coverage,
    get_member_history_coverage_index,
    get_member_history_page,
    get_member_page,
    get_member_preset_compare,
    get_member_timeline_dimension,
    get_member_timeline_index,
    get_member_timeline_page,
    get_member_timeline_year,
    get_member_trend_summary,
    get_member_window_compare,
    get_movement_feed,
    get_movement_window,
    get_ontology_graph,
    get_ontology_index,
    get_ontology_member_features,
    get_ontology_member_graph,
    get_prediction_bootstrap,
    get_prediction_committee_context,
    get_prediction_committee_readiness,
    get_prediction_member_context,
    get_prediction_member_readiness,
    get_prediction_readiness,
    get_prediction_readiness_index,
    get_prediction_sector_context,
    get_prediction_sector_readiness,
    get_prediction_source_context,
    get_prediction_source_index,
    get_prediction_topology,
    get_search_session,
    get_snapshot_compare,
    get_snapshot_index,
    get_snapshot_preset_compare,
    get_snapshot_summary,
    get_zip,
    get_zip_entry,
    search_current_member_lookup,
)
from src.export.builders import sha256_hex
from src.export.writer import (
    current_member_lookup_path,
    evidence_path,
    history_bootstrap_path,
    history_event_page_path,
    history_event_path,
    history_preset_range_path,
    member_change_summary_path,
    member_history_chart_path,
    member_history_coverage_index_path,
    member_history_coverage_path,
    member_history_path,
    member_path,
    member_preset_compare_path,
    member_timeline_dimension_path,
    member_timeline_index_path,
    member_timeline_page_path,
    member_timeline_year_path,
    member_trend_summary_path,
    movement_window_path,
    ontology_edges_path,
    ontology_index_path,
    ontology_member_edges_path,
    ontology_member_features_path,
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
        model.model_dump(mode="json", by_alias=True),
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
    for raw_etag_member in if_none_match.split(","):
        etag_member = raw_etag_member.strip()
        if etag_member == "*":
            return True
        if _normalize_etag_value(etag_member) == candidate:
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
            return sha256_hex(f"{manifest.root_sha256}:{entry.sha256}".encode())
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


def _response_from_success[T: BaseModel](
    payload: ApiEnvelope[T],
    *,
    etag: str | None,
    if_none_match: str | None,
) -> JsonHttpResponse:
    body: bytes | None = None
    if etag is None:
        body = _json_bytes(payload)
        resolved_etag = sha256_hex(body)
    else:
        resolved_etag = etag
    headers = make_headers(payload.meta.snapshot_date, etag=resolved_etag)
    if _etag_matches(if_none_match, headers["ETag"]):
        return JsonHttpResponse(status_code=304, headers=headers, body=b"")
    if body is None:
        body = _json_bytes(payload)
    return JsonHttpResponse(
        status_code=200,
        headers=headers,
        body=body,
    )


def _response_from_not_found(payload: NotFoundBody) -> JsonHttpResponse:
    status_code = (
        503 if payload.resource_type == "snapshot" and payload.identifier == "latest" else 404
    )
    return JsonHttpResponse(
        status_code=status_code,
        headers=_error_headers(),
        body=_json_bytes(payload),
    )


def serve_homepage(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_homepage(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag("homepage/feed.json", snapshot_root=snapshot_root)
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
        etag=None,
        if_none_match=if_none_match,
    )


def serve_history_bootstrap(
    *,
    snapshot_root: Path | None = None,
    dimension: str | None = None,
    top_changes_limit: int | None = None,
    featured_limit: int = 5,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_history_bootstrap(
        snapshot_root=snapshot_root,
        dimension=dimension,
        top_changes_limit=top_changes_limit,
        featured_limit=featured_limit,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = (
        _manifest_entry_etag(
            history_bootstrap_path(),
            snapshot_root=snapshot_root,
        )
        if top_changes_limit is None and featured_limit == 5 and dimension is None
        else None
    )
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )


def serve_history_backfill_report(
    *,
    target_root: Path,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_history_backfill_report(target_root=target_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=None,
        if_none_match=if_none_match,
    )


def serve_history_backfill_bootstrap(
    *,
    target_root: Path,
    dimension: str | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_history_backfill_bootstrap(target_root=target_root, dimension=dimension)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=None,
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
        etag=None,
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
        etag=None,
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


def serve_member_timeline_index(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_timeline_index(slug, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        member_timeline_index_path(slug),
        snapshot_root=snapshot_root,
    )
    return _response_from_success(payload, etag=etag, if_none_match=if_none_match)


def serve_member_timeline_page(
    slug: str,
    page: int,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_timeline_page(
        slug,
        page,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        member_timeline_page_path(slug, page),
        snapshot_root=snapshot_root,
    )
    return _response_from_success(payload, etag=etag, if_none_match=if_none_match)


def serve_member_timeline_dimension(
    slug: str,
    dimension: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_timeline_dimension(
        slug,
        dimension,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        member_timeline_dimension_path(slug, dimension),
        snapshot_root=snapshot_root,
    )
    return _response_from_success(payload, etag=etag, if_none_match=if_none_match)


def serve_member_timeline_year(
    slug: str,
    year: int,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_timeline_year(
        slug,
        year,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        member_timeline_year_path(slug, year),
        snapshot_root=snapshot_root,
    )
    return _response_from_success(payload, etag=etag, if_none_match=if_none_match)


def serve_history_event(
    event_id: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_history_event(event_id, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        history_event_path(event_id),
        snapshot_root=snapshot_root,
    )
    return _response_from_success(payload, etag=etag, if_none_match=if_none_match)


def serve_history_event_page(
    event_id: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_history_event_page(event_id, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        history_event_page_path(event_id),
        snapshot_root=snapshot_root,
    )
    return _response_from_success(payload, etag=etag, if_none_match=if_none_match)


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
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )


def serve_member_history_coverage(
    slug: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_history_coverage(slug, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        member_history_coverage_path(slug),
        snapshot_root=snapshot_root,
    )
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )


def serve_member_history_coverage_index(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_member_history_coverage_index(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        member_history_coverage_index_path(),
        snapshot_root=snapshot_root,
    )
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
        etag=None,
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
        etag=None,
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
        etag=None,
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


def serve_ontology_graph(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_ontology_graph(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            ontology_edges_path(),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_ontology_index(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_ontology_index(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            ontology_index_path(),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_ontology_member_graph(
    member_bioguide_id: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_ontology_member_graph(member_bioguide_id, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            ontology_member_edges_path(member_bioguide_id),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_ontology_member_features(
    member_bioguide_id: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_ontology_member_features(member_bioguide_id, snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            ontology_member_features_path(member_bioguide_id),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_readiness(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_readiness(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_readiness_path(),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_bootstrap(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_bootstrap(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_bootstrap_path(),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_topology(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_topology(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_topology_path(),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_readiness_index(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_readiness_index(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_readiness_index_path(),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_sector_readiness(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_sector_readiness(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_sector_readiness_path(),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_source_index(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_source_index(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_source_index_path(),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_source_context(
    source_key: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_source_context(
        source_key,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_source_context_path(source_key),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_sector_context(
    sector_id: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_sector_context(
        sector_id,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_sector_context_path(sector_id),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_committee_readiness(
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_committee_readiness(snapshot_root=snapshot_root)
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_committee_readiness_path(),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_committee_context(
    committee_id: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_committee_context(
        committee_id,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_committee_context_path(committee_id),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_member_readiness(
    member_bioguide_id: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_member_readiness(
        member_bioguide_id,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_member_readiness_path(member_bioguide_id),
            snapshot_root=snapshot_root,
        ),
        if_none_match=if_none_match,
    )


def serve_prediction_member_context(
    member_bioguide_id: str,
    *,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_prediction_member_context(
        member_bioguide_id,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    return _response_from_success(
        payload,
        etag=_manifest_entry_etag(
            prediction_member_context_path(member_bioguide_id),
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
        etag=None,
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
        etag=None,
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
        etag=None,
        if_none_match=if_none_match,
    )


def serve_movement_window(
    *,
    name: str = "latest",
    dimension: str | None = None,
    snapshot_root: Path | None = None,
    if_none_match: str | None = None,
) -> JsonHttpResponse:
    payload = get_movement_window(
        name=name,
        dimension=dimension,
        snapshot_root=snapshot_root,
    )
    if isinstance(payload, NotFoundBody):
        return _response_from_not_found(payload)
    etag = _manifest_entry_etag(
        movement_window_path(name, dimension=dimension),
        snapshot_root=snapshot_root,
    )
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
        etag=_manifest_root_etag(snapshot_root=snapshot_root),
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
        etag=None,
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
        etag=None,
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
    return _response_from_success(
        payload,
        etag=etag,
        if_none_match=if_none_match,
    )
