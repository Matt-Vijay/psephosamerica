"""Tests for src/runtime/publish_roundtrip_lookup.py."""

from __future__ import annotations

import json
from pathlib import Path

from src.export.local_store import load_latest_manifest
from src.runtime.publish_roundtrip_lookup import (
    verify_published_current_member_lookup_roundtrip,
)
from tests.support.published_snapshot_fixtures import (
    PublishedSnapshotBuilder,
    make_member_profile,
)


def _manifest(root: Path):
    return load_latest_manifest(root)


class TestVerifyPublishedCurrentMemberLookupRoundtrip:
    def test_ok_for_valid_snapshot(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        result = verify_published_current_member_lookup_roundtrip(
            tmp_path,
            _manifest(tmp_path),
        )
        assert result.ok is True
        assert result.stage == "lookup"
        assert result.checked == 1

    def test_missing_lookup_file_is_error(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        (tmp_path / "identity" / "current-member-lookup.json").unlink()
        result = verify_published_current_member_lookup_roundtrip(
            tmp_path,
            _manifest(tmp_path),
        )
        assert result.ok is False
        assert result.checked == 0
        assert any("lookup artifact missing" in issue.message for issue in result.issues)

    def test_invalid_lookup_file_is_error(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        lookup_file = tmp_path / "identity" / "current-member-lookup.json"
        lookup_file.write_text("{not-json", encoding="utf-8")
        result = verify_published_current_member_lookup_roundtrip(
            tmp_path,
            _manifest(tmp_path),
        )
        assert result.ok is False
        assert result.checked == 0
        assert any("failed to load" in issue.message for issue in result.issues)

    def test_member_mismatch_is_error(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        lookup_file = tmp_path / "identity" / "current-member-lookup.json"
        payload = json.loads(lookup_file.read_text())
        payload["m"][0]["s"] = "wrong-slug"
        lookup_file.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        result = verify_published_current_member_lookup_roundtrip(
            tmp_path,
            _manifest(tmp_path),
        )
        assert result.ok is False
        assert result.checked == 1
        assert any("lookup members mismatch" in issue.message for issue in result.issues)

    def test_missing_supporting_member_profile_is_error(self, tmp_path: Path) -> None:
        profiles = [
            make_member_profile(bioguide_id="A000001", slug="alice-smith", name="Alice Smith"),
            make_member_profile(bioguide_id="B000002", slug="bob-jones", name="Bob Jones"),
        ]
        PublishedSnapshotBuilder(tmp_path).with_member_profiles(profiles).build()
        (tmp_path / "members" / "alice-smith.json").unlink()
        result = verify_published_current_member_lookup_roundtrip(
            tmp_path,
            _manifest(tmp_path),
        )
        assert result.ok is False
        assert result.checked == 1
        assert any("supporting member profile" in issue.message for issue in result.issues)
