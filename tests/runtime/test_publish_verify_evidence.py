"""Tests for src/runtime/publish_verify_evidence.py.

Uses real temp directories — no mocks for filesystem operations.
No network calls; all payloads are constructed inline.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from src.export.contracts import (
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    SourceAnchor,
)
from src.export.manifest import ManifestEntry, SnapshotManifest, manifest_root_sha256
from src.export.writer import evidence_path, serialize_payload
from src.runtime.publish_verify_evidence import verify_local_evidence_cards


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SNAPSHOT_DATE = date(2026, 4, 14)
SNAPSHOT_ID = "2026-04-14"


def _card(card_id: str = "ec-001") -> EvidenceCardPayload:
    return EvidenceCardPayload(
        evidence_card_id=card_id,
        member_bioguide_id="P000197",
        member_name="Nancy Pelosi",
        member_slug="nancy-pelosi",
        dimension="conflict_of_interest_risk",
        rule_id="committee_sector_trade",
        rule_version=1,
        score_delta=-2.5,
        short_explanation="Trade overlaps committee jurisdiction.",
        blocks=[
            EvidenceBlock(section=EvidenceSection.FACT, text="PTR disclosed a trade."),
        ],
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-1",
                url="https://disclosures.house.gov/public_disc/ptr-pdfs/2024/1.pdf",
                label="Financial disclosure",
            )
        ],
        confidence=ConfidenceLabel.MEDIUM,
        snapshot_date=SNAPSHOT_DATE,
        created_at=datetime(2026, 4, 14, 8, 0, 0),
    )


def _manifest_with_cards(*card_ids: str) -> SnapshotManifest:
    entries = [
        ManifestEntry(
            path=evidence_path(cid),
            sha256="a" * 64,
            size_bytes=100,
        )
        for cid in card_ids
    ]
    return SnapshotManifest(
        snapshot_id=SNAPSHOT_ID,
        created_at=datetime(2026, 4, 14, 8, 0, 0),
        entries=entries,
        total_files=len(entries),
        total_bytes=sum(e.size_bytes for e in entries),
        root_sha256=manifest_root_sha256(entries),
    )


def _write_card(root: Path, card: EvidenceCardPayload) -> None:
    """Write a card payload to the correct path under *root*."""
    dest = root / evidence_path(card.evidence_card_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(serialize_payload(card))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestVerifyLocalEvidenceCardsOk:
    def test_empty_manifest_returns_zero_checked(self, tmp_path: Path) -> None:
        manifest = _manifest_with_cards()
        result = verify_local_evidence_cards(tmp_path, manifest)
        assert result.stage == "evidence"
        assert result.checked == 0
        assert result.ok is True
        assert result.issues == ()

    def test_single_card_all_good(self, tmp_path: Path) -> None:
        card = _card("ec-single-001")
        _write_card(tmp_path, card)
        manifest = _manifest_with_cards("ec-single-001")

        result = verify_local_evidence_cards(tmp_path, manifest)

        assert result.ok is True
        assert result.checked == 1
        assert result.issues == ()

    def test_multiple_cards_all_good(self, tmp_path: Path) -> None:
        ids = ["ec-alpha", "ec-beta", "ec-gamma"]
        for cid in ids:
            _write_card(tmp_path, _card(cid))
        manifest = _manifest_with_cards(*ids)

        result = verify_local_evidence_cards(tmp_path, manifest)

        assert result.ok is True
        assert result.checked == 3
        assert result.issues == ()

    def test_non_evidence_entries_are_ignored(self, tmp_path: Path) -> None:
        """Manifest entries for members/ and zip/ should not be checked."""
        card = _card("ec-only-one")
        _write_card(tmp_path, card)

        entries = [
            ManifestEntry(path="members/some-member.json", sha256="b" * 64, size_bytes=50),
            ManifestEntry(path="zip/94102.json", sha256="c" * 64, size_bytes=50),
            ManifestEntry(path=evidence_path("ec-only-one"), sha256="d" * 64, size_bytes=50),
        ]
        manifest = SnapshotManifest(
            snapshot_id=SNAPSHOT_ID,
            created_at=datetime(2026, 4, 14, 8, 0, 0),
            entries=entries,
            total_files=3,
            total_bytes=150,
            root_sha256=manifest_root_sha256(entries),
        )

        result = verify_local_evidence_cards(tmp_path, manifest)

        assert result.ok is True
        assert result.checked == 1


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


class TestVerifyLocalEvidenceCardsMissingFile:
    def test_missing_file_raises_error_issue(self, tmp_path: Path) -> None:
        manifest = _manifest_with_cards("ec-missing")
        # Do NOT write the file

        result = verify_local_evidence_cards(tmp_path, manifest)

        assert result.ok is False
        assert result.checked == 1
        assert result.error_count == 1
        issue = result.issues[0]
        assert issue.severity == "error"
        assert issue.stage == "evidence"
        assert "missing" in issue.message
        assert issue.path == evidence_path("ec-missing")

    def test_partial_missing_accumulates_errors(self, tmp_path: Path) -> None:
        _write_card(tmp_path, _card("ec-present"))
        manifest = _manifest_with_cards("ec-present", "ec-absent")

        result = verify_local_evidence_cards(tmp_path, manifest)

        assert result.ok is False
        assert result.checked == 2
        assert result.error_count == 1
        assert result.warning_count == 0


# ---------------------------------------------------------------------------
# Bad JSON
# ---------------------------------------------------------------------------


class TestVerifyLocalEvidenceCardsBadJson:
    def test_invalid_json_reports_error(self, tmp_path: Path) -> None:
        path = tmp_path / evidence_path("ec-bad-json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{ not valid json }")
        manifest = _manifest_with_cards("ec-bad-json")

        result = verify_local_evidence_cards(tmp_path, manifest)

        assert result.ok is False
        assert result.error_count == 1
        issue = result.issues[0]
        assert issue.stage == "evidence"
        assert issue.path == evidence_path("ec-bad-json")

    def test_schema_validation_failure_reports_error(self, tmp_path: Path) -> None:
        """Payload missing required fields should report a load error."""
        path = tmp_path / evidence_path("ec-schema-bad")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps({"evidence_card_id": "ec-schema-bad"}).encode())
        manifest = _manifest_with_cards("ec-schema-bad")

        result = verify_local_evidence_cards(tmp_path, manifest)

        assert result.ok is False
        assert result.error_count == 1


# ---------------------------------------------------------------------------
# Path confinement
# ---------------------------------------------------------------------------


class TestVerifyLocalEvidenceCardsPathConfinement:
    def test_dotdot_path_is_error(self, tmp_path: Path) -> None:
        """Entry with '..' in path should be flagged, not followed."""
        entries = [
            ManifestEntry(
                path="evidence/../../etc/passwd.json",
                sha256="a" * 64,
                size_bytes=100,
            )
        ]
        manifest = SnapshotManifest(
            snapshot_id=SNAPSHOT_ID,
            created_at=datetime(2026, 4, 14, 8, 0, 0),
            entries=entries,
            total_files=1,
            total_bytes=100,
            root_sha256=manifest_root_sha256(entries),
        )
        result = verify_local_evidence_cards(tmp_path, manifest)
        assert result.ok is False
        assert any("escapes" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# ID mismatch
# ---------------------------------------------------------------------------


class TestVerifyLocalEvidenceCardsIdMismatch:
    def test_id_mismatch_between_path_and_payload(self, tmp_path: Path) -> None:
        """Card written with one ID but manifest references a different path ID."""
        card = _card("ec-real-id")
        # Write the card to a *different* path (ec-wrong-id)
        dest = tmp_path / evidence_path("ec-wrong-id")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(serialize_payload(card))  # payload has ec-real-id

        manifest = _manifest_with_cards("ec-wrong-id")

        result = verify_local_evidence_cards(tmp_path, manifest)

        assert result.ok is False
        assert result.error_count == 1
        issue = result.issues[0]
        assert "mismatch" in issue.message
        assert "ec-wrong-id" in issue.message
        assert "ec-real-id" in issue.message


# ---------------------------------------------------------------------------
# Source anchors
# ---------------------------------------------------------------------------


class TestVerifyLocalEvidenceCardsSourceAnchors:
    def test_nonzero_score_card_without_source_anchor_is_error(self, tmp_path: Path) -> None:
        card = _card("ec-unanchored").model_copy(update={"source_anchors": []})
        _write_card(tmp_path, card)

        result = verify_local_evidence_cards(tmp_path, _manifest_with_cards("ec-unanchored"))

        assert result.ok is False
        assert result.error_count == 1
        issue = result.issues[0]
        assert issue.path == evidence_path("ec-unanchored")
        assert "source anchor" in issue.message

    def test_nonzero_score_card_without_source_url_is_error(self, tmp_path: Path) -> None:
        card = _card("ec-no-source-url").model_copy(
            update={
                "source_anchors": [
                    SourceAnchor(
                        source_type="financial_disclosure",
                        source_id="fd-1",
                        url=None,
                        label="Financial disclosure",
                    )
                ]
            }
        )
        _write_card(tmp_path, card)

        result = verify_local_evidence_cards(tmp_path, _manifest_with_cards("ec-no-source-url"))

        assert result.ok is False
        assert result.error_count == 1
        issue = result.issues[0]
        assert issue.path == evidence_path("ec-no-source-url")
        assert "source URL" in issue.message

    def test_nonzero_score_card_with_http_source_url_is_error(self, tmp_path: Path) -> None:
        card = _card("ec-http-source").model_copy(
            update={
                "source_anchors": [
                    SourceAnchor(
                        source_type="financial_disclosure",
                        source_id="fd-1",
                        url="http://disclosures.house.gov/public_disc/ptr-pdfs/2024/1",
                        label="Financial disclosure",
                    )
                ]
            }
        )
        _write_card(tmp_path, card)

        result = verify_local_evidence_cards(tmp_path, _manifest_with_cards("ec-http-source"))

        assert result.ok is False
        assert result.error_count == 1
        assert "source URL" in result.issues[0].message

    def test_nonzero_score_card_requires_url_on_claim_bearing_anchor(self, tmp_path: Path) -> None:
        card = _card("ec-claim-anchor-no-url").model_copy(
            update={
                "source_anchors": [
                    SourceAnchor(
                        source_type="financial_disclosure",
                        source_id="fd-1",
                        url=None,
                        label="Financial disclosure",
                    ),
                    SourceAnchor(
                        source_type="committee_membership",
                        source_id="committee-science",
                        url="https://www.congress.gov/committees/science",
                        label="Science Committee",
                    ),
                ]
            }
        )
        _write_card(tmp_path, card)

        result = verify_local_evidence_cards(
            tmp_path,
            _manifest_with_cards("ec-claim-anchor-no-url"),
        )

        assert result.ok is False
        assert result.error_count == 1
        assert "financial_disclosure fd-1" in result.issues[0].message

    def test_nonzero_score_card_requires_official_claim_source_anchor(self, tmp_path: Path) -> None:
        card = _card("ec-metadata-only-source").model_copy(
            update={
                "source_anchors": [
                    SourceAnchor(
                        source_type="rule_context",
                        source_id="context-1",
                        url="https://example.com/context",
                        label="Rule context",
                    )
                ]
            }
        )
        _write_card(tmp_path, card)

        result = verify_local_evidence_cards(
            tmp_path,
            _manifest_with_cards("ec-metadata-only-source"),
        )

        assert result.ok is False
        assert result.error_count == 1
        assert "official source" in result.issues[0].message

    def test_nonzero_score_card_rejects_duplicate_source_anchor_keys(
        self,
        tmp_path: Path,
    ) -> None:
        card = _card("ec-duplicate-source-anchor")
        payload = card.model_dump(mode="json")
        payload["source_anchors"].append(dict(payload["source_anchors"][0]))
        path = tmp_path / evidence_path("ec-duplicate-source-anchor")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

        result = verify_local_evidence_cards(
            tmp_path,
            _manifest_with_cards("ec-duplicate-source-anchor"),
        )

        assert result.ok is False
        assert result.error_count == 1
        assert "duplicate source anchors" in result.issues[0].message

    def test_zero_score_card_without_source_anchor_is_allowed(self, tmp_path: Path) -> None:
        card = _card("ec-zero").model_copy(update={"score_delta": 0.0, "source_anchors": []})
        _write_card(tmp_path, card)

        result = verify_local_evidence_cards(tmp_path, _manifest_with_cards("ec-zero"))

        assert result.ok is True


# ---------------------------------------------------------------------------
# Stage metadata
# ---------------------------------------------------------------------------


class TestVerifyLocalEvidenceCardsMetadata:
    def test_stage_name_is_evidence(self, tmp_path: Path) -> None:
        result = verify_local_evidence_cards(tmp_path, _manifest_with_cards())
        assert result.stage == "evidence"

    def test_ok_property_true_with_no_errors(self, tmp_path: Path) -> None:
        card = _card("ec-meta-001")
        _write_card(tmp_path, card)
        result = verify_local_evidence_cards(tmp_path, _manifest_with_cards("ec-meta-001"))
        assert result.ok is True

    def test_error_and_warning_counts(self, tmp_path: Path) -> None:
        # One missing file → one error, zero warnings
        result = verify_local_evidence_cards(tmp_path, _manifest_with_cards("ec-err"))
        assert result.error_count == 1
        assert result.warning_count == 0
