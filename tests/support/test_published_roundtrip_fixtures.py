"""Tests for tests/support/published_roundtrip_fixtures.py.

Validates that:
- PublishedRoundtripBuilder writes the expected publish-tree layout.
- The publish tree is aligned with the row sets (typed payload comparison,
  not JSON string comparison).
- make_roundtrip() produces a readable, correctly-rooted PublishedRoundtrip.
- Overrides replace defaults correctly for all four surfaces.
- Row set factory functions produce valid inputs to assemblers.
- Homepage feed row sets can produce a valid HomepageFeedPayload without a
  corresponding file in the tree.

No network calls; all I/O is scoped to pytest's tmp_path.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.export.contracts import EvidenceCardPayload, MemberProfilePayload, ZipFeedPayload
from src.export.local_store import (
    load_evidence_card,
    load_manifest,
    load_member_profile,
    load_zip_feed,
)
from src.export.writer import evidence_path, manifest_path, member_path, zip_path
from src.homepage.contracts import HomepageFeedPayload
from tests.support.published_roundtrip_fixtures import (
    EvidenceCardRowSet,
    HomepageFeedRowSet,
    MemberRowSet,
    PublishedRoundtrip,
    PublishedRoundtripBuilder,
    ZipFeedRowSet,
    assemble_from_evidence_card_row_set,
    assemble_from_homepage_feed_row_set,
    assemble_from_member_row_set,
    assemble_from_zip_feed_row_set,
    make_evidence_card_row_set,
    make_homepage_feed_row_set,
    make_member_row_set,
    make_roundtrip,
    make_zip_feed_row_set,
)


# ---------------------------------------------------------------------------
# PublishedRoundtripBuilder — file layout
# ---------------------------------------------------------------------------


class TestPublishedRoundtripBuilderLayout:
    def test_manifest_file_exists(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path, snapshot_id="2026-01-01").build()
        assert (tmp_path / manifest_path("2026-01-01")).is_file()

    def test_default_member_profile_file_exists(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path).build()
        assert (tmp_path / member_path("nancy-pelosi")).is_file()

    def test_default_evidence_card_file_exists(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path).build()
        assert (tmp_path / evidence_path("ec-0001")).is_file()

    def test_default_zip_feed_file_exists(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path).build()
        assert (tmp_path / zip_path("94102")).is_file()

    def test_members_directory_created(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path).build()
        assert (tmp_path / "members").is_dir()

    def test_evidence_directory_created(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path).build()
        assert (tmp_path / "evidence").is_dir()

    def test_zip_directory_created(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path).build()
        assert (tmp_path / "zip").is_dir()

    def test_snapshots_directory_created(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path, snapshot_id="2026-01-01").build()
        assert (tmp_path / "snapshots" / "2026-01-01").is_dir()

    def test_no_zip_feeds_after_no_zip_feeds(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path).no_zip_feeds().build()
        assert not (tmp_path / "zip").exists()


# ---------------------------------------------------------------------------
# PublishedRoundtripBuilder — typed payload roundtrip
# ---------------------------------------------------------------------------


class TestPublishedRoundtripBuilderRoundtrip:
    """The central guarantee: assembling from rows produces the same typed
    payload as loading from the written file."""

    def test_member_profile_roundtrip(self, tmp_path: Path) -> None:
        rt = PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        rs = rt.member_row_sets[0]
        assembled = assemble_from_member_row_set(rs)
        loaded = load_member_profile(tmp_path, "nancy-pelosi")
        assert assembled == loaded

    def test_evidence_card_roundtrip(self, tmp_path: Path) -> None:
        rt = PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        rs = rt.evidence_card_row_sets[0]
        assembled = assemble_from_evidence_card_row_set(rs)
        loaded = load_evidence_card(tmp_path, "ec-0001")
        assert assembled == loaded

    def test_zip_feed_roundtrip(self, tmp_path: Path) -> None:
        rt = PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        rs = rt.zip_feed_row_sets[0]
        assembled = assemble_from_zip_feed_row_set(rs, snapshot_date=rt.snapshot_date)
        loaded = load_zip_feed(tmp_path, "94102")
        assert assembled == loaded

    def test_member_profile_typed_fields(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        loaded = load_member_profile(tmp_path, "nancy-pelosi")
        assert isinstance(loaded, MemberProfilePayload)
        assert loaded.bioguide_id == "P000197"
        assert loaded.slug == "nancy-pelosi"
        assert loaded.chamber == "house"

    def test_evidence_card_typed_fields(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        loaded = load_evidence_card(tmp_path, "ec-0001")
        assert isinstance(loaded, EvidenceCardPayload)
        assert loaded.evidence_card_id == "ec-0001"
        assert loaded.member_bioguide_id == "P000197"

    def test_zip_feed_typed_fields(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        loaded = load_zip_feed(tmp_path, "94102")
        assert isinstance(loaded, ZipFeedPayload)
        assert loaded.zip_code == "94102"
        assert len(loaded.members) == 1

    def test_manifest_covers_all_data_files(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path, snapshot_id="2026-01-01").build()
        manifest = load_manifest(tmp_path, "2026-01-01")
        paths = {e.path for e in manifest.entries}
        assert member_path("nancy-pelosi") in paths
        assert evidence_path("ec-0001") in paths
        assert zip_path("94102") in paths

    def test_manifest_does_not_include_itself(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path, snapshot_id="2026-01-01").build()
        manifest = load_manifest(tmp_path, "2026-01-01")
        paths = {e.path for e in manifest.entries}
        assert manifest_path("2026-01-01") not in paths

    def test_manifest_verify_counts(self, tmp_path: Path) -> None:
        PublishedRoundtripBuilder(tmp_path, snapshot_id="2026-01-01").build()
        manifest = load_manifest(tmp_path, "2026-01-01")
        assert manifest.verify_counts()

    def test_member_profile_snapshot_date_comes_from_row_set(self, tmp_path: Path) -> None:
        # Member profile snapshot_date is derived from score_snapshot_rows["snapshot_at"],
        # not from the builder's snapshot_date.  The row set controls it.
        rs = make_member_row_set(snapshot_date=date(2025, 6, 15))
        rt = PublishedRoundtripBuilder(tmp_path).with_member_row_sets([rs]).build().roundtrip()
        loaded = load_member_profile(tmp_path, "nancy-pelosi")
        assembled = assemble_from_member_row_set(rt.member_row_sets[0])
        assert loaded.snapshot_date == assembled.snapshot_date == date(2025, 6, 15)

    def test_snapshot_date_preserved_in_zip_feed(self, tmp_path: Path) -> None:
        custom_date = date(2025, 6, 15)
        PublishedRoundtripBuilder(tmp_path).with_snapshot_date(custom_date).build()
        loaded = load_zip_feed(tmp_path, "94102")
        assert loaded.snapshot_date == custom_date


# ---------------------------------------------------------------------------
# PublishedRoundtripBuilder — overrides
# ---------------------------------------------------------------------------


class TestPublishedRoundtripBuilderOverrides:
    def test_with_member_row_sets_replaces_default(self, tmp_path: Path) -> None:
        rs = make_member_row_set(slug="chuck-schumer", bioguide_id="S000148")
        PublishedRoundtripBuilder(tmp_path).with_member_row_sets([rs]).build()
        assert (tmp_path / member_path("chuck-schumer")).is_file()
        assert not (tmp_path / member_path("nancy-pelosi")).is_file()

    def test_with_evidence_card_row_sets_replaces_default(self, tmp_path: Path) -> None:
        rs = make_evidence_card_row_set(public_id="ec-9999")
        PublishedRoundtripBuilder(tmp_path).with_evidence_card_row_sets([rs]).build()
        assert (tmp_path / evidence_path("ec-9999")).is_file()
        assert not (tmp_path / evidence_path("ec-0001")).is_file()

    def test_with_zip_feed_row_sets_replaces_default(self, tmp_path: Path) -> None:
        rs = make_zip_feed_row_set(zip_code="10001")
        PublishedRoundtripBuilder(tmp_path).with_zip_feed_row_sets([rs]).build()
        assert (tmp_path / zip_path("10001")).is_file()
        assert not (tmp_path / zip_path("94102")).is_file()

    def test_no_zip_feeds_clears_zip_row_sets(self, tmp_path: Path) -> None:
        builder = PublishedRoundtripBuilder(tmp_path)
        builder.with_zip_feed_row_sets(
            [make_zip_feed_row_set(zip_code="10001")]
        ).no_zip_feeds().build()
        assert not (tmp_path / "zip").exists()

    def test_multiple_member_row_sets_written(self, tmp_path: Path) -> None:
        sets = [
            make_member_row_set(slug="member-a", bioguide_id="A000001"),
            make_member_row_set(slug="member-b", bioguide_id="B000002"),
        ]
        PublishedRoundtripBuilder(tmp_path).with_member_row_sets(sets).build()
        assert (tmp_path / member_path("member-a")).is_file()
        assert (tmp_path / member_path("member-b")).is_file()

    def test_multiple_evidence_card_row_sets_written(self, tmp_path: Path) -> None:
        sets = [
            make_evidence_card_row_set(public_id="ec-0001"),
            make_evidence_card_row_set(public_id="ec-0002"),
        ]
        PublishedRoundtripBuilder(tmp_path).with_evidence_card_row_sets(sets).build()
        assert (tmp_path / evidence_path("ec-0001")).is_file()
        assert (tmp_path / evidence_path("ec-0002")).is_file()

    def test_multiple_zip_feed_row_sets_written(self, tmp_path: Path) -> None:
        sets = [
            make_zip_feed_row_set(zip_code="94102"),
            make_zip_feed_row_set(zip_code="10001"),
        ]
        PublishedRoundtripBuilder(tmp_path).with_zip_feed_row_sets(sets).build()
        assert (tmp_path / zip_path("94102")).is_file()
        assert (tmp_path / zip_path("10001")).is_file()

    def test_override_member_roundtrip_matches_rows(self, tmp_path: Path) -> None:
        rs = make_member_row_set(slug="test-member", bioguide_id="T000001", full_name="Test Member")
        rt = PublishedRoundtripBuilder(tmp_path).with_member_row_sets([rs]).build().roundtrip()
        assembled = assemble_from_member_row_set(rt.member_row_sets[0])
        loaded = load_member_profile(tmp_path, "test-member")
        assert assembled == loaded

    def test_override_evidence_card_roundtrip_matches_rows(self, tmp_path: Path) -> None:
        rs = make_evidence_card_row_set(public_id="ec-custom", score_delta=12.5)
        rt = (
            PublishedRoundtripBuilder(tmp_path)
            .with_evidence_card_row_sets([rs])
            .build()
            .roundtrip()
        )
        assembled = assemble_from_evidence_card_row_set(rt.evidence_card_row_sets[0])
        loaded = load_evidence_card(tmp_path, "ec-custom")
        assert assembled == loaded

    def test_override_zip_feed_roundtrip_matches_rows(self, tmp_path: Path) -> None:
        rs = make_zip_feed_row_set(zip_code="90210")
        rt = PublishedRoundtripBuilder(tmp_path).with_zip_feed_row_sets([rs]).build().roundtrip()
        assembled = assemble_from_zip_feed_row_set(
            rt.zip_feed_row_sets[0], snapshot_date=rt.snapshot_date
        )
        loaded = load_zip_feed(tmp_path, "90210")
        assert assembled == loaded


# ---------------------------------------------------------------------------
# PublishedRoundtripBuilder — roundtrip() return type
# ---------------------------------------------------------------------------


class TestPublishedRoundtripBuilderResult:
    def test_roundtrip_returns_published_roundtrip(self, tmp_path: Path) -> None:
        rt = PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        assert isinstance(rt, PublishedRoundtrip)

    def test_snapshot_root_attribute(self, tmp_path: Path) -> None:
        rt = PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        assert rt.snapshot.root == tmp_path

    def test_snapshot_id_attribute(self, tmp_path: Path) -> None:
        rt = PublishedRoundtripBuilder(tmp_path, snapshot_id="2026-03-01").build().roundtrip()
        assert rt.snapshot.snapshot_id == "2026-03-01"

    def test_snapshot_date_attribute(self, tmp_path: Path) -> None:
        d = date(2025, 9, 1)
        rt = PublishedRoundtripBuilder(tmp_path).with_snapshot_date(d).build().roundtrip()
        assert rt.snapshot_date == d

    def test_member_row_sets_attribute(self, tmp_path: Path) -> None:
        rt = PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        assert len(rt.member_row_sets) == 1
        assert isinstance(rt.member_row_sets[0], MemberRowSet)

    def test_evidence_card_row_sets_attribute(self, tmp_path: Path) -> None:
        rt = PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        assert len(rt.evidence_card_row_sets) == 1
        assert isinstance(rt.evidence_card_row_sets[0], EvidenceCardRowSet)

    def test_zip_feed_row_sets_attribute(self, tmp_path: Path) -> None:
        rt = PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        assert len(rt.zip_feed_row_sets) == 1
        assert isinstance(rt.zip_feed_row_sets[0], ZipFeedRowSet)

    def test_homepage_feed_row_set_attribute(self, tmp_path: Path) -> None:
        rt = PublishedRoundtripBuilder(tmp_path).build().roundtrip()
        assert isinstance(rt.homepage_feed_row_set, HomepageFeedRowSet)


# ---------------------------------------------------------------------------
# make_roundtrip — convenience wrapper
# ---------------------------------------------------------------------------


class TestMakeRoundtrip:
    def test_returns_published_roundtrip(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        assert isinstance(rt, PublishedRoundtrip)

    def test_default_snapshot_id(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path, snapshot_id="2026-01-01")
        assert rt.snapshot.snapshot_id == "2026-01-01"

    def test_root_is_tmp_path(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        assert rt.snapshot.root == tmp_path

    def test_manifest_exists_by_default(self, tmp_path: Path) -> None:
        make_roundtrip(tmp_path, snapshot_id="2026-01-01")
        assert (tmp_path / manifest_path("2026-01-01")).is_file()

    def test_default_member_profile_exists(self, tmp_path: Path) -> None:
        make_roundtrip(tmp_path)
        assert (tmp_path / member_path("nancy-pelosi")).is_file()

    def test_default_evidence_card_exists(self, tmp_path: Path) -> None:
        make_roundtrip(tmp_path)
        assert (tmp_path / evidence_path("ec-0001")).is_file()

    def test_default_zip_feed_exists(self, tmp_path: Path) -> None:
        make_roundtrip(tmp_path)
        assert (tmp_path / zip_path("94102")).is_file()

    def test_custom_member_row_sets(self, tmp_path: Path) -> None:
        rs = make_member_row_set(slug="test-member", bioguide_id="T000001")
        make_roundtrip(tmp_path, member_row_sets=[rs])
        assert (tmp_path / member_path("test-member")).is_file()
        assert not (tmp_path / member_path("nancy-pelosi")).is_file()

    def test_custom_evidence_card_row_sets(self, tmp_path: Path) -> None:
        rs = make_evidence_card_row_set(public_id="ec-custom")
        make_roundtrip(tmp_path, evidence_card_row_sets=[rs])
        assert (tmp_path / evidence_path("ec-custom")).is_file()
        assert not (tmp_path / evidence_path("ec-0001")).is_file()

    def test_custom_zip_feed_row_sets(self, tmp_path: Path) -> None:
        rs = make_zip_feed_row_set(zip_code="30301")
        make_roundtrip(tmp_path, zip_feed_row_sets=[rs])
        assert (tmp_path / zip_path("30301")).is_file()

    def test_empty_member_row_sets(self, tmp_path: Path) -> None:
        make_roundtrip(tmp_path, member_row_sets=[])
        assert not (tmp_path / "members").exists()

    def test_empty_evidence_card_row_sets(self, tmp_path: Path) -> None:
        make_roundtrip(tmp_path, evidence_card_row_sets=[])
        assert not (tmp_path / "evidence").exists()

    def test_empty_zip_feed_row_sets(self, tmp_path: Path) -> None:
        make_roundtrip(tmp_path, zip_feed_row_sets=[])
        assert not (tmp_path / "zip").exists()

    def test_member_profile_loadable_and_matches(self, tmp_path: Path) -> None:
        rs = make_member_row_set(slug="test-member", bioguide_id="T000001", full_name="Test Member")
        rt = make_roundtrip(tmp_path, member_row_sets=[rs])
        loaded = load_member_profile(rt.snapshot.root, "test-member")
        assembled = assemble_from_member_row_set(rt.member_row_sets[0])
        assert loaded == assembled
        assert loaded.bioguide_id == "T000001"

    def test_evidence_card_loadable_and_matches(self, tmp_path: Path) -> None:
        rs = make_evidence_card_row_set(public_id="ec-load-test", score_delta=10.0)
        rt = make_roundtrip(tmp_path, evidence_card_row_sets=[rs])
        loaded = load_evidence_card(rt.snapshot.root, "ec-load-test")
        assembled = assemble_from_evidence_card_row_set(rt.evidence_card_row_sets[0])
        assert loaded == assembled
        assert loaded.score_delta == 10.0

    def test_zip_feed_loadable_and_matches(self, tmp_path: Path) -> None:
        rs = make_zip_feed_row_set(zip_code="12345")
        rt = make_roundtrip(tmp_path, zip_feed_row_sets=[rs])
        loaded = load_zip_feed(rt.snapshot.root, "12345")
        assembled = assemble_from_zip_feed_row_set(
            rt.zip_feed_row_sets[0], snapshot_date=rt.snapshot_date
        )
        assert loaded == assembled
        assert loaded.zip_code == "12345"

    def test_manifest_valid_and_complete(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path, snapshot_id="2026-01-01")
        manifest = load_manifest(rt.snapshot.root, "2026-01-01")
        assert manifest.verify_counts()
        assert manifest.total_files >= 3  # member + evidence card + zip feed

    def test_custom_snapshot_date_propagates(self, tmp_path: Path) -> None:
        d = date(2025, 3, 15)
        rt = make_roundtrip(tmp_path, snapshot_date=d)
        assert rt.snapshot_date == d
        loaded = load_zip_feed(rt.snapshot.root, "94102")
        assert loaded.snapshot_date == d


# ---------------------------------------------------------------------------
# Homepage feed row set — no file in tree; just assembles correctly
# ---------------------------------------------------------------------------


class TestHomepageFeedRowSet:
    def test_default_produces_valid_payload(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        payload = assemble_from_homepage_feed_row_set(rt.homepage_feed_row_set)
        assert isinstance(payload, HomepageFeedPayload)

    def test_snapshot_date_set_on_payload(self, tmp_path: Path) -> None:
        d = date(2025, 9, 1)
        rt = make_roundtrip(tmp_path, snapshot_date=d)
        payload = assemble_from_homepage_feed_row_set(rt.homepage_feed_row_set, snapshot_date=d)
        assert payload.snapshot_date == d

    def test_recent_events_populated(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        payload = assemble_from_homepage_feed_row_set(rt.homepage_feed_row_set)
        assert len(payload.recent_events) >= 1

    def test_recent_evidence_card_ids_populated(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        payload = assemble_from_homepage_feed_row_set(rt.homepage_feed_row_set)
        assert "ec-0001" in payload.recent_evidence_card_ids

    def test_top_changes_populated(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        payload = assemble_from_homepage_feed_row_set(rt.homepage_feed_row_set)
        assert len(payload.top_changes) >= 1

    def test_custom_homepage_feed_row_set(self, tmp_path: Path) -> None:
        rs = make_homepage_feed_row_set(public_id="ec-hp-01", score_delta=20.0)
        rt = make_roundtrip(tmp_path, homepage_feed_row_set=rs)
        payload = assemble_from_homepage_feed_row_set(rt.homepage_feed_row_set)
        assert "ec-hp-01" in payload.recent_evidence_card_ids

    def test_assembles_without_tree(self) -> None:
        # Homepage feed has no file in the publish tree; assembly works standalone.
        rs = make_homepage_feed_row_set()
        payload = assemble_from_homepage_feed_row_set(rs)
        assert isinstance(payload, HomepageFeedPayload)


# ---------------------------------------------------------------------------
# Row set factory functions
# ---------------------------------------------------------------------------


class TestRowSetFactories:
    def test_make_member_row_set_default(self) -> None:
        rs = make_member_row_set()
        assert isinstance(rs, MemberRowSet)
        assert rs.member_row["bioguide_id"] == "P000197"
        assert rs.member_row["slug"] == "nancy-pelosi"

    def test_make_member_row_set_custom_slug(self) -> None:
        rs = make_member_row_set(slug="john-doe", bioguide_id="D000001")
        assert rs.member_row["slug"] == "john-doe"
        assert rs.member_row["bioguide_id"] == "D000001"

    def test_make_member_row_set_custom_chamber(self) -> None:
        rs = make_member_row_set(chamber="senate")
        assert rs.member_row["chamber"] == "senate"

    def test_make_member_row_set_custom_score(self) -> None:
        rs = make_member_row_set(current_score=25.0)
        assert rs.score_snapshot_rows[0]["score_total"] == 25.0

    def test_make_member_row_set_custom_snapshot_date(self) -> None:
        d = date(2025, 6, 1)
        rs = make_member_row_set(snapshot_date=d)
        assert rs.score_snapshot_rows[0]["snapshot_at"] == d

    def test_make_member_row_set_produces_assembler_input(self) -> None:
        rs = make_member_row_set()
        profile = assemble_from_member_row_set(rs)
        assert isinstance(profile, MemberProfilePayload)

    def test_make_evidence_card_row_set_default(self) -> None:
        rs = make_evidence_card_row_set()
        assert isinstance(rs, EvidenceCardRowSet)
        assert rs.row["public_id"] == "ec-0001"

    def test_make_evidence_card_row_set_custom_id(self) -> None:
        rs = make_evidence_card_row_set(public_id="ec-xyz")
        assert rs.row["public_id"] == "ec-xyz"

    def test_make_evidence_card_row_set_custom_score(self) -> None:
        rs = make_evidence_card_row_set(score_delta=15.0)
        assert rs.row["score_delta"] == 15.0

    def test_make_evidence_card_row_set_produces_assembler_input(self) -> None:
        rs = make_evidence_card_row_set()
        card = assemble_from_evidence_card_row_set(rs)
        assert isinstance(card, EvidenceCardPayload)

    def test_make_zip_feed_row_set_default(self) -> None:
        rs = make_zip_feed_row_set()
        assert isinstance(rs, ZipFeedRowSet)
        assert rs.zip_code == "94102"

    def test_make_zip_feed_row_set_custom_zip(self) -> None:
        rs = make_zip_feed_row_set(zip_code="30301")
        assert rs.zip_code == "30301"

    def test_make_zip_feed_row_set_ambiguity_note(self) -> None:
        rs = make_zip_feed_row_set(ambiguity_note="Spans two districts.")
        assert rs.ambiguity_note == "Spans two districts."

    def test_make_zip_feed_row_set_produces_assembler_input(self) -> None:
        rs = make_zip_feed_row_set()
        feed = assemble_from_zip_feed_row_set(rs)
        assert isinstance(feed, ZipFeedPayload)

    def test_make_homepage_feed_row_set_default(self) -> None:
        rs = make_homepage_feed_row_set()
        assert isinstance(rs, HomepageFeedRowSet)
        assert len(rs.rows) == 1
        assert rs.rows[0]["public_id"] == "ec-0001"

    def test_make_homepage_feed_row_set_custom_id(self) -> None:
        rs = make_homepage_feed_row_set(public_id="ec-hp-99")
        assert rs.rows[0]["public_id"] == "ec-hp-99"

    def test_make_homepage_feed_row_set_produces_assembler_input(self) -> None:
        rs = make_homepage_feed_row_set()
        payload = assemble_from_homepage_feed_row_set(rs)
        assert isinstance(payload, HomepageFeedPayload)


# ---------------------------------------------------------------------------
# Determinism — same rows, same bytes on disk
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_member_profile_bytes_are_stable(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        PublishedRoundtripBuilder(root_a).build()
        PublishedRoundtripBuilder(root_b).build()
        a_bytes = (root_a / member_path("nancy-pelosi")).read_bytes()
        b_bytes = (root_b / member_path("nancy-pelosi")).read_bytes()
        assert a_bytes == b_bytes

    def test_evidence_card_bytes_are_stable(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        PublishedRoundtripBuilder(root_a).build()
        PublishedRoundtripBuilder(root_b).build()
        a_bytes = (root_a / evidence_path("ec-0001")).read_bytes()
        b_bytes = (root_b / evidence_path("ec-0001")).read_bytes()
        assert a_bytes == b_bytes

    def test_zip_feed_bytes_are_stable(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        PublishedRoundtripBuilder(root_a).build()
        PublishedRoundtripBuilder(root_b).build()
        a_bytes = (root_a / zip_path("94102")).read_bytes()
        b_bytes = (root_b / zip_path("94102")).read_bytes()
        assert a_bytes == b_bytes
