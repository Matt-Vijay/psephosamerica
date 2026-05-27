from __future__ import annotations

from collections.abc import Iterable

from src.evidence.source_anchor_policy import is_official_source_url
from src.export.contracts import SourceAnchor


_LEGISLATIVE_SOURCE_TYPES = frozenset({"legislative_bill", "legislative_vote"})
SourceAnchorIdentity = tuple[str, str] | tuple[str, str, str | None, str | None, str | None]


def source_anchor_identity(anchor: SourceAnchor) -> SourceAnchorIdentity:
    """Return the canonical identity for a source-backed prediction fact."""
    if anchor.source_type in _LEGISLATIVE_SOURCE_TYPES:
        return (
            anchor.source_type,
            anchor.source_id,
            anchor.jurisdiction_id,
            anchor.legislative_body_id,
            anchor.legislative_session_id,
        )
    return (anchor.source_type, anchor.source_id)


def source_anchor_preference_key(
    anchor: SourceAnchor,
) -> tuple[int, int, int, str, str, str, int, str]:
    """Rank duplicate anchors so the most useful display/public URL survives."""
    url = anchor.url or ""
    missing_legislative_context = anchor.source_type in _LEGISLATIVE_SOURCE_TYPES and not (
        anchor.jurisdiction_id and anchor.legislative_body_id and anchor.legislative_session_id
    )
    return (
        0 if is_official_source_url(anchor.source_type, anchor.url) else 1,
        0 if url else 1,
        1 if missing_legislative_context else 0,
        anchor.source_type,
        anchor.source_id,
        url,
        len(anchor.label),
        anchor.label,
    )


def _identity_sort_key(identity: SourceAnchorIdentity) -> tuple[str, ...]:
    """None-safe, total ordering for identities (legislative context may be None)."""
    return tuple("" if part is None else part for part in identity)


def dedupe_prediction_source_anchors(anchors: Iterable[SourceAnchor]) -> list[SourceAnchor]:
    """Collapse duplicate prediction anchors by source identity with deterministic winners."""
    anchors_by_identity: dict[SourceAnchorIdentity, SourceAnchor] = {}
    for anchor in sorted(anchors, key=source_anchor_preference_key):
        anchors_by_identity.setdefault(source_anchor_identity(anchor), anchor)
    return [
        anchors_by_identity[identity]
        for identity in sorted(anchors_by_identity, key=_identity_sort_key)
    ]


def describe_missing_legislative_source_context(anchors: Iterable[SourceAnchor]) -> list[str]:
    """Return legislative source anchors missing portable jurisdiction context."""
    missing: list[str] = []
    for anchor in anchors:
        if anchor.source_type not in _LEGISLATIVE_SOURCE_TYPES:
            continue
        missing_fields = [
            field_name
            for field_name, value in (
                ("jurisdiction_id", anchor.jurisdiction_id),
                ("legislative_body_id", anchor.legislative_body_id),
                ("legislative_session_id", anchor.legislative_session_id),
            )
            if not value
        ]
        if missing_fields:
            missing.append(
                f"{anchor.source_type}:{anchor.source_id} missing {','.join(missing_fields)}"
            )
    return sorted(missing)
