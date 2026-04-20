"""Snapshot writer: serialize payloads, compute paths, plan files.  No I/O."""

from __future__ import annotations

import json
from datetime import date
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from src.identity.current_member_lookup import CurrentMemberLookupPayload, build_current_member_lookup

from .builders import build_manifest, sha256_hex
from .contracts import EvidenceCardPayload, MemberHistoryPayload, MemberProfilePayload, ZipFeedPayload

if TYPE_CHECKING:
    from src.api.contracts import MemberPagePayload


# ── Serialisation ──────────────────────────────────────────────────────────────


def serialize_payload(payload: BaseModel) -> bytes:
    # sort_keys ensures byte-for-byte reproducibility across Python versions
    raw: dict[str, Any] = payload.model_dump(mode="json", by_alias=True)
    return json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")


# ── Output path helpers ────────────────────────────────────────────────────────


def member_path(slug: str) -> str:
    return f"members/{slug}.json"


def member_page_payload_path(slug: str) -> str:
    return f"member-pages/{slug}.json"


def zip_path(zip_code: str) -> str:
    return f"zip/{zip_code}.json"


def zip_entry_path(zip_code: str) -> str:
    return f"zip-entry/{zip_code}.json"


def evidence_path(evidence_card_id: str) -> str:
    return f"evidence/{evidence_card_id}.json"


def homepage_bootstrap_path() -> str:
    return "homepage/bootstrap.json"


def member_history_path(slug: str) -> str:
    return f"history/members/{slug}.json"


def member_change_summary_path(slug: str) -> str:
    return f"history/member-changes/{slug}.json"


def member_history_chart_path(slug: str) -> str:
    return f"history/member-charts/{slug}.json"


def member_history_page_path(slug: str) -> str:
    return f"history/member-pages/{slug}.json"


def member_preset_compare_path(slug: str, preset_key: str) -> str:
    return f"history/member-preset-compares/{slug}/{preset_key}.json"


def member_trend_summary_path(slug: str) -> str:
    return f"history/member-trends/{slug}.json"


def snapshot_index_path() -> str:
    return "history/snapshot-index.json"


def history_bootstrap_path() -> str:
    return "history/bootstrap.json"


def snapshot_preset_compare_path(preset_key: str) -> str:
    return f"history/snapshot-preset-compares/{preset_key}.json"


def movement_window_path(name: str = "latest") -> str:
    return f"history/movement/{name}.json"


def history_preset_range_path(preset_key: str) -> str:
    return f"history/preset-ranges/{preset_key}.json"


def current_member_lookup_path() -> str:
    return "identity/current-member-lookup.json"


def manifest_path(snapshot_id: str) -> str:
    return f"snapshots/{snapshot_id}/manifest.json"


def _is_manifest_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return len(pure.parts) == 3 and pure.parts[0] == "snapshots" and pure.parts[2] == "manifest.json"


# ── PlannedFile ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlannedFile:
    """An artifact queued for writing.  Content is pre-computed and hashed.

    Use :meth:`from_bytes` rather than constructing directly so that
    ``sha256`` and ``size_bytes`` are always derived from ``content``.
    """

    path: str
    content: bytes
    sha256: str
    size_bytes: int

    @classmethod
    def from_bytes(cls, path: str, content: bytes) -> PlannedFile:
        return cls(
            path=path,
            content=content,
            sha256=sha256_hex(content),
            size_bytes=len(content),
        )


def finalize_publish_plan(snapshot_id: str, files: list[PlannedFile]) -> list[PlannedFile]:
    manifest_file_path = manifest_path(snapshot_id)
    manifest_paths = {planned.path for planned in files if _is_manifest_path(planned.path)}

    if not manifest_paths:
        return list(files)

    if manifest_paths != {manifest_file_path}:
        unexpected = ", ".join(sorted(manifest_paths))
        raise ValueError(f"publish plan must contain exactly one canonical manifest path: {unexpected}")

    non_manifest_files = [planned for planned in files if planned.path != manifest_file_path]
    manifest = build_manifest(
        snapshot_id=snapshot_id,
        file_entries=[
            {"path": planned.path, "sha256": planned.sha256, "size_bytes": planned.size_bytes}
            for planned in non_manifest_files
        ],
    )
    finalized_manifest = PlannedFile.from_bytes(
        manifest_file_path,
        serialize_payload(manifest),
    )
    return non_manifest_files + [finalized_manifest]


# ── Snapshot plan ──────────────────────────────────────────────────────────────


def plan_snapshot(
    snapshot_id: str,
    member_profiles: list[MemberProfilePayload],
    zip_feeds: list[ZipFeedPayload],
    evidence_cards: list[EvidenceCardPayload],
    *,
    member_histories: list[MemberHistoryPayload] | None = None,
    current_member_lookup_file: PlannedFile | None = None,
    snapshot_date: date | None = None,
) -> list[PlannedFile]:
    # Manifest is always last so callers can stream data files first.
    # It covers only the data files, not itself.
    planned: list[PlannedFile] = []
    evidence_cards_by_id = {card.evidence_card_id: card for card in evidence_cards}

    for profile in member_profiles:
        planned.append(PlannedFile.from_bytes(member_path(profile.slug), serialize_payload(profile)))
        planned.append(
            PlannedFile.from_bytes(
                member_page_payload_path(profile.slug),
                serialize_payload(build_member_page_payload(profile, evidence_cards_by_id)),
            )
        )

    for feed in zip_feeds:
        planned.append(PlannedFile.from_bytes(zip_path(feed.zip_code), serialize_payload(feed)))

    for card in evidence_cards:
        planned.append(
            PlannedFile.from_bytes(evidence_path(card.evidence_card_id), serialize_payload(card))
        )

    for history in member_histories or []:
        planned.append(
            PlannedFile.from_bytes(member_history_path(history.slug), serialize_payload(history))
        )

    lookup_file = current_member_lookup_file or PlannedFile.from_bytes(
        current_member_lookup_path(),
        serialize_payload(
            build_current_member_lookup(
                member_profiles,
                snapshot_date=_resolve_lookup_snapshot_date(snapshot_id, member_profiles, snapshot_date),
            )
        ),
    )
    planned.append(lookup_file)

    manifest = build_manifest(
        snapshot_id=snapshot_id,
        file_entries=[
            {"path": f.path, "sha256": f.sha256, "size_bytes": f.size_bytes}
            for f in planned
        ],
    )
    planned.append(PlannedFile.from_bytes(manifest_path(snapshot_id), serialize_payload(manifest)))

    return planned


def _resolve_member_page_cards(
    evidence_card_ids: list[str],
    evidence_cards_by_id: dict[str, EvidenceCardPayload],
    *,
    limit: int | None = None,
) -> list[EvidenceCardPayload]:
    cards: list[EvidenceCardPayload] = []
    seen_ids: set[str] = set()
    for evidence_card_id in evidence_card_ids:
        if evidence_card_id in seen_ids:
            continue
        card = evidence_cards_by_id.get(evidence_card_id)
        if card is None:
            continue
        seen_ids.add(evidence_card_id)
        cards.append(card)
        if limit is not None and len(cards) >= limit:
            break
    return cards


def build_member_page_payload(
    profile: MemberProfilePayload,
    evidence_cards_by_id: dict[str, EvidenceCardPayload],
    *,
    top_cards_limit: int | None = None,
    recent_cards_limit: int | None = None,
) -> MemberPagePayload:
    from src.api.contracts import MemberPagePayload

    top_evidence_cards = _resolve_member_page_cards(
        profile.top_evidence_card_ids,
        evidence_cards_by_id,
        limit=top_cards_limit,
    )
    recent_evidence_cards = _resolve_member_page_cards(
        [
            fire.evidence_card_id
            for fire in profile.recent_rule_fires
            if fire.evidence_card_id
        ],
        evidence_cards_by_id,
        limit=recent_cards_limit,
    )
    return MemberPagePayload(
        profile=profile,
        top_evidence_cards=top_evidence_cards,
        recent_evidence_cards=recent_evidence_cards,
    )


def _resolve_lookup_snapshot_date(
    snapshot_id: str,
    member_profiles: list[MemberProfilePayload],
    snapshot_date: date | None,
) -> date:
    if snapshot_date is not None:
        return snapshot_date
    if member_profiles:
        return member_profiles[0].snapshot_date
    return date.fromisoformat(snapshot_id)
