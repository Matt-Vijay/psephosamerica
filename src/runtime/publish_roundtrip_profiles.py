"""Roundtrip verification for published member-profile artifacts.

Loads each published member profile referenced in the manifest, re-assembles
the profile from canonical DB rows, and compares typed payloads field-by-field.
When present, the paired ``member-pages/{slug}.json`` aggregate is also
reconstructed from the DB-backed profile plus evidence cards and verified in
the same stage.

Public surface
--------------
verify_published_member_profiles_roundtrip(conn, root, manifest_payload)
    Discovers member profile paths in the manifest, loads each published
    :class:`~src.export.contracts.MemberProfilePayload`, fetches DB rows,
    re-assembles the typed payload via
    :func:`~src.query.member_profile.assemble_member_profile`, and compares
    the two payloads.  Returns a
    :class:`~src.runtime.publish_roundtrip_types.PublishRoundtripStageResult`
    for the ``"profiles"`` stage.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.api.contracts import MemberPagePayload
from src.export.contracts import EvidenceCardPayload, MemberProfilePayload
from src.export.local_store import load_member_page, load_member_profile
from src.export.manifest import SnapshotManifest
from src.export.writer import build_member_page_payload
from src.query.evidence_card import assemble_evidence_card
from src.query.member_profile import assemble_member_profile
from src.query.published_rows import (
    fetch_all_evidence_card_rows,
    fetch_member_committee_rows,
    fetch_member_row_by_slug,
    fetch_member_rule_fire_rows,
    fetch_member_score_snapshot_rows,
)
from src.runtime.publish_roundtrip_types import (
    IssueSeverity,
    PublishRoundtripIssue,
    PublishRoundtripStageResult,
)

_STAGE = "profiles"

# Matches paths emitted by src/export/writer.member_path()
_MEMBER_PATH_RE = re.compile(r"^members/(?P<slug>[^/]+)\.json$")
_MEMBER_PAGE_PATH_RE = re.compile(r"^member-pages/(?P<slug>[^/]+)\.json$")


def _issue(
    message: str,
    *,
    path: str | None = None,
    severity: IssueSeverity = "error",
) -> PublishRoundtripIssue:
    return PublishRoundtripIssue(
        stage=_STAGE,
        message=message,
        severity=severity,
        path=path,
    )


def _compare_payloads(
    published: MemberProfilePayload,
    reassembled: MemberProfilePayload,
    path: str,
) -> list[PublishRoundtripIssue]:
    """Return one issue per field that differs between *published* and *reassembled*.

    Scalar fields are compared directly.  List fields (scores,
    recent_rule_fires, committees) are reduced to comparable tuples so the
    message clearly identifies which values diverged.
    """
    issues: list[PublishRoundtripIssue] = []

    # Scalar fields ──────────────────────────────────────────────────────────
    scalar_fields = (
        "bioguide_id",
        "name",
        "slug",
        "state",
        "district",
        "chamber",
        "party",
        "total_evidence_cards",
        "snapshot_date",
    )
    for field_name in scalar_fields:
        pub_val = getattr(published, field_name)
        rea_val = getattr(reassembled, field_name)
        if pub_val != rea_val:
            issues.append(
                _issue(
                    f"field '{field_name}' mismatch: published={pub_val!r} db={rea_val!r}",
                    path=path,
                )
            )

    # scores ─────────────────────────────────────────────────────────────────
    pub_scores = [(s.dimension, s.current_score, s.rule_fire_count) for s in published.scores]
    rea_scores = [(s.dimension, s.current_score, s.rule_fire_count) for s in reassembled.scores]
    if pub_scores != rea_scores:
        issues.append(
            _issue(
                f"scores mismatch: published={pub_scores!r} db={rea_scores!r}",
                path=path,
            )
        )

    # recent_rule_fires ──────────────────────────────────────────────────────
    pub_fires = [
        (f.rule_id, f.evidence_card_id, f.short_explanation, f.score_delta, f.snapshot_date)
        for f in published.recent_rule_fires
    ]
    rea_fires = [
        (f.rule_id, f.evidence_card_id, f.short_explanation, f.score_delta, f.snapshot_date)
        for f in reassembled.recent_rule_fires
    ]
    if pub_fires != rea_fires:
        issues.append(
            _issue(
                f"recent_rule_fires mismatch: published={pub_fires!r} db={rea_fires!r}",
                path=path,
            )
        )

    if published.top_evidence_card_ids != reassembled.top_evidence_card_ids:
        issues.append(
            _issue(
                f"top_evidence_card_ids mismatch: "
                f"published={published.top_evidence_card_ids!r} "
                f"db={reassembled.top_evidence_card_ids!r}",
                path=path,
            )
        )

    # committees ─────────────────────────────────────────────────────────────
    pub_committees = [(c.committee_name, c.role) for c in published.committees]
    rea_committees = [(c.committee_name, c.role) for c in reassembled.committees]
    if pub_committees != rea_committees:
        issues.append(
            _issue(
                f"committees mismatch: published={pub_committees!r} db={rea_committees!r}",
                path=path,
            )
        )

    return issues


def _compare_member_page_payloads(
    published: MemberPagePayload,
    reassembled: MemberPagePayload,
    path: str,
) -> list[PublishRoundtripIssue]:
    issues: list[PublishRoundtripIssue] = []

    if published.profile.model_dump(mode="json") != reassembled.profile.model_dump(mode="json"):
        issues.append(
            _issue(
                "member page profile mismatch between published artifact and DB assembly",
                path=path,
            )
        )

    pub_top = [card.model_dump(mode="json") for card in published.top_evidence_cards]
    rea_top = [card.model_dump(mode="json") for card in reassembled.top_evidence_cards]
    if pub_top != rea_top:
        issues.append(
            _issue(
                f"member page top_evidence_cards mismatch: "
                f"published={[card['evidence_card_id'] for card in pub_top]!r} "
                f"db={[card['evidence_card_id'] for card in rea_top]!r}",
                path=path,
            )
        )

    pub_recent = [card.model_dump(mode="json") for card in published.recent_evidence_cards]
    rea_recent = [card.model_dump(mode="json") for card in reassembled.recent_evidence_cards]
    if pub_recent != rea_recent:
        issues.append(
            _issue(
                f"member page recent_evidence_cards mismatch: "
                f"published={[card['evidence_card_id'] for card in pub_recent]!r} "
                f"db={[card['evidence_card_id'] for card in rea_recent]!r}",
                path=path,
            )
        )

    return issues


def verify_published_member_profiles_roundtrip(
    conn: Any,
    root: Path,
    manifest_payload: SnapshotManifest,
) -> PublishRoundtripStageResult:
    """Verify manifest-backed member profiles and current-state member pages."""
    member_paths: dict[str, str] = {}
    member_page_paths: dict[str, str] = {}
    for entry in manifest_payload.entries:
        member_match = _MEMBER_PATH_RE.match(entry.path)
        if member_match is not None:
            member_paths[member_match.group("slug")] = entry.path
            continue
        page_match = _MEMBER_PAGE_PATH_RE.match(entry.path)
        if page_match is not None:
            member_page_paths[page_match.group("slug")] = entry.path

    slugs = sorted(set(member_paths) | set(member_page_paths))
    if not slugs:
        return PublishRoundtripStageResult(stage=_STAGE, checked=0, issues=())

    evidence_cards_by_id: dict[str, EvidenceCardPayload] = {}
    for row in fetch_all_evidence_card_rows(conn):
        public_id = row.get("public_id")
        if not public_id:
            continue
        try:
            evidence_cards_by_id[str(public_id)] = assemble_evidence_card(row)
        # Malformed DB rows are ignored so profile checks continue.
        except Exception:  # nosec B112
            continue

    issues: list[PublishRoundtripIssue] = []
    checked = 0

    for slug in slugs:
        entry_path = member_paths.get(slug) or member_page_paths.get(slug)
        if entry_path is None:
            continue
        checked += 1

        member_path = member_paths.get(slug)
        published: MemberProfilePayload | None = None
        if member_path is not None:
            try:
                published = load_member_profile(root, slug)
            except Exception as exc:
                issues.append(
                    _issue(
                        f"cannot load published profile for '{slug}': {exc}",
                        path=member_path,
                    )
                )
                continue

        member_row = fetch_member_row_by_slug(conn, slug)
        if member_row is None:
            issues.append(_issue(f"member '{slug}' not found in DB", path=entry_path))
            continue

        member_id: int = member_row["id"]
        score_snapshot_rows = fetch_member_score_snapshot_rows(conn, member_id)
        rule_fire_rows = fetch_member_rule_fire_rows(conn, member_id)
        committee_rows = fetch_member_committee_rows(conn, member_id)

        try:
            reassembled = assemble_member_profile(
                member_row=member_row,
                score_snapshot_rows=score_snapshot_rows,
                rule_fire_rows=rule_fire_rows,
                committee_rows=committee_rows,
            )
        except Exception as exc:
            issues.append(
                _issue(
                    f"failed to assemble profile for '{slug}' from DB: {exc}",
                    path=entry_path,
                )
            )
            continue

        if published is not None and member_path is not None:
            issues.extend(_compare_payloads(published, reassembled, member_path))

        member_page_path = member_page_paths.get(slug)
        if member_page_path is not None:
            try:
                published_page = load_member_page(root, slug)
            except Exception as exc:
                issues.append(
                    _issue(
                        f"cannot load published member page for '{slug}': {exc}",
                        path=member_page_path,
                    )
                )
                continue

            reassembled_page = build_member_page_payload(reassembled, evidence_cards_by_id)
            issues.extend(
                _compare_member_page_payloads(
                    published_page,
                    reassembled_page,
                    member_page_path,
                )
            )

    return PublishRoundtripStageResult(
        stage=_STAGE,
        checked=checked,
        issues=tuple(issues),
    )
