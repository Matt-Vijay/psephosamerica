"""Tests for src/runtime/publish_roundtrip_evidence.py.

Uses real temp publish trees written via plan_snapshot + write_planned_files.
Patches only fetch_all_evidence_card_rows as the irreducible DB boundary.
Compares typed EvidenceCardPayload values — not raw JSON bytes.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import patch

from src.export.contracts import (
    EvidenceCardPayload,
)
from src.export.filesystem import write_planned_files
from src.export.manifest import ManifestEntry, SnapshotManifest, manifest_root_sha256
from src.export.writer import evidence_path, plan_snapshot, serialize_payload
from src.query.evidence_card import assemble_evidence_card
from src.runtime.publish_roundtrip_evidence import verify_published_evidence_roundtrip
from src.runtime.publish_roundtrip_types import PublishRoundtripStageResult

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_UTC = dt.timezone.utc
_RENDERED_AT = dt.datetime(2025, 4, 1, 10, 0, 0, tzinfo=_UTC)
_CREATED_AT = dt.datetime(2025, 4, 1, 10, 0, 0, tzinfo=_UTC)
_SNAPSHOT_ID = "2025-04-01"

_PATCH_TARGET = "src.runtime.publish_roundtrip_evidence.fetch_all_evidence_card_rows"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_row(
    public_id: str = "ec-001",
    member_bioguide_id: str = "A000001",
    *,
    score_delta: float = -2.5,
    short_explanation: str = "Traded within committee oversight window.",
    confidence_label: str = "HIGH",
    facts: object = None,
    inferences: object = None,
    normative_judgments: object = None,
    rule_version: int = 1,
    **extra,
) -> dict:
    """Return a dict in the shape returned by fetch_all_evidence_card_rows."""
    row: dict = {
        "public_id": public_id,
        "member_bioguide_id": member_bioguide_id,
        "member_full_name": "Jane Smith",
        "member_slug": "jane-smith-a000001",
        "dimension": "conflict_of_interest_risk",
        "rule_id": "coi.committee_sector_trade.v1",
        "rule_version": rule_version,
        "score_delta": score_delta,
        "short_explanation": short_explanation,
        "facts": facts if facts is not None else ["Senator held shares in ACME Corp."],
        "inferences": inferences if inferences is not None else ["Trade within 5 days of hearing."],
        "normative_judgments": normative_judgments,
        "source_anchors": [
            {
                "source_type": "financial_disclosure",
                "source_id": "fd-001",
                "url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/001.pdf",
                "label": "Disclosure 2024",
            }
        ],
        "confidence_label": confidence_label,
        "rendered_at": _RENDERED_AT,
        "created_at": _CREATED_AT,
    }
    row.update(extra)
    return row


def _write_publish_tree(
    tmp_path: Path,
    cards: list[EvidenceCardPayload],
    snapshot_id: str = _SNAPSHOT_ID,
) -> SnapshotManifest:
    """Write cards to a real temp publish tree and return the loaded manifest."""
    from src.export.filesystem import read_manifest

    planned = plan_snapshot(
        snapshot_id=snapshot_id,
        member_profiles=[],
        zip_feeds=[],
        evidence_cards=cards,
    )
    write_planned_files(planned, tmp_path)
    manifest_file = tmp_path / f"snapshots/{snapshot_id}/manifest.json"
    return read_manifest(manifest_file)


def _manifest_with_ids(*evidence_card_ids: str) -> SnapshotManifest:
    """Build a SnapshotManifest listing the given evidence card IDs."""
    entries = [
        ManifestEntry(path=evidence_path(eid), sha256="a" * 64, size_bytes=1)
        for eid in evidence_card_ids
    ]
    return SnapshotManifest(
        snapshot_id=_SNAPSHOT_ID,
        created_at=_CREATED_AT,
        entries=entries,
        total_files=len(entries),
        total_bytes=len(entries),
        root_sha256=manifest_root_sha256(entries),
    )


# ---------------------------------------------------------------------------
# Stage result basics
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_returns_stage_result_instance(self, tmp_path: Path) -> None:
        row = _db_row()
        card = assemble_evidence_card(row)
        manifest = _write_publish_tree(tmp_path, [card])

        with patch(_PATCH_TARGET, return_value=[row]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert isinstance(result, PublishRoundtripStageResult)

    def test_stage_name_is_evidence(self, tmp_path: Path) -> None:
        manifest = _write_publish_tree(tmp_path, [])

        with patch(_PATCH_TARGET, return_value=[]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.stage == "evidence"

    def test_empty_manifest_zero_checked(self, tmp_path: Path) -> None:
        manifest = _write_publish_tree(tmp_path, [])

        with patch(_PATCH_TARGET, return_value=[]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.checked == 0
        assert result.ok is True

    def test_non_evidence_entries_are_skipped(self, tmp_path: Path) -> None:
        """Manifest entries for members/ and zip/ must not be counted."""
        entries = [
            ManifestEntry(path="members/jane-smith.json", sha256="a" * 64, size_bytes=1),
            ManifestEntry(path="zip/12345.json", sha256="b" * 64, size_bytes=1),
        ]
        manifest = SnapshotManifest(
            snapshot_id=_SNAPSHOT_ID,
            created_at=_CREATED_AT,
            entries=entries,
            total_files=2,
            total_bytes=2,
            root_sha256=manifest_root_sha256(entries),
        )

        with patch(_PATCH_TARGET, return_value=[]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.checked == 0
        assert result.ok is True


# ---------------------------------------------------------------------------
# Clean roundtrip
# ---------------------------------------------------------------------------


class TestCleanRoundtrip:
    def test_single_card_matches(self, tmp_path: Path) -> None:
        row = _db_row("ec-001")
        card = assemble_evidence_card(row)
        manifest = _write_publish_tree(tmp_path, [card])

        with patch(_PATCH_TARGET, return_value=[row]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.ok is True
        assert result.checked == 1
        assert result.error_count == 0

    def test_multiple_cards_all_match(self, tmp_path: Path) -> None:
        rows = [
            _db_row("ec-001"),
            _db_row(
                "ec-002",
                member_bioguide_id="B000002",
                member_full_name="Bob Jones",
                member_slug="bob-jones-b000002",
            ),
            _db_row("ec-003", score_delta=-1.0),
        ]
        cards = [assemble_evidence_card(r) for r in rows]
        manifest = _write_publish_tree(tmp_path, cards)

        with patch(_PATCH_TARGET, return_value=rows):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.ok is True
        assert result.checked == 3

    def test_card_with_no_blocks_matches(self, tmp_path: Path) -> None:
        row = _db_row(
            "ec-bare",
            score_delta=0.0,
            facts=None,
            inferences=None,
            normative_judgments=None,
        )
        card = assemble_evidence_card(row)
        manifest = _write_publish_tree(tmp_path, [card])

        with patch(_PATCH_TARGET, return_value=[row]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.ok is True

    def test_db_row_missing_required_source_anchors_reports_issue(self, tmp_path: Path) -> None:
        original = _db_row("ec-no-anchor")
        card = assemble_evidence_card(original)
        manifest = _write_publish_tree(tmp_path, [card])
        altered = {**original, "source_anchors": []}

        with patch(_PATCH_TARGET, return_value=[altered]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.ok is False
        assert result.error_count == 1
        assert "source anchor" in result.issues[0].message

    def test_card_with_dict_facts_matches(self, tmp_path: Path) -> None:
        row = _db_row(
            "ec-dict-facts",
            facts={"f1": "Held ACME Corp shares.", "f2": "Sold after hearing."},
            inferences=None,
        )
        card = assemble_evidence_card(row)
        manifest = _write_publish_tree(tmp_path, [card])

        with patch(_PATCH_TARGET, return_value=[row]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.ok is True

    def test_extra_db_rows_not_in_manifest_are_ignored(self, tmp_path: Path) -> None:
        row_a = _db_row("ec-001")
        row_b = _db_row("ec-extra")  # not in manifest
        card = assemble_evidence_card(row_a)
        manifest = _write_publish_tree(tmp_path, [card])

        with patch(_PATCH_TARGET, return_value=[row_a, row_b]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.ok is True
        assert result.checked == 1


# ---------------------------------------------------------------------------
# Missing published artifact
# ---------------------------------------------------------------------------


class TestMissingPublishedFile:
    def test_missing_file_reports_error(self, tmp_path: Path) -> None:
        row = _db_row("ec-gone")
        card = assemble_evidence_card(row)
        manifest = _write_publish_tree(tmp_path, [card])
        # Remove the published file after writing
        (tmp_path / "evidence" / "ec-gone.json").unlink()

        with patch(_PATCH_TARGET, return_value=[row]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.ok is False
        assert result.error_count == 1

    def test_missing_file_issue_references_path(self, tmp_path: Path) -> None:
        row = _db_row("ec-absent")
        card = assemble_evidence_card(row)
        manifest = _write_publish_tree(tmp_path, [card])
        (tmp_path / "evidence" / "ec-absent.json").unlink()

        with patch(_PATCH_TARGET, return_value=[row]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.issues[0].path == "evidence/ec-absent.json"

    def test_missing_file_issue_mentions_card_id(self, tmp_path: Path) -> None:
        row = _db_row("ec-xyz")
        card = assemble_evidence_card(row)
        manifest = _write_publish_tree(tmp_path, [card])
        (tmp_path / "evidence" / "ec-xyz.json").unlink()

        with patch(_PATCH_TARGET, return_value=[row]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert "ec-xyz" in result.issues[0].message


# ---------------------------------------------------------------------------
# Missing DB row
# ---------------------------------------------------------------------------


class TestCorruptPublishedFile:
    def test_corrupt_schema_is_typed_error(self, tmp_path: Path) -> None:
        """Published evidence card with invalid schema becomes a typed issue."""
        dest = tmp_path / "evidence" / "ec-corrupt.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b'{"evidence_card_id": "ec-corrupt"}')  # valid JSON, invalid schema

        manifest = _manifest_with_ids("ec-corrupt")

        with patch(_PATCH_TARGET, return_value=[]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.ok is False
        assert result.checked == 1
        assert result.error_count == 1
        assert any("ec-corrupt" in i.message for i in result.issues)


class TestMissingDbRow:
    def test_id_in_manifest_not_in_db_reports_error(self, tmp_path: Path) -> None:
        row = _db_row("ec-001")
        card = assemble_evidence_card(row)
        manifest = _write_publish_tree(tmp_path, [card])

        with patch(_PATCH_TARGET, return_value=[]):  # DB empty
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.ok is False
        assert result.error_count == 1

    def test_missing_db_issue_mentions_card_id(self, tmp_path: Path) -> None:
        row = _db_row("ec-nodb")
        card = assemble_evidence_card(row)
        manifest = _write_publish_tree(tmp_path, [card])

        with patch(_PATCH_TARGET, return_value=[]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert "ec-nodb" in result.issues[0].message

    def test_missing_db_issue_is_error_severity(self, tmp_path: Path) -> None:
        row = _db_row("ec-001")
        card = assemble_evidence_card(row)
        manifest = _write_publish_tree(tmp_path, [card])

        with patch(_PATCH_TARGET, return_value=[]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.issues[0].severity == "error"


# ---------------------------------------------------------------------------
# Payload mismatches
# ---------------------------------------------------------------------------


class TestPayloadMismatch:
    def _setup_mismatch(
        self,
        tmp_path: Path,
        original_row: dict,
        altered_row: dict,
    ) -> PublishRoundtripStageResult:
        """Write the original card, then verify against the altered DB row."""
        card = assemble_evidence_card(original_row)
        manifest = _write_publish_tree(tmp_path, [card])
        with patch(_PATCH_TARGET, return_value=[altered_row]):
            return verify_published_evidence_roundtrip(object(), tmp_path, manifest)

    def test_score_delta_mismatch_detected(self, tmp_path: Path) -> None:
        original = _db_row("ec-001", score_delta=-2.5)
        altered = _db_row("ec-001", score_delta=-9.9)
        result = self._setup_mismatch(tmp_path, original, altered)
        assert result.ok is False
        assert result.error_count >= 1

    def test_short_explanation_mismatch_detected(self, tmp_path: Path) -> None:
        original = _db_row("ec-001", short_explanation="Original explanation.")
        altered = _db_row("ec-001", short_explanation="Different explanation.")
        result = self._setup_mismatch(tmp_path, original, altered)
        assert result.ok is False

    def test_mismatch_issue_names_differing_field(self, tmp_path: Path) -> None:
        original = _db_row("ec-001", score_delta=-2.5)
        altered = _db_row("ec-001", score_delta=-99.0)
        result = self._setup_mismatch(tmp_path, original, altered)
        messages = " ".join(i.message for i in result.issues)
        assert "score_delta" in messages

    def test_mismatch_issue_includes_path(self, tmp_path: Path) -> None:
        original = _db_row("ec-001", score_delta=-2.5)
        altered = _db_row("ec-001", score_delta=-99.0)
        result = self._setup_mismatch(tmp_path, original, altered)
        assert result.issues[0].path == "evidence/ec-001.json"

    def test_confidence_mismatch_detected(self, tmp_path: Path) -> None:
        original = _db_row("ec-001", confidence_label="HIGH")
        altered = _db_row("ec-001", confidence_label="LOW")
        result = self._setup_mismatch(tmp_path, original, altered)
        assert result.ok is False

    def test_member_bioguide_mismatch_detected(self, tmp_path: Path) -> None:
        original = _db_row("ec-001", member_bioguide_id="A000001")
        altered = _db_row("ec-001", member_bioguide_id="Z999999")
        result = self._setup_mismatch(tmp_path, original, altered)
        assert result.ok is False

    def test_blocks_mismatch_detected(self, tmp_path: Path) -> None:
        original = _db_row("ec-001", facts=["Original fact."])
        altered = _db_row("ec-001", facts=["Totally different fact."])
        result = self._setup_mismatch(tmp_path, original, altered)
        assert result.ok is False

    def test_multiple_mismatches_all_reported(self, tmp_path: Path) -> None:
        original = _db_row("ec-001", score_delta=-2.5, short_explanation="A.")
        altered = _db_row("ec-001", score_delta=-99.0, short_explanation="B.")
        result = self._setup_mismatch(tmp_path, original, altered)
        assert result.error_count >= 2

    def test_matching_card_has_no_issues(self, tmp_path: Path) -> None:
        row = _db_row("ec-001")
        result = self._setup_mismatch(tmp_path, row, row)
        assert result.ok is True
        assert result.error_count == 0


# ---------------------------------------------------------------------------
# checked count
# ---------------------------------------------------------------------------


class TestCheckedCount:
    def test_checked_equals_evidence_entries_in_manifest(self, tmp_path: Path) -> None:
        rows = [_db_row(f"ec-{i:03d}") for i in range(5)]
        cards = [assemble_evidence_card(r) for r in rows]
        manifest = _write_publish_tree(tmp_path, cards)

        with patch(_PATCH_TARGET, return_value=rows):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.checked == 5

    def test_manifest_with_mixed_entries_counts_only_evidence(self, tmp_path: Path) -> None:
        row = _db_row("ec-001")
        card = assemble_evidence_card(row)
        # Build manifest manually with one evidence entry + one member entry
        evidence_entry = ManifestEntry(path=evidence_path("ec-001"), sha256="a" * 64, size_bytes=1)
        member_entry = ManifestEntry(path="members/jane-smith.json", sha256="b" * 64, size_bytes=1)
        manifest = SnapshotManifest(
            snapshot_id=_SNAPSHOT_ID,
            created_at=_CREATED_AT,
            entries=[evidence_entry, member_entry],
            total_files=2,
            total_bytes=2,
            root_sha256=manifest_root_sha256([evidence_entry, member_entry]),
        )
        # Write the evidence file to disk so load_evidence_card succeeds
        dest = tmp_path / "evidence" / "ec-001.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(serialize_payload(card))

        with patch(_PATCH_TARGET, return_value=[row]):
            result = verify_published_evidence_roundtrip(object(), tmp_path, manifest)

        assert result.checked == 1
