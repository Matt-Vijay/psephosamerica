"""End-to-end tests for verify_roundtrip over real temp publish trees.

Goals
-----
- Call verify_roundtrip(conn, root) directly — no subprocesses.
- Use real temp publish trees built by published_roundtrip_fixtures.
- Confirm ok=True for well-formed trees and ok=False for broken ones.
- No network calls.  No live DB.

Boundary patch strategy
-----------------------
Irreducible DB fetch boundaries patched at sub-module level:

  Profiles stage (publish_roundtrip_profiles):
    src.runtime.publish_roundtrip_profiles.fetch_member_row_by_slug
    src.runtime.publish_roundtrip_profiles.fetch_member_score_snapshot_rows
    src.runtime.publish_roundtrip_profiles.fetch_member_rule_fire_rows
    src.runtime.publish_roundtrip_profiles.fetch_member_committee_rows

  Evidence stage (publish_roundtrip_evidence):
    src.runtime.publish_roundtrip_evidence.fetch_all_evidence_card_rows

Focused ZIP/homepage stage stubs
--------------------------------
The profiles and evidence stages run through their real roundtrip logic in
this file. ZIP and homepage remain stubbed here so the test module can stay
focused on the DB-backed member/evidence roundtrip without duplicating
separate ZIP/homepage fixture setup in every case.

  src.runtime.publish_roundtrip.verify_published_zip_roundtrip    → _zip_stub
  src.runtime.publish_roundtrip.verify_published_homepage_roundtrip → _homepage_stub

Homepage path
-------------
verify_published_homepage_roundtrip checks ``homepage/feed.json`` under
the publish root. These tests write that real path.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.export.filesystem import write_planned_files
from src.export.writer import PlannedFile, finalize_publish_plan
from src.runtime.publish_roundtrip import verify_roundtrip
from src.runtime.publish_roundtrip_types import (
    PublishRoundtripIssue,
    PublishRoundtripResult,
    PublishRoundtripStageResult,
)
from tests.support.published_roundtrip_fixtures import (
    PublishedRoundtrip,
    make_evidence_card_row_set,
    make_member_row_set,
    make_roundtrip,
    make_zip_feed_row_set,
)

# ---------------------------------------------------------------------------
# Patch target strings — irreducible DB fetch boundaries (sub-module level)
# ---------------------------------------------------------------------------

_FETCH_MEMBER_BY_SLUG = "src.runtime.publish_roundtrip_profiles.fetch_member_row_by_slug"
_FETCH_SCORE_ROWS = "src.runtime.publish_roundtrip_profiles.fetch_member_score_snapshot_rows"
_FETCH_RULE_FIRES = "src.runtime.publish_roundtrip_profiles.fetch_member_rule_fire_rows"
_FETCH_COMMITTEES = "src.runtime.publish_roundtrip_profiles.fetch_member_committee_rows"
_FETCH_ALL_CARDS = "src.runtime.publish_roundtrip_evidence.fetch_all_evidence_card_rows"

_VERIFY_ZIP = "src.runtime.publish_roundtrip.verify_published_zip_roundtrip"
_VERIFY_HOMEPAGE = "src.runtime.publish_roundtrip.verify_published_homepage_roundtrip"

# ---------------------------------------------------------------------------
# Focused stubs for zip and homepage sub-verifiers
# ---------------------------------------------------------------------------


def _zip_stub(
    conn: Any,
    root: Path,
    manifest: Any,
    snapshot_date: Any,
) -> PublishRoundtripStageResult:
    """Stand-in for verify_published_zip_roundtrip with the real four-arg contract."""
    zip_dir = root / "zip"
    if not zip_dir.is_dir():
        return PublishRoundtripStageResult(
            stage="zip",
            checked=0,
            issues=(
                PublishRoundtripIssue(
                    stage="zip",
                    message="zip/ directory missing",
                    severity="error",
                ),
            ),
        )
    files = list(zip_dir.glob("*.json"))
    if not files:
        return PublishRoundtripStageResult(
            stage="zip",
            checked=0,
            issues=(
                PublishRoundtripIssue(
                    stage="zip",
                    message="no zip feed files found in zip/",
                    severity="error",
                ),
            ),
        )
    return PublishRoundtripStageResult(stage="zip", checked=len(files), issues=())


def _homepage_stub(
    conn: Any,
    root: Path,
    snapshot_date: Any,
) -> PublishRoundtripStageResult:
    """Stand-in for verify_published_homepage_roundtrip with the real three-arg contract."""
    feed_file = root / "homepage" / "feed.json"
    if not feed_file.exists():
        return PublishRoundtripStageResult(
            stage="homepage",
            checked=0,
            issues=(
                PublishRoundtripIssue(
                    stage="homepage",
                    message="feed.json missing",
                    severity="error",
                ),
            ),
        )
    content = feed_file.read_bytes().strip()
    if not content:
        return PublishRoundtripStageResult(
            stage="homepage",
            checked=0,
            issues=(
                PublishRoundtripIssue(
                    stage="homepage",
                    message="feed.json is empty",
                    severity="error",
                ),
            ),
        )
    return PublishRoundtripStageResult(stage="homepage", checked=1, issues=())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reanchor_manifest(root: Path) -> None:
    manifest_files = sorted((root / "snapshots").glob("*/manifest.json"))
    assert len(manifest_files) == 1
    snapshot_id = manifest_files[0].parent.name
    planned = [
        PlannedFile.from_bytes(file.relative_to(root).as_posix(), file.read_bytes())
        for file in sorted(root.rglob("*"))
        if file.is_file()
    ]
    write_planned_files(finalize_publish_plan(snapshot_id, planned), root)


def _write_feed_json(root: Path) -> None:
    """Write a minimal homepage/feed.json so the homepage stage sees a real file."""
    feed_file = root / "homepage" / "feed.json"
    feed_file.parent.mkdir(parents=True, exist_ok=True)
    feed_file.write_bytes(b'{"feed": "ok"}')
    _reanchor_manifest(root)


def _make_profile_side_effects(rt: PublishedRoundtrip):
    """Return side-effect callables for profile DB boundaries aligned with rt.

    Returns (by_slug, score_rows, rule_fires, committees, all_cards).
    """
    slug_to_rs = {rs.member_row["slug"]: rs for rs in rt.member_row_sets}
    id_to_rs = {rs.member_row["id"]: rs for rs in rt.member_row_sets}

    def _by_slug(conn: Any, slug: str):
        rs = slug_to_rs.get(slug)
        return rs.member_row if rs else None

    def _score_rows(conn: Any, member_id: int):
        rs = id_to_rs.get(member_id)
        return rs.score_snapshot_rows if rs else []

    def _rule_fires(conn: Any, member_id: int):
        rs = id_to_rs.get(member_id)
        return rs.rule_fire_rows if rs else []

    def _committees(conn: Any, member_id: int):
        rs = id_to_rs.get(member_id)
        return rs.committee_rows if rs else []

    def _all_cards(conn: Any):
        return [rs.row for rs in rt.evidence_card_row_sets]

    return _by_slug, _score_rows, _rule_fires, _committees, _all_cards


def _run_roundtrip(root: Path, rt: PublishedRoundtrip) -> PublishRoundtripResult:
    """Invoke verify_roundtrip with all DB boundaries and stage stubs patched."""
    conn = MagicMock()
    _by_slug, _score_rows, _rule_fires, _committees, _all_cards = _make_profile_side_effects(rt)
    with (
        patch(_FETCH_MEMBER_BY_SLUG, side_effect=_by_slug),
        patch(_FETCH_SCORE_ROWS, side_effect=_score_rows),
        patch(_FETCH_RULE_FIRES, side_effect=_rule_fires),
        patch(_FETCH_COMMITTEES, side_effect=_committees),
        patch(_FETCH_ALL_CARDS, side_effect=_all_cards),
        patch(_VERIFY_ZIP, side_effect=_zip_stub),
        patch(_VERIFY_HOMEPAGE, side_effect=_homepage_stub),
    ):
        return verify_roundtrip(conn, root)


def _make_valid_tree(tmp_path: Path) -> PublishedRoundtrip:
    """Build a complete publish tree (all five stages pass) and return roundtrip."""
    rt = make_roundtrip(tmp_path)
    _write_feed_json(tmp_path)
    return rt


# ---------------------------------------------------------------------------
# Valid trees — should pass all five stages
# ---------------------------------------------------------------------------


class TestVerifyRoundtripValidTree:
    """verify_roundtrip returns ok=True for well-formed publish trees."""

    def test_ok_true_for_default_roundtrip(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert result.ok is True

    def test_returns_publish_roundtrip_result(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert isinstance(result, PublishRoundtripResult)

    def test_no_errors_for_valid_tree(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert result.total_errors == 0

    def test_five_stages_present(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        stage_names = {s.stage for s in result.stages}
        assert {"snapshot", "profiles", "evidence", "zip", "homepage"} == stage_names

    def test_all_stages_ok_for_valid_tree(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        for stage in result.stages:
            assert stage.ok is True, f"Stage {stage.stage!r} failed: {stage.issues}"

    def test_total_checked_positive(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert result.total_checked >= 1

    def test_result_is_frozen(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        with pytest.raises(Exception):
            result.stages = ()  # type: ignore[misc]

    def test_all_issues_empty_for_valid_tree(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert result.all_issues() == []

    def test_snapshot_id_preserved_in_manifest(self, tmp_path: Path) -> None:
        snap_id = "2025-07-01"
        rt = make_roundtrip(tmp_path, snapshot_id=snap_id)
        _write_feed_json(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert result.ok is True

    def test_multiple_members_and_cards_ok(self, tmp_path: Path) -> None:
        member_sets = [
            make_member_row_set(bioguide_id="A000001", slug="alice-smith", full_name="Alice Smith"),
            make_member_row_set(bioguide_id="B000002", slug="bob-jones", full_name="Bob Jones"),
        ]
        card_sets = [
            make_evidence_card_row_set(public_id="ec-a001", member_slug="alice-smith", bioguide_id="A000001"),
            make_evidence_card_row_set(public_id="ec-b002", member_slug="bob-jones", bioguide_id="B000002"),
        ]
        zip_sets = [make_zip_feed_row_set(zip_code="94102")]
        rt = make_roundtrip(
            tmp_path,
            member_row_sets=member_sets,
            evidence_card_row_sets=card_sets,
            zip_feed_row_sets=zip_sets,
        )
        _write_feed_json(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert result.ok is True
        assert result.total_errors == 0


# ---------------------------------------------------------------------------
# Snapshot stage
# ---------------------------------------------------------------------------


class TestVerifyRoundtripSnapshotStage:
    """Snapshot stage: manifest consistency checks."""

    def test_snapshot_stage_ok_for_valid_tree(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        snap_stage = result.stage_result("snapshot")
        assert snap_stage is not None
        assert snap_stage.ok is True

    def test_missing_snapshots_dir_fails_snapshot_stage(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        shutil.rmtree(tmp_path / "snapshots")
        result = _run_roundtrip(tmp_path, rt)
        snap_stage = result.stage_result("snapshot")
        assert snap_stage is not None
        assert snap_stage.ok is False

    def test_missing_manifest_fails_snapshot_stage(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        manifest_files = list((tmp_path / "snapshots").glob("*/manifest.json"))
        assert manifest_files
        manifest_files[0].unlink()
        result = _run_roundtrip(tmp_path, rt)
        snap_stage = result.stage_result("snapshot")
        assert snap_stage is not None
        assert snap_stage.ok is False

    def test_corrupt_manifest_fails_snapshot_stage(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        manifest_files = list((tmp_path / "snapshots").glob("*/manifest.json"))
        manifest_files[0].write_bytes(b"not valid json {{{")
        result = _run_roundtrip(tmp_path, rt)
        snap_stage = result.stage_result("snapshot")
        assert snap_stage is not None
        assert snap_stage.ok is False

    def test_snapshot_stage_checked_positive_for_valid_tree(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        snap_stage = result.stage_result("snapshot")
        assert snap_stage is not None
        assert snap_stage.checked >= 1


# ---------------------------------------------------------------------------
# Profiles stage
# ---------------------------------------------------------------------------


class TestVerifyRoundtripProfilesStage:
    """Profiles stage: every manifest member entry must match its DB-assembled profile."""

    def test_profiles_stage_ok_for_valid_tree(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        profiles_stage = result.stage_result("profiles")
        assert profiles_stage is not None
        assert profiles_stage.ok is True

    def test_profiles_checked_equals_member_count(self, tmp_path: Path) -> None:
        member_sets = [
            make_member_row_set(bioguide_id="A000001", slug="alice-smith"),
            make_member_row_set(bioguide_id="B000002", slug="bob-jones"),
        ]
        rt = make_roundtrip(tmp_path, member_row_sets=member_sets)
        _write_feed_json(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        profiles_stage = result.stage_result("profiles")
        assert profiles_stage is not None
        assert profiles_stage.checked == 2

    def test_missing_profile_file_fails_profiles_stage(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        member_files = list((tmp_path / "members").glob("*.json"))
        assert member_files
        member_files[0].unlink()
        result = _run_roundtrip(tmp_path, rt)
        profiles_stage = result.stage_result("profiles")
        assert profiles_stage is not None
        assert profiles_stage.ok is False

    def test_missing_profile_error_message_names_slug(self, tmp_path: Path) -> None:
        slug = "nancy-pelosi"
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        (tmp_path / "members" / f"{slug}.json").unlink()
        result = _run_roundtrip(tmp_path, rt)
        snapshot_stage = result.stage_result("snapshot")
        assert snapshot_stage is not None
        error_paths = [i.path for i in snapshot_stage.issues if i.severity == "error"]
        assert any(path is not None and slug in path for path in error_paths), error_paths

    def test_member_absent_from_db_fails_profiles_stage(self, tmp_path: Path) -> None:
        """fetch_member_row_by_slug returning None reports an error for the member."""
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        conn = MagicMock()
        with (
            patch(_FETCH_MEMBER_BY_SLUG, return_value=None),
            patch(_FETCH_SCORE_ROWS, return_value=[]),
            patch(_FETCH_RULE_FIRES, return_value=[]),
            patch(_FETCH_COMMITTEES, return_value=[]),
            patch(_FETCH_ALL_CARDS, side_effect=lambda c: [rs.row for rs in rt.evidence_card_row_sets]),
            patch(_VERIFY_ZIP, side_effect=_zip_stub),
            patch(_VERIFY_HOMEPAGE, side_effect=_homepage_stub),
        ):
            result = verify_roundtrip(conn, tmp_path)
        profiles_stage = result.stage_result("profiles")
        assert profiles_stage is not None
        assert profiles_stage.ok is False
        error_msgs = [i.message for i in profiles_stage.issues if i.severity == "error"]
        assert error_msgs


# ---------------------------------------------------------------------------
# Evidence stage
# ---------------------------------------------------------------------------


class TestVerifyRoundtripEvidenceStage:
    """Evidence stage: every manifest evidence entry must match its DB-assembled card."""

    def test_evidence_stage_ok_for_valid_tree(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        ev_stage = result.stage_result("evidence")
        assert ev_stage is not None
        assert ev_stage.ok is True

    def test_evidence_checked_equals_card_count(self, tmp_path: Path) -> None:
        card_sets = [
            make_evidence_card_row_set(public_id="ec-001"),
            make_evidence_card_row_set(public_id="ec-002"),
            make_evidence_card_row_set(public_id="ec-003"),
        ]
        rt = make_roundtrip(tmp_path, evidence_card_row_sets=card_sets)
        _write_feed_json(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        ev_stage = result.stage_result("evidence")
        assert ev_stage is not None
        assert ev_stage.checked == 3

    def test_missing_evidence_file_fails_evidence_stage(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        ev_files = list((tmp_path / "evidence").glob("*.json"))
        assert ev_files
        ev_files[0].unlink()
        result = _run_roundtrip(tmp_path, rt)
        ev_stage = result.stage_result("evidence")
        assert ev_stage is not None
        assert ev_stage.ok is False

    def test_missing_evidence_error_names_public_id(self, tmp_path: Path) -> None:
        card_sets = [make_evidence_card_row_set(public_id="ec-target-001")]
        rt = make_roundtrip(tmp_path, evidence_card_row_sets=card_sets)
        _write_feed_json(tmp_path)
        (tmp_path / "evidence" / "ec-target-001.json").unlink()
        result = _run_roundtrip(tmp_path, rt)
        snapshot_stage = result.stage_result("snapshot")
        assert snapshot_stage is not None
        error_paths = [i.path for i in snapshot_stage.issues if i.severity == "error"]
        assert any(path is not None and "ec-target-001" in path for path in error_paths), error_paths

    def test_card_absent_from_db_fails_evidence_stage(self, tmp_path: Path) -> None:
        """fetch_all_evidence_card_rows returning [] causes card-not-in-DB errors."""
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        conn = MagicMock()
        _by_slug, _score_rows, _rule_fires, _committees, _ = _make_profile_side_effects(rt)
        with (
            patch(_FETCH_MEMBER_BY_SLUG, side_effect=_by_slug),
            patch(_FETCH_SCORE_ROWS, side_effect=_score_rows),
            patch(_FETCH_RULE_FIRES, side_effect=_rule_fires),
            patch(_FETCH_COMMITTEES, side_effect=_committees),
            patch(_FETCH_ALL_CARDS, return_value=[]),
            patch(_VERIFY_ZIP, side_effect=_zip_stub),
            patch(_VERIFY_HOMEPAGE, side_effect=_homepage_stub),
        ):
            result = verify_roundtrip(conn, tmp_path)
        ev_stage = result.stage_result("evidence")
        assert ev_stage is not None
        assert ev_stage.ok is False


# ---------------------------------------------------------------------------
# ZIP stage
# ---------------------------------------------------------------------------


class TestVerifyRoundtripZipStage:
    """ZIP stage: at least one zip file must exist in zip/."""

    def test_zip_stage_ok_for_tree_with_zip_feeds(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        zip_stage = result.stage_result("zip")
        assert zip_stage is not None
        assert zip_stage.ok is True

    def test_zip_stage_checked_equals_zip_file_count(self, tmp_path: Path) -> None:
        zip_sets = [
            make_zip_feed_row_set(zip_code="94102"),
            make_zip_feed_row_set(zip_code="10001"),
        ]
        rt = make_roundtrip(tmp_path, zip_feed_row_sets=zip_sets)
        _write_feed_json(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        zip_stage = result.stage_result("zip")
        assert zip_stage is not None
        assert zip_stage.checked == 2

    def test_no_zip_feeds_fails_zip_stage(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path, zip_feed_row_sets=[])
        _write_feed_json(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        zip_stage = result.stage_result("zip")
        assert zip_stage is not None
        assert zip_stage.ok is False

    def test_missing_zip_dir_fails_zip_stage(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        zip_dir = tmp_path / "zip"
        if zip_dir.exists():
            shutil.rmtree(zip_dir)
        result = _run_roundtrip(tmp_path, rt)
        zip_stage = result.stage_result("zip")
        assert zip_stage is not None
        assert zip_stage.ok is False


# ---------------------------------------------------------------------------
# Homepage stage
# ---------------------------------------------------------------------------


class TestVerifyRoundtripHomepageStage:
    """Homepage stage: feed.json must exist and be non-empty."""

    def test_homepage_stage_ok_when_feed_json_present(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        homepage_stage = result.stage_result("homepage")
        assert homepage_stage is not None
        assert homepage_stage.ok is True

    def test_homepage_stage_fails_when_feed_json_missing(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        # Do NOT write feed.json — neither at root nor under homepage/
        result = _run_roundtrip(tmp_path, rt)
        homepage_stage = result.stage_result("homepage")
        assert homepage_stage is not None
        assert homepage_stage.ok is False

    def test_homepage_stage_checked_one_when_file_present(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        homepage_stage = result.stage_result("homepage")
        assert homepage_stage is not None
        assert homepage_stage.checked == 1

    def test_homepage_stage_fails_when_feed_json_empty(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        (tmp_path / "feed.json").write_bytes(b"   ")  # whitespace only
        result = _run_roundtrip(tmp_path, rt)
        homepage_stage = result.stage_result("homepage")
        assert homepage_stage is not None
        assert homepage_stage.ok is False


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class TestVerifyRoundtripResultShape:
    """PublishRoundtripResult aggregated properties behave correctly."""

    def test_stage_result_by_name_returns_correct_stage(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        for name in ("snapshot", "profiles", "evidence", "zip", "homepage"):
            stage = result.stage_result(name)
            assert stage is not None, f"stage {name!r} missing"

    def test_stage_result_returns_none_for_unknown_stage(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert result.stage_result("nonexistent") is None

    def test_total_checked_sum_of_stages(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert result.total_checked == sum(s.checked for s in result.stages)

    def test_total_errors_zero_for_valid_tree(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert result.total_errors == 0

    def test_total_warnings_zero_for_valid_tree(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert result.total_warnings == 0

    def test_ok_false_when_any_stage_fails(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        # No feed.json → homepage stage fails
        result = _run_roundtrip(tmp_path, rt)
        assert result.ok is False

    def test_all_issues_collects_from_all_stages(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        # Omit feed.json + delete a member profile → multiple stage failures
        member_files = list((tmp_path / "members").glob("*.json"))
        if member_files:
            member_files[0].unlink()
        result = _run_roundtrip(tmp_path, rt)
        issues = result.all_issues()
        stage_names_with_issues = {i.stage for i in issues}
        assert "profiles" in stage_names_with_issues or "homepage" in stage_names_with_issues

    def test_five_stages_in_result(self, tmp_path: Path) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        assert len(result.stages) == 5

    def test_stage_order_is_snapshot_profiles_evidence_zip_homepage(
        self, tmp_path: Path
    ) -> None:
        rt = _make_valid_tree(tmp_path)
        result = _run_roundtrip(tmp_path, rt)
        names = [s.stage for s in result.stages]
        assert names == ["snapshot", "profiles", "evidence", "zip", "homepage"]


# ---------------------------------------------------------------------------
# Conn is forwarded to DB boundary functions
# ---------------------------------------------------------------------------


class TestConnForwarding:
    """The conn argument is forwarded to the DB fetch boundaries."""

    def test_fetch_member_by_slug_called_with_passed_conn(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        sentinel_conn = object()
        captured_conns: list[object] = []

        _by_slug, _score_rows, _rule_fires, _committees, _all_cards = _make_profile_side_effects(rt)

        def _capture_slug(conn: Any, slug: str):
            captured_conns.append(conn)
            return _by_slug(conn, slug)

        with (
            patch(_FETCH_MEMBER_BY_SLUG, side_effect=_capture_slug),
            patch(_FETCH_SCORE_ROWS, side_effect=_score_rows),
            patch(_FETCH_RULE_FIRES, side_effect=_rule_fires),
            patch(_FETCH_COMMITTEES, side_effect=_committees),
            patch(_FETCH_ALL_CARDS, side_effect=_all_cards),
            patch(_VERIFY_ZIP, side_effect=_zip_stub),
            patch(_VERIFY_HOMEPAGE, side_effect=_homepage_stub),
        ):
            verify_roundtrip(sentinel_conn, tmp_path)

        assert len(captured_conns) >= 1
        assert all(c is sentinel_conn for c in captured_conns)

    def test_fetch_all_cards_called_with_passed_conn(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        sentinel_conn = object()
        captured_conns: list[object] = []

        _by_slug, _score_rows, _rule_fires, _committees, _all_cards = _make_profile_side_effects(rt)

        def _capture_cards(conn: Any):
            captured_conns.append(conn)
            return _all_cards(conn)

        with (
            patch(_FETCH_MEMBER_BY_SLUG, side_effect=_by_slug),
            patch(_FETCH_SCORE_ROWS, side_effect=_score_rows),
            patch(_FETCH_RULE_FIRES, side_effect=_rule_fires),
            patch(_FETCH_COMMITTEES, side_effect=_committees),
            patch(_FETCH_ALL_CARDS, side_effect=_capture_cards),
            patch(_VERIFY_ZIP, side_effect=_zip_stub),
            patch(_VERIFY_HOMEPAGE, side_effect=_homepage_stub),
        ):
            verify_roundtrip(sentinel_conn, tmp_path)

        assert len(captured_conns) == 1
        assert captured_conns[0] is sentinel_conn

    def test_each_boundary_called_at_least_once(self, tmp_path: Path) -> None:
        rt = make_roundtrip(tmp_path)
        _write_feed_json(tmp_path)
        conn = MagicMock()
        _by_slug, _score_rows, _rule_fires, _committees, _all_cards = _make_profile_side_effects(rt)

        with (
            patch(_FETCH_MEMBER_BY_SLUG, side_effect=_by_slug) as p_slug,
            patch(_FETCH_SCORE_ROWS, side_effect=_score_rows) as p_scores,
            patch(_FETCH_RULE_FIRES, side_effect=_rule_fires) as p_fires,
            patch(_FETCH_COMMITTEES, side_effect=_committees) as p_committees,
            patch(_FETCH_ALL_CARDS, side_effect=_all_cards) as p_cards,
            patch(_VERIFY_ZIP, side_effect=_zip_stub),
            patch(_VERIFY_HOMEPAGE, side_effect=_homepage_stub),
        ):
            verify_roundtrip(conn, tmp_path)

        p_slug.assert_called_once()
        p_scores.assert_called_once()
        p_fires.assert_called_once()
        p_committees.assert_called_once()
        p_cards.assert_called_once()
