"""Roundtrip verification for the current-member-lookup artifact.

Loads the published ``identity/current-member-lookup.json`` artifact, rebuilds
the same payload from the published member profiles referenced in the manifest,
and compares the two typed payloads field-by-field.

This stage intentionally derives from published member profiles rather than a
fresh DB query. Member profiles already have their own DB-backed roundtrip
stage, so this stage verifies that the compact lookup artifact remains
consistent with the canonical published member payloads that drive the product.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.export.local_store import load_current_member_lookup, load_member_profile
from src.export.manifest import SnapshotManifest
from src.export.writer import current_member_lookup_path
from src.identity.current_member_lookup import (
    CurrentMemberLookupPayload,
    build_current_member_lookup,
)
from src.runtime.publish_roundtrip_types import (
    IssueSeverity,
    PublishRoundtripIssue,
    PublishRoundtripStageResult,
)

_STAGE = "lookup"
_LOOKUP_PATH = current_member_lookup_path()
_MEMBER_PATH_RE = re.compile(r"^members/(?P<slug>[^/]+)\.json$")


def _issue(
    message: str,
    *,
    path: str | None = _LOOKUP_PATH,
    severity: IssueSeverity = "error",
) -> PublishRoundtripIssue:
    return PublishRoundtripIssue(
        stage=_STAGE,
        message=message,
        severity=severity,
        path=path,
    )


def _member_tuples(
    payload: CurrentMemberLookupPayload,
) -> list[tuple[str, str, str, str, str, str | None, str]]:
    return [
        (
            member.bioguide_id,
            member.slug,
            member.name,
            member.search_name,
            member.state,
            member.district,
            member.chamber,
        )
        for member in payload.members
    ]


def verify_published_current_member_lookup_roundtrip(
    root: Path,
    manifest_payload: SnapshotManifest,
) -> PublishRoundtripStageResult:
    """Verify that the compact current-member lookup matches published profiles."""
    issues: list[PublishRoundtripIssue] = []

    lookup_entries = [entry for entry in manifest_payload.entries if entry.path == _LOOKUP_PATH]
    if not lookup_entries:
        return PublishRoundtripStageResult(
            stage=_STAGE,
            checked=0,
            issues=(_issue(f"lookup artifact missing from manifest: {_LOOKUP_PATH}"),),
        )

    try:
        published = load_current_member_lookup(root)
    except FileNotFoundError:
        return PublishRoundtripStageResult(
            stage=_STAGE,
            checked=0,
            issues=(_issue(f"lookup artifact missing: {_LOOKUP_PATH}"),),
        )
    except Exception as exc:
        return PublishRoundtripStageResult(
            stage=_STAGE,
            checked=0,
            issues=(_issue(f"lookup artifact failed to load: {exc}"),),
        )

    member_profiles = []
    for entry in manifest_payload.entries:
        match = _MEMBER_PATH_RE.match(entry.path)
        if match is None:
            continue
        slug = match.group("slug")
        try:
            member_profiles.append(load_member_profile(root, slug))
        except Exception as exc:
            issues.append(
                _issue(
                    f"supporting member profile '{slug}' could not be loaded: {exc}",
                    path=entry.path,
                )
            )

    if issues:
        return PublishRoundtripStageResult(
            stage=_STAGE,
            checked=1,
            issues=tuple(issues),
        )

    try:
        reassembled = build_current_member_lookup(
            member_profiles,
            snapshot_date=published.snapshot_date,
        )
    except Exception as exc:
        return PublishRoundtripStageResult(
            stage=_STAGE,
            checked=1,
            issues=(_issue(f"lookup artifact could not be reassembled: {exc}"),),
        )

    if published.snapshot_date != reassembled.snapshot_date:
        issues.append(
            _issue(
                "snapshot_date mismatch: "
                f"published={published.snapshot_date!r} db={reassembled.snapshot_date!r}"
            )
        )

    pub_members = _member_tuples(published)
    rea_members = _member_tuples(reassembled)
    if pub_members != rea_members:
        issues.append(
            _issue(f"lookup members mismatch: published={pub_members!r} db={rea_members!r}")
        )

    return PublishRoundtripStageResult(
        stage=_STAGE,
        checked=1,
        issues=tuple(issues),
    )
