"""Tests for src/pipeline/publish_snapshot_run.py.

No live DB. DB fetchers and payload assemblers are mocked. Filesystem writes
use pytest's tmp_path.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest import mock

from src.api.read_service import get_homepage_bootstrap
from src.api.read_service import get_zip_entry
from src.export.contracts import (  # noqa: E402
    ConfidenceLabel,
    EvidenceCardPayload,
    HistoricalCommitteeMembership,
    MemberHistoryEvent,
    MemberHistoryPayload,
    MemberHistorySnapshot,
    MemberProfilePayload,
    ZipFeedPayload,
)
from src.export.manifest import SnapshotManifest
from src.homepage.contracts import HomepageFeedPayload
from src.homepage.contracts import MemberMovementSummary, RecentEventSummary
from src.pipeline.publish_snapshot_run import (  # noqa: E402
    ZipBundleInputs,
    _build_current_member_lookup_file,
    _build_current_member_lookup_payload,
    _build_evidence_cards,
    _build_member_histories,
    _build_homepage_bootstrap_file,
    _build_homepage_file,
    _build_member_profiles,
    _build_zip_feeds,
    _make_planner,
    publish_snapshot_run,
)
from src.export.writer import zip_entry_path
from src.zip.resolve import (  # noqa: E402
    DistrictMemberRow,
    SenatorRow,
    ZipDistrictRow,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_SNAP_DATE = date(2026, 4, 14)
_SNAP_ID = "2026-04-14"


def _member_profile(slug: str = "alice-smith") -> MemberProfilePayload:
    return MemberProfilePayload(
        bioguide_id="B001",
        name="Alice Smith",
        slug=slug,
        state="CA",
        chamber="house",
        party="D",
        scores=[],
        recent_rule_fires=[],
        committees=[],
        total_evidence_cards=0,
        snapshot_date=_SNAP_DATE,
    )


def _evidence_card(card_id: str = "ec-001") -> EvidenceCardPayload:
    return EvidenceCardPayload(
        evidence_card_id=card_id,
        member_bioguide_id="B001",
        member_name="Alice Smith",
        member_slug="alice-smith",
        dimension="conflict_of_interest_risk",
        rule_id="committee_sector_trade_v1",
        rule_version=1,
        score_delta=2.0,
        short_explanation="Committee overlap detected.",
        blocks=[],
        source_anchors=[],
        confidence=ConfidenceLabel.HIGH,
        snapshot_date=_SNAP_DATE,
        created_at=datetime(2026, 4, 14, tzinfo=UTC),
    )


def _zip_feed(zip_code: str = "90210") -> ZipFeedPayload:
    return ZipFeedPayload(
        zip_code=zip_code,
        congressional_district="CA-30",
        ambiguity_note=None,
        members=[],
        snapshot_date=_SNAP_DATE,
    )


def _member_history(slug: str = "alice-smith") -> MemberHistoryPayload:
    return MemberHistoryPayload(
        bioguide_id="B001",
        name="Alice Smith",
        slug=slug,
        state="CA",
        district=None,
        chamber="house",
        party="D",
        snapshots=[
            MemberHistorySnapshot(
                snapshot_date=_SNAP_DATE,
                score_total=5.0,
                score_total_delta=None,
                dimension_scores={"conflict_of_interest_risk": 5.0},
                published_at=datetime(2026, 4, 14, tzinfo=UTC),
            )
        ],
        events=[
            MemberHistoryEvent(
                rule_id="committee_sector_trade_v1",
                dimension="conflict_of_interest_risk",
                severity="high",
                evidence_card_id="ec-001",
                short_explanation="Committee overlap detected.",
                score_delta=2.0,
                snapshot_date=_SNAP_DATE,
                fired_at=datetime(2026, 4, 14, tzinfo=UTC),
            )
        ],
        committee_history=[
            HistoricalCommitteeMembership(
                committee_name="Finance",
                role="Member",
                start_date=_SNAP_DATE,
                end_date=None,
                is_current=True,
                chamber="house",
                committee_type="standing",
            )
        ],
    )


def _zip_bundle(zip5_codes: list[str] | None = None) -> ZipBundleInputs:
    codes = ["90210"] if zip5_codes is None else zip5_codes
    return ZipBundleInputs(
        zip5_codes=codes,
        zip_district_rows=[ZipDistrictRow(zip5="90210", state="CA", district=30, population_share=1.0)],
        district_member_rows=[DistrictMemberRow(state="CA", district=30, bioguide_id="B001", full_name="Alice Smith", party="D", slug="alice-smith")],
        senator_rows=[
            SenatorRow(state="CA", bioguide_id="S001", full_name="Sen One", party="D", slug="sen-one", seat=1),
            SenatorRow(state="CA", bioguide_id="S002", full_name="Sen Two", party="D", slug="sen-two", seat=2),
        ],
    )


def _empty_zip_bundle() -> ZipBundleInputs:
    return ZipBundleInputs(zip5_codes=[], zip_district_rows=[], district_member_rows=[], senator_rows=[])


def _fake_homepage_payload() -> HomepageFeedPayload:
    return HomepageFeedPayload(
        snapshot_date=_SNAP_DATE,
        top_changes=[],
        recent_events=[],
        recent_evidence_card_ids=[],
    )


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------

#: All DB-fetcher patch targets (located in publish_snapshot_run's namespace).
_FETCH_PATCHES: dict[str, Any] = {
    "src.pipeline.publish_snapshot_run.fetch_current_member_slugs": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.fetch_member_row_by_slug": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.fetch_member_score_snapshot_rows": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.fetch_member_rule_fire_rows": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.fetch_member_committee_rows": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.fetch_all_evidence_card_rows": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.fetch_homepage_feed_rows": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.fetch_zip_member_summary_rows": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.fetch_recent_evidence_ids_by_bioguide": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.assemble_member_profile": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.assemble_member_history": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.assemble_evidence_card": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.assemble_homepage_payload": mock.DEFAULT,
    "src.pipeline.publish_snapshot_run.assemble_zip_feed": mock.DEFAULT,
}


# ---------------------------------------------------------------------------
# Tests for private helpers
# ---------------------------------------------------------------------------


class TestBuildMemberProfiles:
    def test_one_member(self) -> None:
        conn = object()
        profile = _member_profile()
        with (
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_row_by_slug", return_value={"id": 1}) as _row,
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_score_snapshot_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_rule_fire_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_committee_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_member_profile", return_value=profile) as assemble,
        ):
            result = _build_member_profiles(conn, ["alice-smith"])
        assert result == [profile]
        assemble.assert_called_once()

    def test_missing_row_is_skipped(self) -> None:
        conn = object()
        with mock.patch("src.pipeline.publish_snapshot_run.fetch_member_row_by_slug", return_value=None):
            result = _build_member_profiles(conn, ["ghost-slug"])
        assert result == []

    def test_multiple_slugs(self) -> None:
        conn = object()
        profiles = [_member_profile("s1"), _member_profile("s2")]
        call_count = {"n": 0}

        def _row_side(_, slug):
            return {"id": call_count["n"]}

        def _assemble_side(*_args):
            p = profiles[call_count["n"]]
            call_count["n"] += 1
            return p

        with (
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_row_by_slug", side_effect=_row_side),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_score_snapshot_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_rule_fire_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_committee_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_member_profile", side_effect=_assemble_side),
        ):
            result = _build_member_profiles(conn, ["s1", "s2"])
        assert len(result) == 2


class TestBuildEvidenceCards:
    def test_assembles_each_row(self) -> None:
        conn = object()
        card = _evidence_card()
        with (
            mock.patch("src.pipeline.publish_snapshot_run.fetch_all_evidence_card_rows", return_value=[{"id": 1}]),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_evidence_card", return_value=card) as assemble,
        ):
            result = _build_evidence_cards(conn)
        assert result == [card]
        assemble.assert_called_once_with({"id": 1})

    def test_empty_returns_empty(self) -> None:
        conn = object()
        with mock.patch("src.pipeline.publish_snapshot_run.fetch_all_evidence_card_rows", return_value=[]):
            result = _build_evidence_cards(conn)
        assert result == []


class TestBuildMemberHistories:
    def test_one_member(self) -> None:
        conn = object()
        history = _member_history()
        with (
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_row_by_slug", return_value={"id": 1}) as _row,
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_score_snapshot_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_rule_fire_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_committee_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_member_history", return_value=history) as assemble,
        ):
            result = _build_member_histories(conn, ["alice-smith"])
        assert result == [history]
        assemble.assert_called_once()


class TestBuildZipFeeds:
    def test_known_zip_produces_feed(self) -> None:
        conn = object()
        feed = _zip_feed()
        inputs = _zip_bundle(["90210"])
        with (
            mock.patch("src.pipeline.publish_snapshot_run.fetch_zip_member_summary_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_recent_evidence_ids_by_bioguide", return_value={}),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_zip_feed", return_value=feed) as assemble,
        ):
            result = _build_zip_feeds(conn, inputs, _SNAP_DATE)
        assert result == [feed]
        assemble.assert_called_once()

    def test_unknown_zip_is_skipped(self) -> None:
        conn = object()
        inputs = ZipBundleInputs(
            zip5_codes=["99999"],
            zip_district_rows=[],   # no crosswalk row → bundle = None
            district_member_rows=[],
            senator_rows=[],
        )
        result = _build_zip_feeds(conn, inputs, _SNAP_DATE)
        assert result == []

    def test_empty_zip_list(self) -> None:
        conn = object()
        inputs = _zip_bundle([])
        result = _build_zip_feeds(conn, inputs, _SNAP_DATE)
        assert result == []


class TestBuildHomepageFile:
    def test_produces_planned_file_at_correct_path(self) -> None:
        conn = object()
        payload = _fake_homepage_payload()
        with (
            mock.patch("src.pipeline.publish_snapshot_run.fetch_homepage_feed_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_homepage_payload", return_value=payload),
        ):
            pf = _build_homepage_file(conn, _SNAP_DATE)
        assert pf.path == "homepage/feed.json"
        assert len(pf.content) > 0
        assert pf.sha256 and len(pf.sha256) == 64


class TestBuildCurrentMemberLookupFile:
    def test_produces_planned_file_at_correct_path(self) -> None:
        pf = _build_current_member_lookup_file([_member_profile()], _SNAP_DATE)

        assert pf.path == "identity/current-member-lookup.json"
        assert len(pf.content) > 0
        assert pf.sha256 and len(pf.sha256) == 64


class TestBuildHomepageBootstrapFile:
    def test_produces_planned_file_at_correct_path(self) -> None:
        homepage = HomepageFeedPayload(
            snapshot_date=_SNAP_DATE,
            top_changes=[
                MemberMovementSummary(
                    bioguide_id="B001",
                    name="Alice Smith",
                    slug="alice-smith",
                    chamber="house",
                    party="D",
                    state="CA",
                    dimension="conflict_of_interest_risk",
                    score_delta=2.0,
                    abs_delta=2.0,
                    event_count=1,
                    top_evidence_card_ids=["ec-001"],
                )
            ],
            recent_events=[
                RecentEventSummary(
                    feed_event_id="event-001",
                    member_bioguide_id="B001",
                    member_name="Alice Smith",
                    member_slug="alice-smith",
                    dimension="conflict_of_interest_risk",
                    score_delta=2.0,
                    short_explanation="Committee overlap detected.",
                    evidence_card_id="ec-001",
                    occurred_at=_SNAP_DATE,
                )
            ],
            recent_evidence_card_ids=["ec-001"],
        )
        lookup_payload = _build_current_member_lookup_payload(
            [_member_profile()],
            _SNAP_DATE,
        )
        manifest = SnapshotManifest.model_validate(
            {
                "snapshot_id": _SNAP_ID,
                "created_at": "2026-04-14T00:00:00Z",
                "entries": [
                    {"path": "members/alice-smith.json", "sha256": "a" * 64, "size_bytes": 10},
                    {
                        "path": "identity/current-member-lookup.json",
                        "sha256": "b" * 64,
                        "size_bytes": 10,
                    },
                ],
                "total_files": 2,
                "total_bytes": 20,
                "root_sha256": "c" * 64,
            }
        )

        pf = _build_homepage_bootstrap_file(
            homepage,
            lookup_payload,
            manifest,
        )

        assert pf.path == "homepage/bootstrap.json"
        assert len(pf.content) > 0
        assert pf.sha256 and len(pf.sha256) == 64


class TestMakePlanner:
    def test_snapshot_files_plus_homepage(self, tmp_path: Path) -> None:
        profile = _member_profile()
        card = _evidence_card()
        feed = _zip_feed()
        lookup = _build_current_member_lookup_file([profile], _SNAP_DATE)
        homepage_payload = _fake_homepage_payload()
        homepage = mock.MagicMock()
        homepage.path = "homepage/feed.json"
        homepage.content = homepage_payload.model_dump_json().encode("utf-8")
        homepage.sha256 = "a" * 64
        homepage.size_bytes = len(homepage.content)
        history = _member_history()

        planner = _make_planner(
            _SNAP_ID,
            [profile],
            [history],
            [feed],
            [card],
            lookup,
            homepage,
        )
        planned = planner()

        paths = [f.path for f in planned]
        assert "members/alice-smith.json" in paths
        assert "history/members/alice-smith.json" in paths
        assert "zip/90210.json" in paths
        assert "evidence/ec-001.json" in paths
        assert "identity/current-member-lookup.json" in paths
        assert f"snapshots/{_SNAP_ID}/manifest.json" in paths
        assert "homepage/feed.json" in paths
        assert "homepage/bootstrap.json" in paths
        # homepage sits after the manifest
        assert paths.index("homepage/feed.json") > paths.index(f"snapshots/{_SNAP_ID}/manifest.json")
        assert paths.index("homepage/bootstrap.json") > paths.index("homepage/feed.json")


# ---------------------------------------------------------------------------
# Tests for the public entry point
# ---------------------------------------------------------------------------


class TestPublishSnapshotRun:
    """Integration tests over publish_snapshot_run; DB mocked, real filesystem."""

    def _run(
        self,
        tmp_path: Path,
        *,
        member_slugs: list[str] | None = None,
        profiles: list[MemberProfilePayload] | None = None,
        member_histories: list[MemberHistoryPayload] | None = None,
        evidence_cards: list[EvidenceCardPayload] | None = None,
        zip_feeds: list[ZipFeedPayload] | None = None,
        zip_bundle: ZipBundleInputs | None = None,
        homepage_payload: HomepageFeedPayload | None = None,
    ):
        member_slugs = member_slugs or []
        profiles = profiles or []
        member_histories = member_histories or []
        evidence_cards = evidence_cards or []
        zip_feeds = zip_feeds or []
        zip_bundle = zip_bundle if zip_bundle is not None else _empty_zip_bundle()
        homepage_payload = homepage_payload or _fake_homepage_payload()

        profile_iter = iter(profiles)
        history_iter = iter(member_histories)
        card_iter = iter(evidence_cards)
        feed_iter = iter(zip_feeds)

        with (
            mock.patch("src.pipeline.publish_snapshot_run.fetch_current_member_slugs", return_value=member_slugs),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_row_by_slug", return_value={"id": 1}),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_score_snapshot_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_rule_fire_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_committee_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_member_profile", side_effect=lambda *_: next(profile_iter)),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_member_history", side_effect=lambda *_: next(history_iter)),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_all_evidence_card_rows", return_value=[None] * len(evidence_cards)),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_evidence_card", side_effect=lambda *_: next(card_iter)),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_zip_member_summary_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_recent_evidence_ids_by_bioguide", return_value={}),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_zip_feed", side_effect=lambda *_: next(feed_iter)),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_homepage_feed_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_homepage_payload", return_value=homepage_payload),
        ):
            return publish_snapshot_run(
                conn=object(),
                snapshot_id=_SNAP_ID,
                snapshot_date=_SNAP_DATE,
                target_dir=tmp_path,
                zip_bundle_inputs=zip_bundle,
            )

    def test_empty_run_succeeds(self, tmp_path: Path) -> None:
        result = self._run(tmp_path)
        assert result.succeeded

    def test_happy_path_succeeds(self, tmp_path: Path) -> None:
        result = self._run(
            tmp_path,
            member_slugs=["alice-smith"],
            profiles=[_member_profile()],
            member_histories=[_member_history()],
            evidence_cards=[_evidence_card()],
            zip_feeds=[_zip_feed()],
            zip_bundle=_zip_bundle(["90210"]),
        )
        assert result.succeeded

    def test_snapshot_id_propagated(self, tmp_path: Path) -> None:
        result = self._run(tmp_path)
        assert result.snapshot_id == _SNAP_ID

    def test_member_file_written(self, tmp_path: Path) -> None:
        self._run(
            tmp_path,
            member_slugs=["alice-smith"],
            profiles=[_member_profile()],
            member_histories=[_member_history()],
        )
        assert (tmp_path / "members" / "alice-smith.json").exists()

    def test_member_history_file_written(self, tmp_path: Path) -> None:
        self._run(
            tmp_path,
            member_slugs=["alice-smith"],
            profiles=[_member_profile()],
            member_histories=[_member_history()],
        )
        assert (tmp_path / "history" / "members" / "alice-smith.json").exists()

    def test_evidence_file_written(self, tmp_path: Path) -> None:
        self._run(
            tmp_path,
            evidence_cards=[_evidence_card("ec-001")],
        )
        assert (tmp_path / "evidence" / "ec-001.json").exists()

    def test_zip_file_written(self, tmp_path: Path) -> None:
        self._run(
            tmp_path,
            zip_feeds=[_zip_feed("90210")],
            zip_bundle=_zip_bundle(["90210"]),
        )
        assert (tmp_path / "zip" / "90210.json").exists()
        assert (tmp_path / "zip-entry" / "90210.json").exists()

    def test_homepage_file_written(self, tmp_path: Path) -> None:
        self._run(tmp_path)
        assert (tmp_path / "homepage" / "feed.json").exists()

    def test_homepage_bootstrap_file_written(self, tmp_path: Path) -> None:
        self._run(tmp_path)
        assert (tmp_path / "homepage" / "bootstrap.json").exists()

    def test_manifest_written(self, tmp_path: Path) -> None:
        self._run(tmp_path)
        assert (tmp_path / "snapshots" / _SNAP_ID / "manifest.json").exists()

    def test_current_member_lookup_file_written(self, tmp_path: Path) -> None:
        self._run(tmp_path)
        assert (tmp_path / "identity" / "current-member-lookup.json").exists()

    def test_planned_count_one_of_each(self, tmp_path: Path) -> None:
        # 1 member + 1 member page + 1 member history + 1 card + 1 zip + 1 zip-entry + 1 lookup + 1 manifest + 1 homepage + 1 homepage bootstrap = 10
        result = self._run(
            tmp_path,
            member_slugs=["alice-smith"],
            profiles=[_member_profile()],
            member_histories=[_member_history()],
            evidence_cards=[_evidence_card()],
            zip_feeds=[_zip_feed()],
            zip_bundle=_zip_bundle(["90210"]),
        )
        assert (tmp_path / "member-pages" / "alice-smith.json").exists()
        assert (tmp_path / zip_entry_path("90210")).exists()
        assert (tmp_path / "homepage" / "bootstrap.json").exists()
        assert result.planned_count == 10
        assert result.written_count == 10

    def test_written_homepage_bootstrap_matches_fallback_service(self, tmp_path: Path) -> None:
        homepage_payload = HomepageFeedPayload(
            snapshot_date=_SNAP_DATE,
            top_changes=[
                MemberMovementSummary(
                    bioguide_id="B001",
                    name="Alice Smith",
                    slug="alice-smith",
                    chamber="house",
                    party="D",
                    state="CA",
                    dimension="conflict_of_interest_risk",
                    score_delta=2.0,
                    abs_delta=2.0,
                    event_count=1,
                    top_evidence_card_ids=["ec-001"],
                )
            ],
            recent_events=[
                RecentEventSummary(
                    feed_event_id="event-001",
                    member_bioguide_id="B001",
                    member_name="Alice Smith",
                    member_slug="alice-smith",
                    dimension="conflict_of_interest_risk",
                    score_delta=2.0,
                    short_explanation="Committee overlap detected.",
                    evidence_card_id="ec-001",
                    occurred_at=_SNAP_DATE,
                )
            ],
            recent_evidence_card_ids=["ec-001"],
        )
        self._run(
            tmp_path,
            member_slugs=["alice-smith"],
            profiles=[_member_profile()],
            member_histories=[_member_history()],
            homepage_payload=homepage_payload,
        )

        artifact_result = get_homepage_bootstrap(snapshot_root=tmp_path)
        assert artifact_result.ok is True

        (tmp_path / "homepage" / "bootstrap.json").unlink()
        fallback_result = get_homepage_bootstrap(snapshot_root=tmp_path)

        assert fallback_result.ok is True
        assert artifact_result.data.model_dump(mode="json") == fallback_result.data.model_dump(mode="json")

    def test_written_zip_entry_matches_fallback_service(self, tmp_path: Path) -> None:
        self._run(
            tmp_path,
            member_slugs=["alice-smith"],
            profiles=[_member_profile()],
            member_histories=[_member_history()],
            zip_feeds=[_zip_feed("90210")],
            zip_bundle=_zip_bundle(["90210"]),
        )

        artifact_result = get_zip_entry("90210", snapshot_root=tmp_path)
        assert artifact_result.ok is True

        (tmp_path / zip_entry_path("90210")).unlink()
        fallback_result = get_zip_entry("90210", snapshot_root=tmp_path)

        assert fallback_result.ok is True
        assert artifact_result.data.model_dump(mode="json") == fallback_result.data.model_dump(mode="json")

    def test_missing_member_row_does_not_fail_run(self, tmp_path: Path) -> None:
        # fetch_member_row_by_slug returns None → member skipped, run still OK
        with (
            mock.patch("src.pipeline.publish_snapshot_run.fetch_current_member_slugs", return_value=["ghost"]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_member_row_by_slug", return_value=None),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_all_evidence_card_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.fetch_homepage_feed_rows", return_value=[]),
            mock.patch("src.pipeline.publish_snapshot_run.assemble_homepage_payload", return_value=_fake_homepage_payload()),
        ):
            result = publish_snapshot_run(
                conn=object(),
                snapshot_id=_SNAP_ID,
                snapshot_date=_SNAP_DATE,
                target_dir=tmp_path,
                zip_bundle_inputs=_zip_bundle([]),
            )
        assert result.succeeded

    def test_no_verification_failures_on_success(self, tmp_path: Path) -> None:
        result = self._run(tmp_path)
        assert result.verification_failures == []

    def test_multiple_members_produce_multiple_files(self, tmp_path: Path) -> None:
        profiles = [_member_profile("alice-smith"), _member_profile("bob-jones")]
        # Adjust the second profile so it represents a distinct member.
        profiles[1] = MemberProfilePayload(
            **{
                **profiles[1].model_dump(),
                "bioguide_id": "B002",
                "slug": "bob-jones",
                "name": "Bob Jones",
            }
        )
        self._run(
            tmp_path,
            member_slugs=["alice-smith", "bob-jones"],
            profiles=profiles,
            member_histories=[_member_history("alice-smith"), _member_history("bob-jones")],
        )
        assert (tmp_path / "members" / "alice-smith.json").exists()
        assert (tmp_path / "members" / "bob-jones.json").exists()
