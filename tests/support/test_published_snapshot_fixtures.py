"""Tests for tests/support/published_snapshot_fixtures.py.

Validates that:
- PublishedSnapshotBuilder writes the expected publish-tree layout.
- make_snapshot() produces a readable, correctly-rooted PublishedSnapshot.
- manifest.json covers all data files with correct hashes and counts.
- member profile files and evidence card files round-trip via local_store.
- Optional zip feed files are written when provided and absent otherwise.
- Overrides replace defaults correctly.
- Helper factories (make_evidence_card, make_member_profile, make_zip_feed)
  produce valid payloads.

No network calls; all I/O is scoped to pytest's tmp_path.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.export.contracts import (
    EvidenceCardPayload,
    MemberProfilePayload,
    ZipFeedPayload,
)
from src.export.filesystem import verify_written_files
from src.export.local_store import (
    load_evidence_card,
    load_manifest,
    load_member_profile,
    load_zip_feed,
)
from src.export.writer import evidence_path, manifest_path, member_path, zip_path
from tests.support.published_snapshot_fixtures import (
    PublishedSnapshot,
    PublishedSnapshotBuilder,
    make_evidence_card,
    make_member_profile,
    make_snapshot,
    make_zip_feed,
)


# ---------------------------------------------------------------------------
# PublishedSnapshotBuilder — file layout
# ---------------------------------------------------------------------------


class TestPublishedSnapshotBuilderLayout:
    def test_manifest_file_exists(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        assert (tmp_path / manifest_path("2026-01-01")).is_file()

    def test_default_member_profile_file_exists(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        assert (tmp_path / member_path("nancy-pelosi")).is_file()

    def test_default_evidence_card_file_exists(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        assert (tmp_path / evidence_path("ec-0001")).is_file()

    def test_no_zip_feeds_by_default(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        assert not (tmp_path / "zip").exists()

    def test_zip_feed_file_written_when_provided(self, tmp_path: Path) -> None:
        feed = make_zip_feed(zip_code="94102")
        PublishedSnapshotBuilder(tmp_path).with_zip_feeds([feed]).build()
        assert (tmp_path / zip_path("94102")).is_file()

    def test_members_directory_created(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        assert (tmp_path / "members").is_dir()

    def test_evidence_directory_created(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        assert (tmp_path / "evidence").is_dir()

    def test_snapshots_directory_created(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        assert (tmp_path / "snapshots" / "2026-01-01").is_dir()


# ---------------------------------------------------------------------------
# PublishedSnapshotBuilder — data overrides
# ---------------------------------------------------------------------------


class TestPublishedSnapshotBuilderOverrides:
    def test_with_member_profiles_replaces_default(self, tmp_path: Path) -> None:
        profile = make_member_profile(slug="chuck-schumer", bioguide_id="S000148")
        PublishedSnapshotBuilder(tmp_path).with_member_profiles([profile]).build()
        assert (tmp_path / member_path("chuck-schumer")).is_file()
        assert not (tmp_path / member_path("nancy-pelosi")).is_file()

    def test_with_evidence_cards_replaces_default(self, tmp_path: Path) -> None:
        card = make_evidence_card(evidence_card_id="ec-9999")
        PublishedSnapshotBuilder(tmp_path).with_evidence_cards([card]).build()
        assert (tmp_path / evidence_path("ec-9999")).is_file()
        assert not (tmp_path / evidence_path("ec-0001")).is_file()

    def test_no_zip_feeds_clears_zip_feeds(self, tmp_path: Path) -> None:
        feed = make_zip_feed(zip_code="10001")
        builder = PublishedSnapshotBuilder(tmp_path)
        builder.with_zip_feeds([feed]).no_zip_feeds().build()
        assert not (tmp_path / "zip").exists()

    def test_multiple_member_profiles_written(self, tmp_path: Path) -> None:
        profiles = [
            make_member_profile(slug="member-a", bioguide_id="A000001"),
            make_member_profile(slug="member-b", bioguide_id="B000002"),
        ]
        PublishedSnapshotBuilder(tmp_path).with_member_profiles(profiles).build()
        assert (tmp_path / member_path("member-a")).is_file()
        assert (tmp_path / member_path("member-b")).is_file()

    def test_multiple_evidence_cards_written(self, tmp_path: Path) -> None:
        cards = [
            make_evidence_card(evidence_card_id="ec-0001"),
            make_evidence_card(evidence_card_id="ec-0002"),
        ]
        PublishedSnapshotBuilder(tmp_path).with_evidence_cards(cards).build()
        assert (tmp_path / evidence_path("ec-0001")).is_file()
        assert (tmp_path / evidence_path("ec-0002")).is_file()

    def test_multiple_zip_feeds_written(self, tmp_path: Path) -> None:
        feeds = [make_zip_feed(zip_code="94102"), make_zip_feed(zip_code="10001")]
        PublishedSnapshotBuilder(tmp_path).with_zip_feeds(feeds).build()
        assert (tmp_path / zip_path("94102")).is_file()
        assert (tmp_path / zip_path("10001")).is_file()


# ---------------------------------------------------------------------------
# PublishedSnapshotBuilder — round-trip via local_store
# ---------------------------------------------------------------------------


class TestPublishedSnapshotBuilderRoundTrip:
    def test_member_profile_roundtrip(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        profile = load_member_profile(tmp_path, "nancy-pelosi")
        assert isinstance(profile, MemberProfilePayload)
        assert profile.bioguide_id == "P000197"
        assert profile.slug == "nancy-pelosi"

    def test_evidence_card_roundtrip(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        card = load_evidence_card(tmp_path, "ec-0001")
        assert isinstance(card, EvidenceCardPayload)
        assert card.evidence_card_id == "ec-0001"
        assert card.member_bioguide_id == "P000197"

    def test_zip_feed_roundtrip(self, tmp_path: Path) -> None:
        feed = make_zip_feed(zip_code="94102")
        PublishedSnapshotBuilder(tmp_path).with_zip_feeds([feed]).build()
        loaded = load_zip_feed(tmp_path, "94102")
        assert isinstance(loaded, ZipFeedPayload)
        assert loaded.zip_code == "94102"

    def test_manifest_roundtrip(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        manifest = load_manifest(tmp_path, "2026-01-01")
        assert manifest.snapshot_id == "2026-01-01"
        assert manifest.verify_counts()

    def test_manifest_covers_member_file(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        manifest = load_manifest(tmp_path, "2026-01-01")
        paths = {e.path for e in manifest.entries}
        assert member_path("nancy-pelosi") in paths

    def test_manifest_covers_evidence_file(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        manifest = load_manifest(tmp_path, "2026-01-01")
        paths = {e.path for e in manifest.entries}
        assert evidence_path("ec-0001") in paths

    def test_manifest_covers_zip_file(self, tmp_path: Path) -> None:
        feed = make_zip_feed(zip_code="94102")
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").with_zip_feeds([feed]).build()
        manifest = load_manifest(tmp_path, "2026-01-01")
        paths = {e.path for e in manifest.entries}
        assert zip_path("94102") in paths

    def test_manifest_does_not_include_itself(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        manifest = load_manifest(tmp_path, "2026-01-01")
        paths = {e.path for e in manifest.entries}
        assert manifest_path("2026-01-01") not in paths

    def test_verify_written_files_passes(self, tmp_path: Path) -> None:
        from src.export.writer import plan_snapshot

        profile = make_member_profile()
        card = make_evidence_card()
        planned = plan_snapshot(
            snapshot_id="2026-01-01",
            member_profiles=[profile],
            zip_feeds=[],
            evidence_cards=[card],
        )
        from src.export.filesystem import write_planned_files

        write_planned_files(planned, tmp_path)
        failures = verify_written_files(planned, tmp_path)
        assert failures == []

    def test_manifest_total_files_matches_entries(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        manifest = load_manifest(tmp_path, "2026-01-01")
        assert manifest.total_files == len(manifest.entries)

    def test_manifest_total_bytes_matches_entries(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01").build()
        manifest = load_manifest(tmp_path, "2026-01-01")
        assert manifest.total_bytes == sum(e.size_bytes for e in manifest.entries)

    def test_member_profile_json_is_valid_json(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        raw = (tmp_path / member_path("nancy-pelosi")).read_bytes()
        data = json.loads(raw)
        assert "bioguide_id" in data

    def test_evidence_card_json_is_valid_json(self, tmp_path: Path) -> None:
        PublishedSnapshotBuilder(tmp_path).build()
        raw = (tmp_path / evidence_path("ec-0001")).read_bytes()
        data = json.loads(raw)
        assert "evidence_card_id" in data


# ---------------------------------------------------------------------------
# PublishedSnapshotBuilder — snapshot() result type
# ---------------------------------------------------------------------------


class TestPublishedSnapshotBuilderResult:
    def test_snapshot_returns_published_snapshot(self, tmp_path: Path) -> None:
        builder = PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01")
        builder.build()
        snap = builder.snapshot()
        assert isinstance(snap, PublishedSnapshot)

    def test_snapshot_root_attribute(self, tmp_path: Path) -> None:
        builder = PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-01-01")
        builder.build()
        snap = builder.snapshot()
        assert snap.root == tmp_path

    def test_snapshot_id_attribute(self, tmp_path: Path) -> None:
        builder = PublishedSnapshotBuilder(tmp_path, snapshot_id="2026-03-01")
        builder.build()
        snap = builder.snapshot()
        assert snap.snapshot_id == "2026-03-01"


# ---------------------------------------------------------------------------
# make_snapshot — convenience wrapper
# ---------------------------------------------------------------------------


class TestMakeSnapshot:
    def test_returns_published_snapshot(self, tmp_path: Path) -> None:
        snap = make_snapshot(tmp_path)
        assert isinstance(snap, PublishedSnapshot)

    def test_default_snapshot_id(self, tmp_path: Path) -> None:
        snap = make_snapshot(tmp_path, snapshot_id="2026-01-01")
        assert snap.snapshot_id == "2026-01-01"

    def test_root_is_tmp_path(self, tmp_path: Path) -> None:
        snap = make_snapshot(tmp_path)
        assert snap.root == tmp_path

    def test_manifest_exists_by_default(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path, snapshot_id="2026-01-01")
        assert (tmp_path / manifest_path("2026-01-01")).is_file()

    def test_default_member_profile_exists(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        assert (tmp_path / member_path("nancy-pelosi")).is_file()

    def test_default_evidence_card_exists(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        assert (tmp_path / evidence_path("ec-0001")).is_file()

    def test_no_zip_feeds_by_default(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path)
        assert not (tmp_path / "zip").exists()

    def test_custom_member_profiles(self, tmp_path: Path) -> None:
        profile = make_member_profile(slug="test-member", bioguide_id="T000001")
        make_snapshot(tmp_path, member_profiles=[profile])
        assert (tmp_path / member_path("test-member")).is_file()
        assert not (tmp_path / member_path("nancy-pelosi")).is_file()

    def test_custom_evidence_cards(self, tmp_path: Path) -> None:
        card = make_evidence_card(evidence_card_id="ec-custom")
        make_snapshot(tmp_path, evidence_cards=[card])
        assert (tmp_path / evidence_path("ec-custom")).is_file()
        assert not (tmp_path / evidence_path("ec-0001")).is_file()

    def test_zip_feeds_written_when_provided(self, tmp_path: Path) -> None:
        feed = make_zip_feed(zip_code="90210")
        make_snapshot(tmp_path, zip_feeds=[feed])
        assert (tmp_path / zip_path("90210")).is_file()

    def test_member_profile_loadable(self, tmp_path: Path) -> None:
        profile = make_member_profile(slug="test-member", bioguide_id="T000001", name="Test Member")
        snap = make_snapshot(tmp_path, member_profiles=[profile])
        loaded = load_member_profile(snap.root, "test-member")
        assert loaded.bioguide_id == "T000001"
        assert loaded.name == "Test Member"

    def test_evidence_card_loadable(self, tmp_path: Path) -> None:
        card = make_evidence_card(evidence_card_id="ec-load-test", score_delta=10.0)
        snap = make_snapshot(tmp_path, evidence_cards=[card])
        loaded = load_evidence_card(snap.root, "ec-load-test")
        assert loaded.score_delta == 10.0

    def test_zip_feed_loadable(self, tmp_path: Path) -> None:
        feed = make_zip_feed(zip_code="12345")
        snap = make_snapshot(tmp_path, zip_feeds=[feed])
        loaded = load_zip_feed(snap.root, "12345")
        assert loaded.zip_code == "12345"

    def test_manifest_valid_and_complete(self, tmp_path: Path) -> None:
        snap = make_snapshot(tmp_path, snapshot_id="2026-01-01")
        manifest = load_manifest(snap.root, "2026-01-01")
        assert manifest.verify_counts()
        assert manifest.total_files >= 2  # at least one member + one evidence card

    def test_empty_member_profiles(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path, member_profiles=[])
        assert not (tmp_path / "members").exists()

    def test_empty_evidence_cards(self, tmp_path: Path) -> None:
        make_snapshot(tmp_path, evidence_cards=[])
        assert not (tmp_path / "evidence").exists()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


class TestHelperFactories:
    def test_make_evidence_card_returns_valid_payload(self) -> None:
        card = make_evidence_card()
        assert isinstance(card, EvidenceCardPayload)
        assert card.evidence_card_id == "ec-0001"

    def test_make_evidence_card_custom_id(self) -> None:
        card = make_evidence_card(evidence_card_id="ec-xyz")
        assert card.evidence_card_id == "ec-xyz"

    def test_make_evidence_card_custom_score_delta(self) -> None:
        card = make_evidence_card(score_delta=12.5)
        assert card.score_delta == 12.5

    def test_make_evidence_card_custom_slug(self) -> None:
        card = make_evidence_card(member_slug="john-doe")
        assert card.member_slug == "john-doe"

    def test_make_evidence_card_custom_snapshot_date(self) -> None:
        card = make_evidence_card(snapshot_date=date(2025, 6, 1))
        assert card.snapshot_date == date(2025, 6, 1)

    def test_make_member_profile_returns_valid_payload(self) -> None:
        profile = make_member_profile()
        assert isinstance(profile, MemberProfilePayload)
        assert profile.bioguide_id == "P000197"

    def test_make_member_profile_custom_slug(self) -> None:
        profile = make_member_profile(slug="john-smith", bioguide_id="S999999")
        assert profile.slug == "john-smith"
        assert profile.bioguide_id == "S999999"

    def test_make_member_profile_custom_chamber(self) -> None:
        profile = make_member_profile(chamber="senate")
        assert profile.chamber == "senate"

    def test_make_zip_feed_returns_valid_payload(self) -> None:
        feed = make_zip_feed()
        assert isinstance(feed, ZipFeedPayload)
        assert feed.zip_code == "94102"

    def test_make_zip_feed_custom_zip_code(self) -> None:
        feed = make_zip_feed(zip_code="30301")
        assert feed.zip_code == "30301"

    def test_make_zip_feed_custom_members(self) -> None:
        from src.export.contracts import ZipMemberSummary

        members = [
            ZipMemberSummary(
                bioguide_id="X000001",
                name="Test Person",
                slug="test-person",
                chamber="house",
                party="Independent",
                scores=[],
                top_evidence_card_ids=[],
            )
        ]
        feed = make_zip_feed(zip_code="11111", members=members)
        assert len(feed.members) == 1
        assert feed.members[0].bioguide_id == "X000001"

    def test_make_zip_feed_empty_members(self) -> None:
        feed = make_zip_feed(zip_code="99999", members=[])
        assert feed.members == []
