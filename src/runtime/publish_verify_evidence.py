"""Verify evidence-card artifacts within a published snapshot tree.

Entry point: verify_local_evidence_cards(root, manifest_payload)

Checks performed for each evidence-card entry in the manifest:
- The entry path is confined to the publish root.
- The file exists on disk at the expected path.
- The file loads and parses cleanly as EvidenceCardPayload.
- The evidence_card_id in the payload matches the id encoded in the path.
"""

from __future__ import annotations

from pathlib import Path

from src.evidence.source_anchor_policy import (
    describe_missing_source_anchor_urls,
    has_https_source_url,
    has_official_claim_source_anchor,
)
from src.export.local_store import load_evidence_card
from src.export.manifest import SnapshotManifest
from src.runtime.publish_verify_types import (
    IssueSeverity,
    PublishVerifyIssue,
    PublishVerifyStageResult,
    path_is_confined,
)

_EVIDENCE_PREFIX = "evidence/"
_EVIDENCE_SUFFIX = ".json"
_STAGE = "evidence"


def _issue(
    message: str, *, path: str | None = None, severity: IssueSeverity = "error"
) -> PublishVerifyIssue:
    return PublishVerifyIssue(stage=_STAGE, message=message, severity=severity, path=path)


def _id_from_path(path: str) -> str:
    """Extract evidence_card_id from a path like ``evidence/<id>.json``."""
    stem = path[len(_EVIDENCE_PREFIX) :]
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
        if entry.path.startswith(_EVIDENCE_PREFIX) and entry.path.endswith(_EVIDENCE_SUFFIX)
    ]

    issues: list[PublishVerifyIssue] = []

    for entry in evidence_entries:
        path = entry.path
        expected_id = _id_from_path(path)

        # 0. Path confinement.
        if not path_is_confined(path):
            issues.append(_issue("entry path escapes publish root", path=path))
            continue

        # 1. File exists.
        file = root / path
        if not file.exists():
            issues.append(_issue(f"evidence card file missing: {path}", path=path))
            continue

        # 2. File loads and parses cleanly.
        try:
            card = load_evidence_card(root, expected_id)
        except Exception as exc:
            issues.append(_issue(f"evidence card failed to load ({path}): {exc}", path=path))
            continue

        # 3. Structural coherence: evidence_card_id is present and matches path.
        if not card.evidence_card_id:
            issues.append(_issue(f"evidence card has empty evidence_card_id: {path}", path=path))
        elif card.evidence_card_id != expected_id:
            issues.append(
                _issue(
                    f"evidence_card_id mismatch: path encodes '{expected_id}' "
                    f"but payload has '{card.evidence_card_id}'",
                    path=path,
                )
            )

        if card.score_delta != 0 and not card.source_anchors:
            issues.append(
                _issue(
                    "nonzero evidence card is missing source anchor",
                    path=path,
                )
            )
        elif card.score_delta != 0 and not has_https_source_url(card.source_anchors):
            issues.append(
                _issue(
                    "nonzero evidence card is missing HTTPS source URL",
                    path=path,
                )
            )
        else:
            missing_source_urls = describe_missing_source_anchor_urls(card.source_anchors)
            if card.score_delta != 0 and missing_source_urls:
                issues.append(
                    _issue(
                        "nonzero evidence card has claim-bearing source anchor "
                        f"without HTTPS source URL: {missing_source_urls}",
                        path=path,
                    )
                )
            elif card.score_delta != 0 and not has_official_claim_source_anchor(
                card.source_anchors
            ):
                issues.append(
                    _issue(
                        "nonzero evidence card is missing official source anchor",
                        path=path,
                    )
                )

    return PublishVerifyStageResult(
        stage=_STAGE,
        checked=len(evidence_entries),
        issues=tuple(issues),
    )
