"""Verify evidence-card artifacts within a published snapshot tree.

Entry point: verify_local_evidence_cards(root, manifest_payload)

Checks performed for each evidence-card entry in the manifest:
- The file exists on disk at the expected path.
- The file loads and parses cleanly as EvidenceCardPayload.
- The evidence_card_id in the payload matches the id encoded in the path.
"""

from __future__ import annotations

from pathlib import Path

from src.export.local_store import load_evidence_card
from src.export.manifest import SnapshotManifest
from src.runtime.publish_verify_types import (
    PublishVerifyIssue,
    PublishVerifyStageResult,
)

_EVIDENCE_PREFIX = "evidence/"
_EVIDENCE_SUFFIX = ".json"
_STAGE = "evidence"


def _id_from_path(path: str) -> str:
    """Extract evidence_card_id from a path like ``evidence/<id>.json``."""
    stem = path[len(_EVIDENCE_PREFIX):]
    if stem.endswith(_EVIDENCE_SUFFIX):
        stem = stem[: -len(_EVIDENCE_SUFFIX)]
    return stem


def verify_local_evidence_cards(
    root: Path,
    manifest_payload: SnapshotManifest,
) -> PublishVerifyStageResult:
    """Verify all evidence-card entries listed in *manifest_payload*.

    Args:
        root:             Root directory of the publish tree.
        manifest_payload: Loaded and validated SnapshotManifest.

    Returns:
        A :class:`PublishVerifyStageResult` for the ``evidence`` stage.
    """
    evidence_entries = [
        entry
        for entry in manifest_payload.entries
        if entry.path.startswith(_EVIDENCE_PREFIX)
        and entry.path.endswith(_EVIDENCE_SUFFIX)
    ]

    issues: list[PublishVerifyIssue] = []

    for entry in evidence_entries:
        path = entry.path
        expected_id = _id_from_path(path)

        # 1. File exists.
        file = root / path
        if not file.exists():
            issues.append(
                PublishVerifyIssue(
                    stage=_STAGE,
                    message=f"evidence card file missing: {path}",
                    severity="error",
                    path=path,
                )
            )
            continue

        # 2. File loads and parses cleanly.
        try:
            card = load_evidence_card(root, expected_id)
        except Exception as exc:
            issues.append(
                PublishVerifyIssue(
                    stage=_STAGE,
                    message=f"evidence card failed to load ({path}): {exc}",
                    severity="error",
                    path=path,
                )
            )
            continue

        # 3. Structural coherence: evidence_card_id is present and matches path.
        if not card.evidence_card_id:
            issues.append(
                PublishVerifyIssue(
                    stage=_STAGE,
                    message=f"evidence card has empty evidence_card_id: {path}",
                    severity="error",
                    path=path,
                )
            )
        elif card.evidence_card_id != expected_id:
            issues.append(
                PublishVerifyIssue(
                    stage=_STAGE,
                    message=(
                        f"evidence_card_id mismatch: path encodes '{expected_id}' "
                        f"but payload has '{card.evidence_card_id}'"
                    ),
                    severity="error",
                    path=path,
                )
            )

    return PublishVerifyStageResult(
        stage=_STAGE,
        checked=len(evidence_entries),
        issues=tuple(issues),
    )
