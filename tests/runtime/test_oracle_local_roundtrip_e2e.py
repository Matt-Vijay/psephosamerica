"""Oracle-local end-to-end tests including publish roundtrip verification.

Goals
-----
- Run run_oracle_local with real temp archives and bundles.
- Verify that the publish tree produced by the oracle passes roundtrip
  verification via verify_roundtrip(conn, target_dir).
- Use real temp publish trees and real local loaders.
- Patch only irreducible DB fetch boundaries.
- No subprocesses. No network calls.

Boundary patch strategy
-----------------------
Oracle stages — same as test_oracle_local_e2e.py:
  src.runtime.congress_archive.run_congress_load_runtime    — DB write
  src.runtime.disclosures_bundle_process.stage_disclosures_bundle — DB write
  src.runtime.disclosures_bundle_process.run_disclosure_parse_runtime — DB+IO
  src.runtime.disclosures_bundle_process.run_disclosures_load_runtime — DB write
  src.runtime.oracle_local.run_recompute_runtime            — entire stage
  src.runtime.oracle_local.run_publish_runtime              — entire stage

The publish mock uses a side_effect that writes a real publish tree to
target_dir via published_roundtrip_fixtures.  This ensures the roundtrip
verifier has real filesystem content to inspect.

Roundtrip boundaries (sub-module level, same as test_publish_roundtrip_e2e):
  src.runtime.publish_roundtrip_profiles.fetch_member_row_by_slug
  src.runtime.publish_roundtrip_profiles.fetch_member_score_snapshot_rows
  src.runtime.publish_roundtrip_profiles.fetch_member_rule_fire_rows
  src.runtime.publish_roundtrip_profiles.fetch_member_committee_rows
  src.runtime.publish_roundtrip_evidence.fetch_all_evidence_card_rows
  src.runtime.publish_roundtrip_ontology.fetch_all_ontology_edge_rows

Focused prediction/ZIP/homepage stage stubs:
  src.runtime.publish_roundtrip.verify_published_prediction_roundtrip → _prediction_stub
  src.runtime.publish_roundtrip.verify_published_zip_roundtrip    → _zip_stub
  src.runtime.publish_roundtrip.verify_published_homepage_roundtrip → _homepage_stub
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from src.db.load_report import TableWriteResult, WarnErrorSummary, build_load_summary
from src.pipeline.recompute_run import RecomputeRunResult
from src.runtime.congress import CongressLoadResult
from src.runtime.disclosures_bundle import DisclosuresBundle, disclosures_bundle_from_dict
from src.runtime.oracle_contracts import (
    CongressOracleOptions,
    LocalOracleOptions,
    LocalOracleRunResult,
)
from src.runtime.oracle_local import run_oracle_local
from src.runtime.publish import PublishRuntimeResult
from src.runtime.publish_roundtrip import verify_roundtrip
from src.runtime.publish_roundtrip_types import (
    PublishRoundtripIssue,
    PublishRoundtripResult,
    PublishRoundtripStageResult,
)
from src.runtime.recompute import RuntimeRecomputeResult
from src.export.writer import serialize_payload
from tests.support.published_roundtrip_fixtures import (
    PublishedRoundtrip,
    assemble_from_homepage_feed_row_set,
    make_roundtrip,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = dt.date(2024, 6, 1)
_CONGRESS = 119
_CONGRESS_OPTIONS = CongressOracleOptions(congress=_CONGRESS)

# ---------------------------------------------------------------------------
# Oracle patch targets
# ---------------------------------------------------------------------------

_CONGRESS_LOAD_RT = "src.runtime.congress_archive.run_congress_load_runtime"
_STAGE_BUNDLE = "src.runtime.disclosures_bundle_process.stage_disclosures_bundle"
_PARSE_RT = "src.runtime.disclosures_bundle_process.run_disclosure_parse_runtime"
_DISC_LOAD_RT = "src.runtime.disclosures_bundle_process.run_disclosures_load_runtime"
_RECOMPUTE_RT = "src.runtime.oracle_local.run_recompute_runtime"
_PUBLISH_RT = "src.runtime.oracle_local.run_publish_runtime"

# Roundtrip boundaries — profiles (sub-module level)
_FETCH_MEMBER_BY_SLUG = "src.runtime.publish_roundtrip_profiles.fetch_member_row_by_slug"
_FETCH_SCORE_ROWS = "src.runtime.publish_roundtrip_profiles.fetch_member_score_snapshot_rows"
_FETCH_RULE_FIRES = "src.runtime.publish_roundtrip_profiles.fetch_member_rule_fire_rows"
_FETCH_COMMITTEES = "src.runtime.publish_roundtrip_profiles.fetch_member_committee_rows"
_FETCH_PROFILE_CARDS = "src.runtime.publish_roundtrip_profiles.fetch_all_evidence_card_rows"
_FETCH_ALL_CARDS = "src.runtime.publish_roundtrip_evidence.fetch_all_evidence_card_rows"
_FETCH_ONTOLOGY_ROWS = "src.runtime.publish_roundtrip_ontology.fetch_all_ontology_edge_rows"

_VERIFY_PREDICTION = "src.runtime.publish_roundtrip.verify_published_prediction_roundtrip"
_VERIFY_ZIP = "src.runtime.publish_roundtrip.verify_published_zip_roundtrip"
_VERIFY_HOMEPAGE = "src.runtime.publish_roundtrip.verify_published_homepage_roundtrip"

# ---------------------------------------------------------------------------
# Focused stubs (mirrored from test_publish_roundtrip_e2e)
# ---------------------------------------------------------------------------


def _prediction_stub(
    conn: Any,
    root: Path,
    manifest: Any,
) -> PublishRoundtripStageResult:
    """Stand-in for verify_published_prediction_roundtrip with the real three-arg contract."""
    return PublishRoundtripStageResult(stage="prediction", checked=0, issues=())


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
# Profile DB side-effect factories
# ---------------------------------------------------------------------------


def _make_profile_side_effects(rt: PublishedRoundtrip):
    """Return side-effect callables for profile DB boundaries aligned with rt."""
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


# ---------------------------------------------------------------------------
# Real input builders
# ---------------------------------------------------------------------------


def _make_congress_archive(tmp_path: Path, congress: int = _CONGRESS) -> Path:
    """Build a minimal real Congress archive directory from inline JSON."""
    root = tmp_path / "congress"
    root.mkdir(parents=True, exist_ok=True)

    members_payload: dict[str, Any] = {
        "members": [
            {
                "bioguideId": "A000001",
                "firstName": "Jane",
                "lastName": "Doe",
                "directOrderName": "Jane Doe",
                "terms": {"item": [{"chamber": "House of Representatives"}]},
                "currentMember": True,
                "state": "CA",
                "partyName": "Democratic",
            }
        ]
    }
    (root / "members.json").write_text(json.dumps(members_payload), encoding="utf-8")
    (root / "committees.json").write_text(json.dumps({"committees": []}), encoding="utf-8")
    (root / "bills.json").write_text(json.dumps({"bills": []}), encoding="utf-8")
    (root / "member_details").mkdir()
    (root / "bill_details").mkdir()
    (root / "cosponsors").mkdir()
    return root


_SHA256 = "a" * 64


def _make_disclosures_bundle(*, include_house: bool = True) -> DisclosuresBundle:
    """Build a real DisclosuresBundle from an inline JSON dict."""
    if not include_house:
        return disclosures_bundle_from_dict({"artifacts": []})

    entry: dict[str, Any] = {
        "source_record_id": "99001",
        "chamber": "house",
        "filing_year": 2024,
        "storage_uri": "house/2024/99001.pdf",
        "source_url": "https://disclosures.house.gov/public_disc/financial-pdfs/2024/99001.pdf",
        "source_slug": "house_disclosures",
        "artifact_kind": "pdf",
        "sha256": _SHA256,
        "index_row": {
            "last_name": "Doe",
            "first_name": "Jane",
            "suffix": "",
            "raw_filing_type": "O",
            "state_dst": "CA08",
            "filing_date": "2024-01-15",
            "doc_id": "99001",
            "filing_kind": "annual",
        },
    }
    return disclosures_bundle_from_dict({"artifacts": [entry]})


# ---------------------------------------------------------------------------
# Fake DB-boundary results
# ---------------------------------------------------------------------------


def _fake_congress_load_result() -> CongressLoadResult:
    we = WarnErrorSummary()
    tr = TableWriteResult(table="member", inserted=1)
    summary = build_load_summary([tr], warn_error=we, run_id=1)
    return CongressLoadResult(
        data_source={"id": 1, "slug": "congress-core"},
        run_id=1,
        load_summary=summary,
    )


def _fake_stage_result() -> MagicMock:
    r = MagicMock(name="stage_result")
    r.staged_count = 0
    r.mirrored_count = 0
    return r


def _fake_parse_runtime_result() -> MagicMock:
    r = MagicMock(name="parse_result")
    r.succeeded_count = 0
    r.failed_count = 0
    r.processed_count = 0
    r.parse_sessions = ()
    return r


def _fake_disclosures_load_result() -> MagicMock:
    r = MagicMock(name="disc_load_result")
    r.run_id = 2
    r.data_source = {"id": 3, "slug": "disclosure-load"}
    r.load_summary.total_written = 0
    r.load_summary.ok = True
    return r


def _fake_recompute_result() -> RuntimeRecomputeResult:
    inner = RecomputeRunResult(rule_fires=[], evidence_cards=[], load_summary=None)
    return RuntimeRecomputeResult(
        data_source={"id": 5, "slug": "conflict-recompute"},
        run_id=7,
        recompute_result=inner,
    )


# ---------------------------------------------------------------------------
# LocalOracleOptions helper
# ---------------------------------------------------------------------------


def _options(publish_dir: Path, *, snapshot_id: str | None = None) -> LocalOracleOptions:
    return LocalOracleOptions(
        congress_options=_CONGRESS_OPTIONS,
        snapshot_date=_SNAPSHOT_DATE,
        target_dir=publish_dir,
        snapshot_id=snapshot_id,
    )


# ---------------------------------------------------------------------------
# Core helper: run oracle + verify roundtrip over a real publish tree
# ---------------------------------------------------------------------------


def _run_oracle_with_roundtrip(
    tmp_path: Path,
    archive: Path,
    bundle: DisclosuresBundle,
    options: LocalOracleOptions,
) -> tuple[LocalOracleRunResult, PublishRoundtripResult]:
    """Run oracle (DB stages patched) over a real publish tree, then verify roundtrip.

    The publish mock writes a real tree using published_roundtrip_fixtures so that
    verify_roundtrip has real filesystem content.  The DB fetch boundaries used
    by verify_roundtrip are patched to return data matching the tree.

    Note: run_oracle_local also calls verify_publish_roundtrip internally after
    the publish stage (oracle stage 5).  All DB stubs must therefore be active
    during the oracle run as well as the explicit rt verify.
    """
    publish_dir = options.target_dir
    publish_dir.mkdir(parents=True, exist_ok=True)

    # Write publish tree before oracle runs so the publish mock can add homepage/feed.json
    rt = make_roundtrip(publish_dir)

    def _publish_se(*_a, **_kw) -> PublishRuntimeResult:
        feed_payload = assemble_from_homepage_feed_row_set(
            rt.homepage_feed_row_set,
            snapshot_date=rt.snapshot_date,
        )
        feed_file = publish_dir / "homepage" / "feed.json"
        feed_file.parent.mkdir(parents=True, exist_ok=True)
        feed_file.write_bytes(serialize_payload(feed_payload))
        publish_inner = MagicMock(name="publish_inner")
        publish_inner.written_count = 5
        publish_inner.succeeded = True
        return PublishRuntimeResult(
            data_source={"id": 6, "slug": "snapshot-publish"},
            run_id=9,
            snapshot_id=_SNAPSHOT_DATE.isoformat(),
            publish_result=publish_inner,
        )

    _by_slug, _score_rows, _rule_fires, _committees, _all_cards = _make_profile_side_effects(rt)
    conn = MagicMock()

    # Apply all patches during oracle run: oracle internally calls verify_publish_roundtrip
    # after the publish stage, so the zip/homepage stubs and DB patches must be active.
    with (
        patch(_CONGRESS_LOAD_RT, return_value=_fake_congress_load_result()),
        patch(_STAGE_BUNDLE, return_value=_fake_stage_result()),
        patch(_PARSE_RT, return_value=_fake_parse_runtime_result()),
        patch(_DISC_LOAD_RT, return_value=_fake_disclosures_load_result()),
        patch(_RECOMPUTE_RT, return_value=_fake_recompute_result()),
        patch(_PUBLISH_RT, side_effect=_publish_se),
        patch(_FETCH_MEMBER_BY_SLUG, side_effect=_by_slug),
        patch(_FETCH_SCORE_ROWS, side_effect=_score_rows),
        patch(_FETCH_RULE_FIRES, side_effect=_rule_fires),
        patch(_FETCH_COMMITTEES, side_effect=_committees),
        patch(_FETCH_PROFILE_CARDS, side_effect=_all_cards),
        patch(_FETCH_ALL_CARDS, side_effect=_all_cards),
        patch(_FETCH_ONTOLOGY_ROWS, return_value=[]),
        patch(_VERIFY_PREDICTION, side_effect=_prediction_stub),
        patch(_VERIFY_ZIP, side_effect=_zip_stub),
        patch(_VERIFY_HOMEPAGE, side_effect=_homepage_stub),
    ):
        oracle_result = run_oracle_local(conn, archive, bundle, options)

    # Run verify_roundtrip explicitly so tests can assert on the result independently.
    with (
        patch(_FETCH_MEMBER_BY_SLUG, side_effect=_by_slug),
        patch(_FETCH_SCORE_ROWS, side_effect=_score_rows),
        patch(_FETCH_RULE_FIRES, side_effect=_rule_fires),
        patch(_FETCH_COMMITTEES, side_effect=_committees),
        patch(_FETCH_PROFILE_CARDS, side_effect=_all_cards),
        patch(_FETCH_ALL_CARDS, side_effect=_all_cards),
        patch(_FETCH_ONTOLOGY_ROWS, return_value=[]),
        patch(_VERIFY_PREDICTION, side_effect=_prediction_stub),
        patch(_VERIFY_ZIP, side_effect=_zip_stub),
        patch(_VERIFY_HOMEPAGE, side_effect=_homepage_stub),
    ):
        rt_result = verify_roundtrip(conn, publish_dir)

    return oracle_result, rt_result


# ---------------------------------------------------------------------------
# Tests: real input object types
# ---------------------------------------------------------------------------


class TestRealInputObjectTypes:
    """Confirm helpers produce real typed objects, not MagicMock stubs."""

    def test_make_congress_archive_returns_real_directory(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        assert archive.is_dir()

    def test_make_congress_archive_has_members_json(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        payload = json.loads((archive / "members.json").read_text())
        assert payload["members"][0]["bioguideId"] == "A000001"

    def test_make_disclosures_bundle_returns_real_bundle(self) -> None:
        bundle = _make_disclosures_bundle()
        assert isinstance(bundle, DisclosuresBundle)

    def test_make_disclosures_bundle_has_one_artifact(self) -> None:
        bundle = _make_disclosures_bundle(include_house=True)
        assert len(bundle.artifacts) == 1

    def test_make_disclosures_bundle_empty_when_requested(self) -> None:
        bundle = _make_disclosures_bundle(include_house=False)
        assert len(bundle.artifacts) == 0


# ---------------------------------------------------------------------------
# Tests: oracle result shape with roundtrip verification
# ---------------------------------------------------------------------------


class TestOracleLocalRoundtripResultShape:
    """Oracle returns a typed result; roundtrip verify returns a typed result."""

    def test_oracle_returns_local_oracle_run_result(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        oracle_result, _ = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert isinstance(oracle_result, LocalOracleRunResult)

    def test_roundtrip_returns_publish_roundtrip_result(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        _, rt_result = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert isinstance(rt_result, PublishRoundtripResult)

    def test_oracle_verify_field_is_typed(self, tmp_path: Path) -> None:
        from src.runtime.publish_verify_types import PublishVerifyResult

        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        oracle_result, _ = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert isinstance(oracle_result.verify, PublishVerifyResult)

    def test_roundtrip_has_eight_stages(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        _, rt_result = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert len(rt_result.stages) == 8

    def test_roundtrip_stage_names(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        _, rt_result = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        names = {s.stage for s in rt_result.stages}
        assert {
            "snapshot",
            "profiles",
            "evidence",
            "ontology",
            "prediction",
            "zip",
            "homepage",
            "lookup",
        } == names


# ---------------------------------------------------------------------------
# Tests: roundtrip passes on a real oracle publish tree
# ---------------------------------------------------------------------------


class TestOracleRoundtripPasses:
    """After oracle runs, verify_roundtrip returns ok=True on the real publish tree."""

    def test_roundtrip_ok_true_after_oracle(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        _, rt_result = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert rt_result.ok is True

    def test_roundtrip_no_errors_after_oracle(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        _, rt_result = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert rt_result.total_errors == 0

    def test_roundtrip_all_stages_ok_after_oracle(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        _, rt_result = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        for stage in rt_result.stages:
            assert stage.ok is True, f"Stage {stage.stage!r} failed: {stage.issues}"

    def test_roundtrip_total_checked_positive(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        _, rt_result = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert rt_result.total_checked >= 1

    def test_roundtrip_all_issues_empty_after_oracle(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        _, rt_result = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert rt_result.all_issues() == []

    def test_oracle_snapshot_id_matches_options(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        oracle_result, _ = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert oracle_result.snapshot_id == _SNAPSHOT_DATE.isoformat()

    def test_explicit_snapshot_id_propagates(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish", snapshot_id="roundtrip-test-001")
        oracle_result, _ = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert oracle_result.snapshot_id == "roundtrip-test-001"


# ---------------------------------------------------------------------------
# Tests: roundtrip fails on a broken oracle publish tree
# ---------------------------------------------------------------------------


class TestOracleRoundtripBrokenTree:
    """Deleting files after the publish tree is built causes roundtrip to fail."""

    def _setup_tree(self, tmp_path: Path) -> tuple[Path, PublishedRoundtrip]:
        """Build publish dir + roundtrip fixture; return (publish_dir, rt)."""
        publish_dir = tmp_path / "publish"
        publish_dir.mkdir(parents=True, exist_ok=True)
        rt = make_roundtrip(publish_dir)
        feed_payload = assemble_from_homepage_feed_row_set(
            rt.homepage_feed_row_set,
            snapshot_date=rt.snapshot_date,
        )
        feed_file = publish_dir / "homepage" / "feed.json"
        feed_file.parent.mkdir(parents=True, exist_ok=True)
        feed_file.write_bytes(serialize_payload(feed_payload))
        return publish_dir, rt

    def _verify_broken(self, publish_dir: Path, rt: PublishedRoundtrip) -> PublishRoundtripResult:
        conn = MagicMock()
        _by_slug, _score_rows, _rule_fires, _committees, _all_cards = _make_profile_side_effects(rt)
        with (
            patch(_FETCH_MEMBER_BY_SLUG, side_effect=_by_slug),
            patch(_FETCH_SCORE_ROWS, side_effect=_score_rows),
            patch(_FETCH_RULE_FIRES, side_effect=_rule_fires),
            patch(_FETCH_COMMITTEES, side_effect=_committees),
            patch(_FETCH_PROFILE_CARDS, side_effect=_all_cards),
            patch(_FETCH_ALL_CARDS, side_effect=_all_cards),
            patch(_FETCH_ONTOLOGY_ROWS, return_value=[]),
            patch(_VERIFY_PREDICTION, side_effect=_prediction_stub),
            patch(_VERIFY_ZIP, side_effect=_zip_stub),
            patch(_VERIFY_HOMEPAGE, side_effect=_homepage_stub),
        ):
            return verify_roundtrip(conn, publish_dir)

    def test_missing_member_profile_fails_roundtrip(self, tmp_path: Path) -> None:
        publish_dir, rt = self._setup_tree(tmp_path)
        member_files = list((publish_dir / "members").glob("*.json"))
        assert member_files
        member_files[0].unlink()
        result = self._verify_broken(publish_dir, rt)
        assert result.ok is False

    def test_missing_evidence_card_fails_roundtrip(self, tmp_path: Path) -> None:
        publish_dir, rt = self._setup_tree(tmp_path)
        ev_files = list((publish_dir / "evidence").glob("*.json"))
        assert ev_files
        ev_files[0].unlink()
        result = self._verify_broken(publish_dir, rt)
        assert result.ok is False

    def test_missing_manifest_fails_roundtrip(self, tmp_path: Path) -> None:
        publish_dir, rt = self._setup_tree(tmp_path)
        manifest_files = list((publish_dir / "snapshots").glob("*/manifest.json"))
        assert manifest_files
        manifest_files[0].unlink()
        result = self._verify_broken(publish_dir, rt)
        assert result.ok is False

    def test_missing_feed_json_fails_roundtrip(self, tmp_path: Path) -> None:
        publish_dir, rt = self._setup_tree(tmp_path)
        feed_file = publish_dir / "homepage" / "feed.json"
        if feed_file.exists():
            feed_file.unlink()
        result = self._verify_broken(publish_dir, rt)
        assert result.ok is False


# ---------------------------------------------------------------------------
# Tests: stage ordering — oracle then roundtrip
# ---------------------------------------------------------------------------


class TestStageOrderingOracleThenRoundtrip:
    """Oracle stages fire first; roundtrip verify runs after publish completes."""

    def test_oracle_db_stages_called_before_roundtrip(self, tmp_path: Path) -> None:
        """Oracle DB stages fire before the roundtrip DB queries."""
        call_log: list[str] = []
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        publish_dir = tmp_path / "publish"
        publish_dir.mkdir(parents=True, exist_ok=True)
        rt = make_roundtrip(publish_dir)

        def _congress_load(*_a, **_kw):
            call_log.append("congress_load")
            return _fake_congress_load_result()

        def _recompute(*_a, **_kw):
            call_log.append("recompute")
            return _fake_recompute_result()

        def _publish(*_a, **_kw):
            call_log.append("publish")
            feed_payload = assemble_from_homepage_feed_row_set(
                rt.homepage_feed_row_set,
                snapshot_date=rt.snapshot_date,
            )
            feed_file = publish_dir / "homepage" / "feed.json"
            feed_file.parent.mkdir(parents=True, exist_ok=True)
            feed_file.write_bytes(serialize_payload(feed_payload))
            publish_inner = MagicMock()
            publish_inner.written_count = 5
            publish_inner.succeeded = True
            return PublishRuntimeResult(
                data_source={"id": 6, "slug": "snapshot-publish"},
                run_id=9,
                snapshot_id=_SNAPSHOT_DATE.isoformat(),
                publish_result=publish_inner,
            )

        _by_slug, _score_rows, _rule_fires, _committees, _all_cards = _make_profile_side_effects(rt)

        conn = MagicMock()
        opts = _options(publish_dir)

        # All stubs must be active during oracle run since oracle calls verify_publish_roundtrip
        with (
            patch(_CONGRESS_LOAD_RT, side_effect=_congress_load),
            patch(_STAGE_BUNDLE, return_value=_fake_stage_result()),
            patch(_PARSE_RT, return_value=_fake_parse_runtime_result()),
            patch(_DISC_LOAD_RT, return_value=_fake_disclosures_load_result()),
            patch(_RECOMPUTE_RT, side_effect=_recompute),
            patch(_PUBLISH_RT, side_effect=_publish),
            patch(_FETCH_MEMBER_BY_SLUG, side_effect=_by_slug),
            patch(_FETCH_SCORE_ROWS, side_effect=_score_rows),
            patch(_FETCH_RULE_FIRES, side_effect=_rule_fires),
            patch(_FETCH_COMMITTEES, side_effect=_committees),
            patch(_FETCH_PROFILE_CARDS, side_effect=_all_cards),
            patch(_FETCH_ALL_CARDS, side_effect=_all_cards),
            patch(_FETCH_ONTOLOGY_ROWS, return_value=[]),
            patch(_VERIFY_PREDICTION, side_effect=_prediction_stub),
            patch(_VERIFY_ZIP, side_effect=_zip_stub),
            patch(_VERIFY_HOMEPAGE, side_effect=_homepage_stub),
        ):
            run_oracle_local(conn, archive, bundle, opts)

        def _rt_slugs(conn2: Any, slug: str):
            call_log.append("rt_slugs")
            return _by_slug(conn2, slug)

        with (
            patch(_FETCH_MEMBER_BY_SLUG, side_effect=_rt_slugs),
            patch(_FETCH_SCORE_ROWS, side_effect=_score_rows),
            patch(_FETCH_RULE_FIRES, side_effect=_rule_fires),
            patch(_FETCH_COMMITTEES, side_effect=_committees),
            patch(_FETCH_PROFILE_CARDS, side_effect=_all_cards),
            patch(_FETCH_ALL_CARDS, side_effect=_all_cards),
            patch(_FETCH_ONTOLOGY_ROWS, return_value=[]),
            patch(_VERIFY_PREDICTION, side_effect=_prediction_stub),
            patch(_VERIFY_ZIP, side_effect=_zip_stub),
            patch(_VERIFY_HOMEPAGE, side_effect=_homepage_stub),
        ):
            verify_roundtrip(conn, publish_dir)

        # Oracle stages come before roundtrip stages in the log
        assert "congress_load" in call_log
        assert "publish" in call_log
        assert "rt_slugs" in call_log
        assert call_log.index("publish") < call_log.index("rt_slugs")

    def test_publish_precedes_roundtrip_verification(self, tmp_path: Path) -> None:
        """The publish step always fires before roundtrip verification."""
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle()
        opts = _options(tmp_path / "publish")
        oracle_result, rt_result = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert oracle_result.publish["succeeded"] is True
        assert isinstance(rt_result, PublishRoundtripResult)


# ---------------------------------------------------------------------------
# Tests: empty bundle flows through without errors
# ---------------------------------------------------------------------------


class TestOracleRoundtripEmptyBundle:
    """Empty disclosure bundle flows through oracle + roundtrip without errors."""

    def test_roundtrip_ok_with_empty_bundle(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle(include_house=False)
        opts = _options(tmp_path / "publish")
        _, rt_result = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert rt_result.ok is True

    def test_oracle_result_type_with_empty_bundle(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle(include_house=False)
        opts = _options(tmp_path / "publish")
        oracle_result, _ = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert isinstance(oracle_result, LocalOracleRunResult)

    def test_roundtrip_result_type_with_empty_bundle(self, tmp_path: Path) -> None:
        archive = _make_congress_archive(tmp_path)
        bundle = _make_disclosures_bundle(include_house=False)
        opts = _options(tmp_path / "publish")
        _, rt_result = _run_oracle_with_roundtrip(tmp_path, archive, bundle, opts)
        assert isinstance(rt_result, PublishRoundtripResult)
