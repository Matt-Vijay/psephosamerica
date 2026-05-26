"""Tests for provenance.keys — deterministic storage key builders."""

import pytest
from datetime import date, datetime, timezone

from src.provenance.keys import (
    raw_artifact_key,
    parsed_output_key,
    snapshot_output_key,
    archive_manifest_key,
)


# ---------------------------------------------------------------------------
# raw_artifact_key
# ---------------------------------------------------------------------------


class TestRawArtifactKey:
    VALID_SHA = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"

    def test_basic_structure(self):
        key = raw_artifact_key("congress-api", "2025-06-01", self.VALID_SHA, "members.json")
        assert key == f"raw/congress-api/2025-06-01/{self.VALID_SHA[:8]}/members.json"

    def test_prefix_is_raw(self):
        key = raw_artifact_key("fec-bulk", "2025-01-15", self.VALID_SHA, "cm.zip")
        assert key.startswith("raw/")

    def test_date_object_accepted(self):
        key = raw_artifact_key("fec-bulk", date(2025, 3, 1), self.VALID_SHA, "file.csv")
        assert "2025-03-01" in key

    def test_datetime_object_accepted(self):
        dt = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        key = raw_artifact_key("fec-bulk", dt, self.VALID_SHA, "file.csv")
        assert "2025-06-15" in key

    def test_sha_prefix_is_first_8_chars(self):
        key = raw_artifact_key("congress-api", "2025-06-01", self.VALID_SHA, "x.json")
        parts = key.split("/")
        assert parts[3] == self.VALID_SHA[:8]

    def test_invalid_sha_raises(self):
        with pytest.raises(ValueError):
            raw_artifact_key("congress-api", "2025-06-01", "GHIJKLMN", "x.json")

    def test_short_sha_raises(self):
        with pytest.raises(ValueError):
            raw_artifact_key("congress-api", "2025-06-01", "abc", "x.json")

    def test_unsafe_source_slug_raises(self):
        with pytest.raises(ValueError):
            raw_artifact_key("congress/api", "2025-06-01", self.VALID_SHA, "x.json")

    def test_invalid_date_string_raises(self):
        with pytest.raises(ValueError):
            raw_artifact_key("congress-api", "June 1 2025", self.VALID_SHA, "x.json")

    def test_deterministic(self):
        key1 = raw_artifact_key("efdsearch-senate", "2025-07-04", self.VALID_SHA, "disclosure.pdf")
        key2 = raw_artifact_key("efdsearch-senate", "2025-07-04", self.VALID_SHA, "disclosure.pdf")
        assert key1 == key2


# ---------------------------------------------------------------------------
# parsed_output_key
# ---------------------------------------------------------------------------


class TestParsedOutputKey:
    VALID_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

    def test_basic_structure(self):
        key = parsed_output_key(
            "efdsearch-senate", "disclosure-pdf", "0.3.1", self.VALID_SHA, "holdings.json"
        )
        assert (
            key
            == f"parsed/efdsearch-senate/disclosure-pdf/0.3.1/{self.VALID_SHA[:8]}/holdings.json"
        )

    def test_prefix_is_parsed(self):
        key = parsed_output_key("src", "parser", "1.0.0", self.VALID_SHA, "out.json")
        assert key.startswith("parsed/")

    def test_version_with_dots_accepted(self):
        key = parsed_output_key("src", "parser", "1.2.3", self.VALID_SHA, "out.json")
        assert "1.2.3" in key

    def test_invalid_parser_version_raises(self):
        with pytest.raises(ValueError):
            parsed_output_key("src", "parser", "v1/2", self.VALID_SHA, "out.json")

    def test_invalid_sha_raises(self):
        with pytest.raises(ValueError):
            parsed_output_key("src", "parser", "1.0", "ZZZZZZZZ", "out.json")

    def test_deterministic(self):
        args = ("efdsearch-senate", "disclosure-pdf", "0.3.1", self.VALID_SHA, "holdings.json")
        assert parsed_output_key(*args) == parsed_output_key(*args)


# ---------------------------------------------------------------------------
# snapshot_output_key
# ---------------------------------------------------------------------------


class TestSnapshotOutputKey:
    def test_basic_structure(self):
        key = snapshot_output_key("2025-06-01", "A000001", "profile.json")
        assert key == "snapshots/2025-06-01/A000001/profile.json"

    def test_prefix_is_snapshots(self):
        key = snapshot_output_key("2025-06-01", "B001234", "score.json")
        assert key.startswith("snapshots/")

    def test_bioguide_normalised_to_upper(self):
        key = snapshot_output_key("2025-06-01", "a000001", "profile.json")
        assert "/A000001/" in key

    def test_date_object_accepted(self):
        key = snapshot_output_key(date(2025, 6, 1), "A000001", "profile.json")
        assert "2025-06-01" in key

    def test_invalid_bioguide_raises(self):
        with pytest.raises(ValueError):
            snapshot_output_key("2025-06-01", "INVALID", "profile.json")

    def test_invalid_bioguide_no_digits_raises(self):
        with pytest.raises(ValueError):
            snapshot_output_key("2025-06-01", "ABC123", "profile.json")

    def test_deterministic(self):
        k1 = snapshot_output_key("2025-06-01", "A000001", "profile.json")
        k2 = snapshot_output_key("2025-06-01", "A000001", "profile.json")
        assert k1 == k2


# ---------------------------------------------------------------------------
# archive_manifest_key
# ---------------------------------------------------------------------------


class TestArchiveManifestKey:
    def test_basic_structure(self):
        key = archive_manifest_key("2025-06-01")
        assert key == "archives/2025-06-01/manifest.json"

    def test_prefix_is_archives(self):
        key = archive_manifest_key("2025-01-01")
        assert key.startswith("archives/")

    def test_always_ends_with_manifest_json(self):
        key = archive_manifest_key("2025-12-31")
        assert key.endswith("/manifest.json")

    def test_date_object_accepted(self):
        key = archive_manifest_key(date(2025, 9, 15))
        assert "2025-09-15" in key

    def test_deterministic(self):
        assert archive_manifest_key("2025-06-01") == archive_manifest_key("2025-06-01")

    def test_different_dates_give_different_keys(self):
        assert archive_manifest_key("2025-06-01") != archive_manifest_key("2025-06-02")
