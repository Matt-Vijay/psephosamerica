"""Tests for src/ingest/congress/archive_validate.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ingest.congress.archive import (
    CongressArchive,
    manifest_from_archive,
)
from src.ingest.congress.archive_validate import (
    ArchiveValidationResult,
    MissingFile,
    validate_congress_archive_manifest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_exist(path: Path) -> bool:
    """Fake exists() that reports every path as present."""
    return True


def _none_exist(path: Path) -> bool:
    """Fake exists() that reports every path as absent."""
    return False


def _only(present: set[Path]) -> object:
    """Return an exists() callable that is True only for paths in *present*."""
    def _exists(path: Path) -> bool:
        return path in present
    return _exists


# ---------------------------------------------------------------------------
# Result type invariants
# ---------------------------------------------------------------------------


class TestResultTypes:
    def test_valid_result_has_empty_missing(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        result = validate_congress_archive_manifest(manifest, exists=_all_exist)
        assert result.valid is True
        assert result.missing == ()

    def test_invalid_result_has_missing_files(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        result = validate_congress_archive_manifest(manifest, exists=_none_exist)
        assert result.valid is False
        assert len(result.missing) > 0

    def test_result_is_frozen(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        result = validate_congress_archive_manifest(manifest, exists=_all_exist)
        with pytest.raises(AttributeError):
            result.valid = False  # type: ignore[misc]

    def test_missing_file_is_frozen(self, tmp_path: Path) -> None:
        mf = MissingFile(path=tmp_path / "x.json", label="members")
        with pytest.raises(AttributeError):
            mf.label = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Singleton mandatory files
# ---------------------------------------------------------------------------


class TestMandatorySingletons:
    def test_members_missing_reported(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        present = {manifest.committees.path, manifest.bills.path}
        result = validate_congress_archive_manifest(manifest, exists=_only(present))
        labels = {mf.label for mf in result.missing}
        assert "members" in labels

    def test_committees_missing_reported(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        present = {manifest.members.path, manifest.bills.path}
        result = validate_congress_archive_manifest(manifest, exists=_only(present))
        labels = {mf.label for mf in result.missing}
        assert "committees" in labels

    def test_bills_missing_reported(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        present = {manifest.members.path, manifest.committees.path}
        result = validate_congress_archive_manifest(manifest, exists=_only(present))
        labels = {mf.label for mf in result.missing}
        assert "bills" in labels

    def test_all_singletons_present_valid(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        present = {manifest.members.path, manifest.committees.path, manifest.bills.path}
        result = validate_congress_archive_manifest(manifest, exists=_only(present))
        assert result.valid is True

    def test_missing_file_carries_correct_path(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        result = validate_congress_archive_manifest(manifest, exists=_none_exist)
        paths = {mf.path for mf in result.missing}
        assert manifest.members.path in paths
        assert manifest.committees.path in paths
        assert manifest.bills.path in paths


# ---------------------------------------------------------------------------
# Member detail files
# ---------------------------------------------------------------------------


class TestMemberDetails:
    def _manifest_with_members(self, tmp_path: Path):
        arch = CongressArchive(tmp_path, congress=119)
        return manifest_from_archive(arch, bioguide_ids=["P000197", "S000148"])

    def test_all_member_details_present(self, tmp_path: Path) -> None:
        manifest = self._manifest_with_members(tmp_path)
        present = {src.path for src in manifest.member_details}
        present |= {manifest.members.path, manifest.committees.path, manifest.bills.path}
        result = validate_congress_archive_manifest(manifest, exists=_only(present))
        assert result.valid is True

    def test_one_member_detail_missing(self, tmp_path: Path) -> None:
        manifest = self._manifest_with_members(tmp_path)
        p000197_path = next(
            src.path for src in manifest.member_details if src.bioguide_id == "P000197"
        )
        present = {
            src.path for src in manifest.member_details if src.path != p000197_path
        } | {manifest.members.path, manifest.committees.path, manifest.bills.path}
        result = validate_congress_archive_manifest(manifest, exists=_only(present))
        assert result.valid is False
        labels = {mf.label for mf in result.missing}
        assert "member_details[P000197]" in labels
        assert "member_details[S000148]" not in labels

    def test_member_detail_label_format(self, tmp_path: Path) -> None:
        manifest = self._manifest_with_members(tmp_path)
        result = validate_congress_archive_manifest(manifest, exists=_none_exist)
        labels = {mf.label for mf in result.missing}
        assert "member_details[P000197]" in labels
        assert "member_details[S000148]" in labels


# ---------------------------------------------------------------------------
# Bill detail and cosponsor files
# ---------------------------------------------------------------------------


class TestBillFiles:
    def _manifest_with_bills(self, tmp_path: Path):
        arch = CongressArchive(tmp_path, congress=119)
        return manifest_from_archive(
            arch, bill_keys=[(119, "hr", 1), (119, "s", 42)]
        )

    def test_all_bill_files_present(self, tmp_path: Path) -> None:
        manifest = self._manifest_with_bills(tmp_path)
        present = (
            {src.path for src in manifest.bill_details}
            | {src.path for src in manifest.cosponsors}
            | {manifest.members.path, manifest.committees.path, manifest.bills.path}
        )
        result = validate_congress_archive_manifest(manifest, exists=_only(present))
        assert result.valid is True

    def test_missing_bill_detail_label(self, tmp_path: Path) -> None:
        manifest = self._manifest_with_bills(tmp_path)
        result = validate_congress_archive_manifest(manifest, exists=_none_exist)
        labels = {mf.label for mf in result.missing}
        assert "bill_details[119_hr_1]" in labels
        assert "bill_details[119_s_42]" in labels

    def test_missing_cosponsor_label(self, tmp_path: Path) -> None:
        manifest = self._manifest_with_bills(tmp_path)
        result = validate_congress_archive_manifest(manifest, exists=_none_exist)
        labels = {mf.label for mf in result.missing}
        assert "cosponsors[119_hr_1]" in labels
        assert "cosponsors[119_s_42]" in labels


# ---------------------------------------------------------------------------
# Vote files
# ---------------------------------------------------------------------------


class TestVoteFiles:
    def test_house_votes_missing_reported(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(
            arch, house_vote_keys=[(2025, 42), (2025, 100)]
        )
        result = validate_congress_archive_manifest(manifest, exists=_none_exist)
        labels = {mf.label for mf in result.missing}
        assert "house_votes[2025_0042]" in labels
        assert "house_votes[2025_0100]" in labels

    def test_senate_votes_missing_reported(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(
            arch, senate_vote_keys=[(119, 1, 10), (119, 2, 3)]
        )
        result = validate_congress_archive_manifest(manifest, exists=_none_exist)
        labels = {mf.label for mf in result.missing}
        assert "senate_votes[119_1_00010]" in labels
        assert "senate_votes[119_2_00003]" in labels

    def test_empty_vote_lists_not_in_missing(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        result = validate_congress_archive_manifest(manifest, exists=_none_exist)
        labels = {mf.label for mf in result.missing}
        assert not any(label.startswith("house_votes") for label in labels)
        assert not any(label.startswith("senate_votes") for label in labels)

    def test_vote_files_present_does_not_affect_validity_of_others(
        self, tmp_path: Path
    ) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(
            arch, house_vote_keys=[(2025, 1)]
        )
        # Only singletons present, vote file absent
        present = {manifest.members.path, manifest.committees.path, manifest.bills.path}
        result = validate_congress_archive_manifest(manifest, exists=_only(present))
        assert result.valid is False
        labels = {mf.label for mf in result.missing}
        assert "house_votes[2025_0001]" in labels


# ---------------------------------------------------------------------------
# Missing count and path accuracy
# ---------------------------------------------------------------------------


class TestMissingCount:
    def test_count_matches_absent_files(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(
            arch,
            bill_keys=[(119, "hr", 1)],
            bioguide_ids=["A000001"],
            house_vote_keys=[(2025, 5)],
        )
        # 3 singletons + 1 member + 2 bill files (detail + cosponsor) + 1 vote = 7
        result = validate_congress_archive_manifest(manifest, exists=_none_exist)
        assert len(result.missing) == 7

    def test_partial_presence_counts_correctly(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch, bioguide_ids=["A000001", "B000002"])
        present = {
            manifest.members.path,
            manifest.committees.path,
            manifest.bills.path,
            manifest.member_details[0].path,  # only one of two present
        }
        result = validate_congress_archive_manifest(manifest, exists=_only(present))
        assert len(result.missing) == 1
        assert result.missing[0].label == "member_details[B000002]"

    def test_return_type_is_archive_validation_result(self, tmp_path: Path) -> None:
        arch = CongressArchive(tmp_path, congress=119)
        manifest = manifest_from_archive(arch)
        result = validate_congress_archive_manifest(manifest, exists=_all_exist)
        assert isinstance(result, ArchiveValidationResult)
