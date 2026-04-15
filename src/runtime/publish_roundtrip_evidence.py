"""Roundtrip verification for published evidence-card artifacts.

Loads each published evidence card referenced in the manifest, re-assembles
the card from canonical DB rows, and compares typed payloads field-by-field.

Public surface
--------------
verify_published_evidence_roundtrip(conn, root, manifest_payload)
    Discovers evidence-card paths in the manifest, loads each published
    :class:`~src.export.contracts.EvidenceCardPayload`, fetches DB rows via
    :func:`~src.query.published_rows.fetch_all_evidence_card_rows`, re-assembles
    the typed payload via :func:`~src.query.evidence_card.assemble_evidence_card`,
    and compares the two payloads field-by-field.  Returns a
    :class:`~src.runtime.publish_roundtrip_types.PublishRoundtripStageResult`
    for the ``"evidence"`` stage.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.export.contracts import EvidenceCardPayload
from src.export.local_store import load_evidence_card
from src.export.manifest import SnapshotManifest
from src.query.evidence_card import assemble_evidence_card
from src.query.published_rows import fetch_all_evidence_card_rows
from src.runtime.publish_roundtrip_types import (
    IssueSeverity,
    PublishRoundtripIssue,
    PublishRoundtripStageResult,
)

_STAGE = "evidence"

# Matches paths emitted by src/export/writer.evidence_path()
_EVIDENCE_PATH_RE = re.compile(r"^evidence/(?P<id>[^/]+)\.json$")


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
    published: EvidenceCardPayload,
    reassembled: EvidenceCardPayload,
    path: str,
) -> list[PublishRoundtripIssue]:
    """Return one issue per field that differs between *published* and *reassembled*.

    Scalar fields are compared directly.  Structured list fields (blocks,
    source_anchors) are reduced to comparable tuples so the message clearly
    identifies which values diverged.
    """
    issues: list[PublishRoundtripIssue] = []

    # Scalar fields ──────────────────────────────────────────────────────────
    scalar_fields = (
        "evidence_card_id",
        "member_bioguide_id",
        "member_name",
        "member_slug",
        "dimension",
        "rule_id",
        "rule_version",
        "score_delta",
        "short_explanation",
        "confidence",
        "snapshot_date",
        "created_at",
    )
    for field_name in scalar_fields:
        pub_val = getattr(published, field_name)
        rea_val = getattr(reassembled, field_name)
        if pub_val != rea_val:
            issues.append(
                _issue(
                    f"field '{field_name}' mismatch: "
                    f"published={pub_val!r} db={rea_val!r}",
                    path=path,
                )
            )

    # blocks ─────────────────────────────────────────────────────────────────
    pub_blocks = [(b.section, b.text) for b in published.blocks]
    rea_blocks = [(b.section, b.text) for b in reassembled.blocks]
    if pub_blocks != rea_blocks:
        issues.append(
            _issue(
                f"blocks mismatch: published={pub_blocks!r} db={rea_blocks!r}",
                path=path,
            )
        )

    # source_anchors ─────────────────────────────────────────────────────────
    pub_anchors = [
        (a.source_type, a.source_id, a.url, a.label)
        for a in published.source_anchors
    ]
    rea_anchors = [
        (a.source_type, a.source_id, a.url, a.label)
        for a in reassembled.source_anchors
    ]
    if pub_anchors != rea_anchors:
        issues.append(
            _issue(
                f"source_anchors mismatch: "
                f"published={pub_anchors!r} db={rea_anchors!r}",
                path=path,
            )
        )

    return issues


def verify_published_evidence_roundtrip(
    conn: Any,
    root: Path,
    manifest_payload: SnapshotManifest,
) -> PublishRoundtripStageResult:
    """Verify that every published evidence card matches its DB-assembled counterpart.

    For each evidence-card path in *manifest_payload*:

    1. Discover the evidence card ID from the manifest entry path.
    2. Load the published :class:`~src.export.contracts.EvidenceCardPayload`
       from *root*.
    3. Fetch the canonical DB row via
       :func:`~src.query.published_rows.fetch_all_evidence_card_rows` (one bulk
       query, indexed by ``public_id``).
    4. Re-assemble a fresh payload from the DB row using
       :func:`~src.query.evidence_card.assemble_evidence_card`.
    5. Compare typed payloads field-by-field and record any mismatches as
       ``"error"`` issues.

    Args:
        conn:             Live DB connection used for read-only row fetches.
        root:             Root directory of the published snapshot tree.
        manifest_payload: Parsed snapshot manifest whose entries are inspected.

    Returns:
        A :class:`~src.runtime.publish_roundtrip_types.PublishRoundtripStageResult`
        for the ``"evidence"`` stage.  ``ok`` is ``True`` when every referenced
        card matches its DB-assembled payload exactly.
    """
    # Stage 1: collect evidence-card IDs and paths from the manifest
    evidence_entries: list[tuple[str, str]] = []  # (evidence_card_id, entry_path)
    for entry in manifest_payload.entries:
        m = _EVIDENCE_PATH_RE.match(entry.path)
        if m is not None:
            evidence_entries.append((m.group("id"), entry.path))

    if not evidence_entries:
        return PublishRoundtripStageResult(stage=_STAGE, checked=0, issues=())

    # Stage 2: fetch all DB rows in one query, index by public_id
    db_rows = fetch_all_evidence_card_rows(conn)
    db_by_id: dict[str, dict[str, Any]] = {
        row["public_id"]: row for row in db_rows
    }

    issues: list[PublishRoundtripIssue] = []
    checked = 0

    for evidence_card_id, entry_path in evidence_entries:
        checked += 1

        # Step 2a: Load the published payload from disk ───────────────────────
        try:
            published = load_evidence_card(root, evidence_card_id)
        except Exception as exc:
            issues.append(
                _issue(
                    f"cannot load published evidence card '{evidence_card_id}': {exc}",
                    path=entry_path,
                )
            )
            continue

        # Step 3: Look up the DB row ──────────────────────────────────────────
        if evidence_card_id not in db_by_id:
            issues.append(
                _issue(
                    f"evidence card '{evidence_card_id}' not found in DB",
                    path=entry_path,
                )
            )
            continue

        # Step 4: Re-assemble typed payload from DB row ───────────────────────
        try:
            reassembled = assemble_evidence_card(db_by_id[evidence_card_id])
        except Exception as exc:
            issues.append(
                _issue(
                    f"failed to assemble evidence card '{evidence_card_id}' "
                    f"from DB: {exc}",
                    path=entry_path,
                )
            )
            continue

        # Step 5: Compare typed payloads ─────────────────────────────────────
        issues.extend(_compare_payloads(published, reassembled, entry_path))

    return PublishRoundtripStageResult(
        stage=_STAGE,
        checked=checked,
        issues=tuple(issues),
    )
