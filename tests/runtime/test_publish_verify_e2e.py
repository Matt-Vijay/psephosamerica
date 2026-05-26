"""End-to-end tests for verify_local_publish over real temp publish trees.

Goals
-----
- Call verify_local_publish(root) directly — no subprocesses.
- Use real temp publish trees built by the published-snapshot fixture support.
- Confirm ok=True for well-formed trees and ok=False / error issues for
  deliberately broken ones.
- No network calls.  No database access.  No subprocess invocations.
- Patch nothing; the verifier is a pure filesystem operation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime.publish_verify import verify_local_publish
from src.runtime.publish_verify_types import PublishVerifyResult
from tests.support.published_snapshot_fixtures import (
    make_evidence_card,
    make_member_profile,
    make_snapshot,
    make_zip_feed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_snap(tmp_path: Path, snapshot_id: str = "2026-01-01") -> Path:
    """Write a minimal valid publish tree; return its root."""
    make_snapshot(tmp_path, snapshot_id=snapshot_id)
    return tmp_path


# ---------------------------------------------------------------------------
# Valid trees — should pass
# ---------------------------------------------------------------------------


class TestVerifyLocalPublishValidTree:
    """verify_local_publish returns ok=True for well-formed publish trees."""

    def test_ok_true_for_default_snapshot(self, tmp_path: Path) -> None:
        root = _ok_snap(tmp_path)
        result = verify_local_publish(root)
        assert isinstance(result, PublishVerifyResult)
        assert result.ok is True

    def test_no_errors_for_default_snapshot(self, tmp_path: Path) -> None:
        root = _ok_snap(tmp_path)
        result = verify_local_publish(root)
        assert result.total_errors == 0

    def test_total_checked_positive(self, tmp_path: Path) -> None:
        root = _ok_snap(tmp_path)
        result = verify_local_publish(root)
        # At least one member profile and one evidence card are present by default.
        assert result.total_checked >= 2

    def test_six_stages_present(self, tmp_path: Path) -> None:
        root = _ok_snap(tmp_path)
        result = verify_local_publish(root)
        stage_names = {s.stage for s in result.stages}
        assert {"manifest", "profiles", "evidence", "ontology", "prediction", "zip"} == stage_names

    def test_all_stages_ok_for_valid_tree(self, tmp_path: Path) -> None:
        root = _ok_snap(tmp_path)
        result = verify_local_publish(root)
        for stage in result.stages:
            assert stage.ok is True, f"Stage {stage.stage!r} failed: {stage.issues}"

    def test_multiple_members_ok(self, tmp_path: Path) -> None:
        profiles = [
            make_member_profile(bioguide_id="A000001", slug="alice-smith", name="Alice Smith"),
            make_member_profile(bioguide_id="B000002", slug="bob-jones", name="Bob Jones"),
        ]
        cards = [
            make_evidence_card(
                evidence_card_id="ec-a001",
                member_slug="alice-smith",
                member_bioguide_id="A000001",
                member_name="Alice Smith",
            ),
            make_evidence_card(
                evidence_card_id="ec-b002",
                member_slug="bob-jones",
                member_bioguide_id="B000002",
                member_name="Bob Jones",
            ),
        ]
        make_snapshot(tmp_path, member_profiles=profiles, evidence_cards=cards)
        result = verify_local_publish(tmp_path)
        assert result.ok is True
        assert result.total_errors == 0

    def test_with_zip_feeds_ok(self, tmp_path: Path) -> None:
        feeds = [make_zip_feed(zip_code="94102"), make_zip_feed(zip_code="10001")]
        make_snapshot(tmp_path, zip_feeds=feeds)
        result = verify_local_publish(tmp_path)
        assert result.ok is True

    def test_snapshot_id_preserved(self, tmp_path: Path) -> None:
        snap_id = "2025-06-15"
        make_snapshot(tmp_path, snapshot_id=snap_id)
        result = verify_local_publish(tmp_path)
        assert result.ok is True

    def test_result_is_frozen(self, tmp_path: Path) -> None:
        root = _ok_snap(tmp_path)
        result = verify_local_publish(root)
        with pytest.raises(Exception):
            result.stages = ()  # type: ignore[misc]

    def test_all_issues_empty_for_valid_tree(self, tmp_path: Path) -> None:
        root = _ok_snap(tmp_path)
        result = verify_local_publish(root)
        assert result.all_issues() == []


# ---------------------------------------------------------------------------
# Missing member profile file
# ---------------------------------------------------------------------------


class TestVerifyMissingMemberProfile:
    """Deleting a member profile file after writing yields profile-stage errors."""

    def test_missing_profile_gives_error(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        # Delete the member profile that the manifest references.
        member_files = list((tmp_path / "members").glob("*.json"))
        assert member_files, "Expected at least one member profile file"
        member_files[0].unlink()

        result = verify_local_publish(tmp_path)
        assert result.ok is False

    def test_missing_profile_error_in_profiles_stage(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        member_files = list((tmp_path / "members").glob("*.json"))
        member_files[0].unlink()

        result = verify_local_publish(tmp_path)
        profiles_stage = result.stage_result("profiles")
        assert profiles_stage is not None
        assert not profiles_stage.ok
        error_msgs = [i.message for i in profiles_stage.issues if i.severity == "error"]
        assert any("missing" in m or "load failed" in m for m in error_msgs), error_msgs


# ---------------------------------------------------------------------------
# Corrupt member profile content
# ---------------------------------------------------------------------------


class TestVerifyCorruptMemberProfile:
    """Overwriting a profile file with garbage content yields a profile-stage error."""

    def test_corrupt_profile_gives_error(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        member_files = list((tmp_path / "members").glob("*.json"))
        assert member_files
        member_files[0].write_bytes(b"not valid json {{{")

        result = verify_local_publish(tmp_path)
        assert result.ok is False

    def test_corrupt_profile_manifest_hash_mismatch(self, tmp_path: Path) -> None:
        """Manifest stage detects hash mismatch when profile content is replaced."""
        make_snapshot(tmp_path)
        member_files = list((tmp_path / "members").glob("*.json"))
        member_files[0].write_bytes(b'{"tampered": true}')

        result = verify_local_publish(tmp_path)
        # Manifest or profiles stage must raise an error.
        assert result.ok is False
        assert result.total_errors >= 1

    def test_corrupt_profile_fails_manifest_stage(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        member_files = list((tmp_path / "members").glob("*.json"))
        member_files[0].write_bytes(b'{"tampered": true}')

        result = verify_local_publish(tmp_path)

        manifest_stage = result.stage_result("manifest")
        assert manifest_stage is not None
        assert manifest_stage.ok is False
        assert any("sha256 mismatch" in issue.message for issue in manifest_stage.issues)


# ---------------------------------------------------------------------------
# Missing evidence card file
# ---------------------------------------------------------------------------


class TestVerifyMissingEvidenceCard:
    """Deleting an evidence card file yields an evidence-stage error."""

    def test_missing_evidence_card_gives_error(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        ev_files = list((tmp_path / "evidence").glob("*.json"))
        assert ev_files, "Expected at least one evidence file"
        ev_files[0].unlink()

        result = verify_local_publish(tmp_path)
        assert result.ok is False

    def test_missing_evidence_error_in_evidence_stage(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        ev_files = list((tmp_path / "evidence").glob("*.json"))
        ev_files[0].unlink()

        result = verify_local_publish(tmp_path)
        ev_stage = result.stage_result("evidence")
        assert ev_stage is not None
        assert not ev_stage.ok


# ---------------------------------------------------------------------------
# Corrupt evidence card content
# ---------------------------------------------------------------------------


class TestVerifyCorruptEvidenceCard:
    """Overwriting an evidence card file yields an evidence-stage error."""

    def test_corrupt_evidence_card_gives_error(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        ev_files = list((tmp_path / "evidence").glob("*.json"))
        assert ev_files
        ev_files[0].write_bytes(b"not valid json")

        result = verify_local_publish(tmp_path)
        assert result.ok is False

    def test_evidence_card_id_mismatch_gives_error(self, tmp_path: Path) -> None:
        """Replacing evidence card content with wrong id gives error."""
        cards = [
            make_evidence_card(evidence_card_id="ec-original"),
        ]
        profiles = [make_member_profile()]
        make_snapshot(tmp_path, evidence_cards=cards, member_profiles=profiles)

        ev_file = tmp_path / "evidence" / "ec-original.json"
        assert ev_file.exists()
        data = json.loads(ev_file.read_bytes())
        data["evidence_card_id"] = "ec-different"
        ev_file.write_bytes(json.dumps(data).encode("utf-8"))

        result = verify_local_publish(tmp_path)
        assert result.ok is False


# ---------------------------------------------------------------------------
# Missing manifest
# ---------------------------------------------------------------------------


class TestVerifyMissingManifest:
    """A publish tree with no manifest fails at the manifest stage."""

    def test_missing_manifest_fails(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path, snapshot_id="2026-01-01")
        # Remove the manifest.
        snap_dir = tmp_path / "snapshots" / "2026-01-01"
        manifest_file = snap_dir / "manifest.json"
        if manifest_file.exists():
            manifest_file.unlink()

        result = verify_local_publish(tmp_path)
        # Manifest stage should report an error.
        assert result.ok is False

    def test_empty_root_fails(self, tmp_path: Path) -> None:
        empty_root = tmp_path / "empty"
        empty_root.mkdir()
        result = verify_local_publish(empty_root)
        assert result.ok is False


class TestVerifyUnmanifestedManagedFiles:
    def test_extra_member_file_fails_manifest_stage(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        extra = tmp_path / "members" / "unlisted-member.json"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_bytes(b'{"member":"extra"}')

        result = verify_local_publish(tmp_path)

        manifest_stage = result.stage_result("manifest")
        assert manifest_stage is not None
        assert manifest_stage.ok is False
        assert any("not listed in manifest" in issue.message for issue in manifest_stage.issues)

    def test_homepage_file_warns_without_failing_local_manifest_coverage(
        self, tmp_path: Path
    ) -> None:
        make_snapshot(tmp_path)
        homepage = tmp_path / "homepage" / "feed.json"
        homepage.parent.mkdir(parents=True, exist_ok=True)
        homepage.write_text('{"items":[]}', encoding="utf-8")

        result = verify_local_publish(tmp_path)

        manifest_stage = result.stage_result("manifest")
        assert manifest_stage is not None
        assert manifest_stage.ok is True
        assert result.ok is True
        assert any(issue.severity == "warning" for issue in manifest_stage.issues)
        assert any(issue.path == "homepage/feed.json" for issue in manifest_stage.issues)


# ---------------------------------------------------------------------------
# Zip feed verification
# ---------------------------------------------------------------------------


class TestVerifyZipFeeds:
    """Zip-feed stage verifies optional zip files."""

    def test_no_zip_feeds_passes(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path, zip_feeds=[])
        result = verify_local_publish(tmp_path)
        assert result.ok is True

    def test_zip_stage_checked_zero_when_no_feeds(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path, zip_feeds=[])
        result = verify_local_publish(tmp_path)
        zip_stage = result.stage_result("zip")
        assert zip_stage is not None
        assert zip_stage.checked == 0

    def test_valid_zip_feeds_pass(self, tmp_path: Path) -> None:
        feeds = [make_zip_feed(zip_code="90210")]
        make_snapshot(tmp_path, zip_feeds=feeds)
        result = verify_local_publish(tmp_path)
        assert result.ok is True

    def test_corrupt_zip_feed_fails(self, tmp_path: Path) -> None:
        feeds = [make_zip_feed(zip_code="90210")]
        make_snapshot(tmp_path, zip_feeds=feeds)
        zip_file = tmp_path / "zip" / "90210.json"
        assert zip_file.exists()
        zip_file.write_bytes(b"not valid json <<<")
        result = verify_local_publish(tmp_path)
        assert result.ok is False


# ---------------------------------------------------------------------------
# Result shape contracts
# ---------------------------------------------------------------------------


class TestVerifyResultShape:
    """PublishVerifyResult exposes the expected aggregated properties."""

    def test_stage_result_by_name(self, tmp_path: Path) -> None:
        root = _ok_snap(tmp_path)
        result = verify_local_publish(root)
        for name in ("manifest", "profiles", "evidence", "ontology", "zip"):
            stage = result.stage_result(name)
            assert stage is not None, f"stage {name!r} missing from result"

    def test_total_checked_sum_of_stages(self, tmp_path: Path) -> None:
        root = _ok_snap(tmp_path)
        result = verify_local_publish(root)
        assert result.total_checked == sum(s.checked for s in result.stages)

    def test_total_errors_zero_for_valid_tree(self, tmp_path: Path) -> None:
        root = _ok_snap(tmp_path)
        result = verify_local_publish(root)
        assert result.total_errors == 0

    def test_total_warnings_zero_for_valid_tree(self, tmp_path: Path) -> None:
        root = _ok_snap(tmp_path)
        result = verify_local_publish(root)
        assert result.total_warnings == 0
