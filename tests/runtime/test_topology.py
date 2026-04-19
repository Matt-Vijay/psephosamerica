"""End-to-end topology test: verify the runtime layer is wired correctly.

Story: local archive -> local bundle -> local oracle -> publish
       -> verify -> roundtrip -> local read.

Covers:
  - RuntimeContext can be built without a live DB
  - Each run_*_runtime command delegates to its pipeline function
  - Path helpers return coherent repo-relative Paths
  - Source registry covers the canonical pipeline stages
  - zip_bundle_from_dict round-trips a well-formed input dict
  - Local oracle topology chains six stages
  - Verify / roundtrip verify topology against real temp trees
  - Published-read inspect helpers are importable and callable
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.runtime as runtime
from src.export.manifest import manifest_root_sha256
from src.runtime.publish_roundtrip_types import (
    ROUNDTRIP_STAGES,
    PublishRoundtripIssue,
    PublishRoundtripResult,
    PublishRoundtripStageResult,
)
from src.runtime.publish_verify_types import (
    PUBLISH_STAGES,
    PublishVerifyIssue,
    PublishVerifyResult,
    PublishVerifyStageResult,
)


# ---------------------------------------------------------------------------
# 1. All __all__ names are importable from src.runtime
# ---------------------------------------------------------------------------


class TestKeyRuntimeEntryPointsCallable:
    """Verify key runtime entry points are callable — surface completeness is
    tested exhaustively in test_init_exports.py."""

    _ENTRY_POINTS = [
        "build_runtime_context",
        "run_congress_archive_load",
        "run_congress_load_runtime",
        "run_disclosure_artifact_ingest",
        "run_disclosures_load_runtime",
        "run_parse_session",
        "run_publish_runtime",
        "run_recompute_runtime",
        "recompute_snapshot",
        "smoke_oracle_path",
    ]

    def test_key_types_present(self):
        for name in self._ENTRY_POINTS:
            assert callable(getattr(runtime, name)), f"{name} should be callable"


# ---------------------------------------------------------------------------
# 2. RuntimeContext can be built without a DB connection
# ---------------------------------------------------------------------------


class TestRuntimeContextBuilds:
    def _fake_settings(self):
        from src.core.settings import Settings
        return Settings()

    def _fake_taxonomy(self):
        return MagicMock()

    def test_build_with_defaults_returns_runtime_context(self):
        settings = self._fake_settings()
        taxonomy = self._fake_taxonomy()
        ctx = runtime.build_runtime_context(settings, taxonomy)
        assert isinstance(ctx, runtime.RuntimeContext)

    def test_null_resolver_embedded_by_default(self):
        ctx = runtime.build_runtime_context(self._fake_settings(), self._fake_taxonomy())
        # null resolver always returns None regardless of input
        assert ctx.issuer_sector_resolver("Acme", "ACM") is None
        assert ctx.issuer_sector_resolver("X", None) is None

    def test_custom_connect_fn_stored(self):
        def fake_connect(settings):
            return object()

        ctx = runtime.build_runtime_context(
            self._fake_settings(), self._fake_taxonomy(), connect_fn=fake_connect
        )
        assert ctx.connect_fn is fake_connect

    def test_open_connection_calls_connect_fn(self):
        sentinel = object()
        fake_connect = MagicMock(return_value=sentinel)
        ctx = runtime.build_runtime_context(
            self._fake_settings(), self._fake_taxonomy(), connect_fn=fake_connect
        )
        result = runtime.open_connection(ctx)
        assert result is sentinel
        fake_connect.assert_called_once()


# ---------------------------------------------------------------------------
# 3. Path helpers return coherent repo-relative Paths
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_repo_root_is_path(self):
        assert isinstance(runtime.repo_root(), Path)

    def test_repo_root_contains_engineering_spec(self):
        assert (runtime.repo_root() / "ENGINEERING_SPEC_V1.md").exists()

    def test_db_schema_path_under_repo(self):
        p = runtime.db_schema_path()
        assert isinstance(p, Path)
        assert p.parent.name == "db"
        assert p.name == "schema.sql"

    def test_db_migrations_dir_under_repo(self):
        p = runtime.db_migrations_dir()
        assert isinstance(p, Path)
        assert p.parent.name == "db"

    def test_taxonomy_dir_under_data(self):
        p = runtime.taxonomy_dir()
        assert p.parent.name == "data"
        assert p.name == "taxonomy"

    def test_crosswalks_dir_under_data(self):
        p = runtime.crosswalks_dir()
        assert p.parent.name == "data"
        assert p.name == "crosswalks"

    def test_local_publish_root_is_path(self):
        assert isinstance(runtime.local_publish_root(), Path)

    def test_publish_root_is_child_of_repo_root(self):
        assert runtime.local_publish_root().parent == runtime.repo_root()

    def test_all_paths_share_same_repo_root(self):
        root = runtime.repo_root()
        assert runtime.db_schema_path().is_relative_to(root)
        assert runtime.taxonomy_dir().is_relative_to(root)
        assert runtime.crosswalks_dir().is_relative_to(root)
        assert runtime.local_publish_root().is_relative_to(root)
        assert runtime.local_artifact_root().is_relative_to(root)


# ---------------------------------------------------------------------------
# 4. Source registry covers the four canonical pipeline stages
# ---------------------------------------------------------------------------


class TestSourceRegistry:
    _EXPECTED_SLUGS = {
        "congress-gov-api",
        "house-disclosures",
        "senate-disclosures",
        "financial-disclosures",
        "conflict-recompute",
        "snapshot-publish",
    }

    def test_all_sources_returns_tuple(self):
        assert isinstance(runtime.all_sources(), tuple)

    def test_all_four_slugs_present(self):
        slugs = {s.slug for s in runtime.all_sources()}
        assert self._EXPECTED_SLUGS == slugs

    def test_source_by_slug_returns_spec(self):
        for slug in self._EXPECTED_SLUGS:
            spec = runtime.source_by_slug(slug)
            assert spec.slug == slug

    def test_source_by_slug_unknown_raises(self):
        with pytest.raises(KeyError):
            runtime.source_by_slug("does-not-exist")

    def test_source_kinds_are_valid(self):
        valid_kinds = {"official", "supporting", "artifact", "internal"}
        for spec in runtime.all_sources():
            assert spec.source_kind in valid_kinds

    def test_internal_sources_have_no_base_url(self):
        internal = [s for s in runtime.all_sources() if s.source_kind == "internal"]
        for spec in internal:
            assert spec.base_url is None


# ---------------------------------------------------------------------------
# 5. run_congress_load_runtime delegates to run_congress_load
# ---------------------------------------------------------------------------


class TestCongressLoadDelegates:
    _MOD = "src.runtime.congress"

    def test_delegates_to_pipeline(self):
        from src.db.load_report import WarnErrorSummary, build_load_summary
        conn = MagicMock()
        inputs = MagicMock()
        fake_ds = {"id": 1, "slug": "congress-gov-api"}
        fake_summary = build_load_summary([], warn_error=WarnErrorSummary(), run_id=5)

        with (
            patch(f"{self._MOD}.ensure_data_source", return_value=fake_ds),
            patch(f"{self._MOD}.start_ingestion_run", return_value=5),
            patch(f"{self._MOD}.run_congress_load", return_value=fake_summary) as mock_load,
            patch(f"{self._MOD}.finish_ingestion_run"),
            patch(f"{self._MOD}.fail_ingestion_run"),
        ):
            result = runtime.run_congress_load_runtime(conn, inputs)

        mock_load.assert_called_once()
        assert isinstance(result, runtime.CongressLoadResult)
        assert result.run_id == 5
        assert hasattr(result, "data_source")
        assert hasattr(result, "load_summary")

    def test_exception_triggers_fail_and_reraises(self):
        conn = MagicMock()
        inputs = MagicMock()
        fake_ds = {"id": 1}

        with (
            patch(f"{self._MOD}.ensure_data_source", return_value=fake_ds),
            patch(f"{self._MOD}.start_ingestion_run", return_value=9),
            patch(f"{self._MOD}.run_congress_load", side_effect=RuntimeError("boom")),
            patch(f"{self._MOD}.finish_ingestion_run"),
            patch(f"{self._MOD}.fail_ingestion_run") as mock_fail,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                runtime.run_congress_load_runtime(conn, inputs)

        mock_fail.assert_called_once()


# ---------------------------------------------------------------------------
# 6. run_recompute_runtime delegates to run_recompute
# ---------------------------------------------------------------------------


class TestRecomputeDelegates:
    _MOD = "src.runtime.recompute"

    def _empty_result(self):
        from src.pipeline.recompute_run import RecomputeRunResult
        return RecomputeRunResult()

    def test_delegates_to_pipeline(self):
        conn = MagicMock()
        fake_ds = {"id": 2, "slug": "conflict-recompute"}

        with (
            patch(f"{self._MOD}.ensure_data_source", return_value=fake_ds),
            patch(f"{self._MOD}.start_ingestion_run", return_value=11),
            patch(f"{self._MOD}.run_recompute", return_value=self._empty_result()) as mock_run,
            patch(f"{self._MOD}.finish_ingestion_run"),
            patch(f"{self._MOD}.fail_ingestion_run"),
        ):
            result = runtime.run_recompute_runtime(
                conn, dt.date(2026, 1, 1), taxonomy=MagicMock()
            )

        mock_run.assert_called_once()
        assert isinstance(result, runtime.RuntimeRecomputeResult)

    def test_run_type_is_recompute(self):
        conn = MagicMock()
        fake_ds = {"id": 2}

        with (
            patch(f"{self._MOD}.ensure_data_source", return_value=fake_ds),
            patch(f"{self._MOD}.start_ingestion_run", return_value=11) as mock_start,
            patch(f"{self._MOD}.run_recompute", return_value=self._empty_result()),
            patch(f"{self._MOD}.finish_ingestion_run"),
            patch(f"{self._MOD}.fail_ingestion_run"),
        ):
            runtime.run_recompute_runtime(conn, dt.date(2026, 1, 1), taxonomy=MagicMock())

        _args, kwargs = mock_start.call_args
        run_type = kwargs.get("run_type") or _args[2]
        assert run_type == "recompute"


# ---------------------------------------------------------------------------
# 7. run_publish_runtime delegates to publish_snapshot_run
# ---------------------------------------------------------------------------


class TestPublishDelegates:
    _MOD = "src.runtime.publish"

    def _ok_publish_result(self):
        r = MagicMock()
        r.succeeded = True
        r.written_count = 3
        r.verification_failures = []
        return r

    def test_delegates_to_pipeline(self, tmp_path: Path):
        conn = MagicMock()
        fake_ds = {"id": 3, "slug": "snapshot-publish"}
        target_dir = tmp_path / "snap"
        staging_dir = tmp_path / ".staging"

        with (
            patch(f"{self._MOD}.ensure_data_source", return_value=fake_ds),
            patch(f"{self._MOD}.start_ingestion_run", return_value=20),
            patch(f"{self._MOD}.publish_snapshot_run", return_value=self._ok_publish_result()) as mock_pub,
            patch(f"{self._MOD}._make_staging_dir", return_value=staging_dir) as mock_make_staging,
            patch(f"{self._MOD}._promote_staging_dir") as mock_promote,
            patch(f"{self._MOD}.finish_ingestion_run"),
            patch(f"{self._MOD}.fail_ingestion_run"),
        ):
            result = runtime.run_publish_runtime(
                conn, dt.date(2026, 4, 1), target_dir, MagicMock()
            )

        mock_make_staging.assert_called_once_with(target_dir, "2026-04-01")
        mock_pub.assert_called_once()
        assert mock_pub.call_args.kwargs["target_dir"] == staging_dir
        mock_promote.assert_called_once_with(staging_dir, target_dir, "2026-04-01")
        assert isinstance(result, runtime.PublishRuntimeResult)

    def test_default_snapshot_id_is_isoformat(self):
        assert runtime.default_snapshot_id(dt.date(2026, 4, 14)) == "2026-04-14"

    def test_run_type_is_export(self, tmp_path: Path):
        conn = MagicMock()
        fake_ds = {"id": 3}
        target_dir = tmp_path / "publish-root"
        staging_dir = tmp_path / ".staging"

        with (
            patch(f"{self._MOD}.ensure_data_source", return_value=fake_ds),
            patch(f"{self._MOD}.start_ingestion_run", return_value=20) as mock_start,
            patch(f"{self._MOD}.publish_snapshot_run", return_value=self._ok_publish_result()),
            patch(f"{self._MOD}._make_staging_dir", return_value=staging_dir),
            patch(f"{self._MOD}._promote_staging_dir"),
            patch(f"{self._MOD}.finish_ingestion_run"),
            patch(f"{self._MOD}.fail_ingestion_run"),
        ):
            runtime.run_publish_runtime(conn, dt.date(2026, 4, 1), target_dir, MagicMock())

        _args, kwargs = mock_start.call_args
        run_type = kwargs.get("run_type") or _args[2]
        assert run_type == "export"


# ---------------------------------------------------------------------------
# 8. zip_bundle_from_dict: local publish/inspect path shape is coherent
# ---------------------------------------------------------------------------


class TestZipBundleShape:
    """Exercises the local publish input path without filesystem or DB."""

    def _minimal_bundle(self):
        return {
            "zip5_codes": ["90210"],
            "zip_district_rows": [
                {"zip5": "90210", "state": "CA", "district": 33, "population_share": 1.0}
            ],
            "district_member_rows": [
                {
                    "state": "CA",
                    "district": 33,
                    "bioguide_id": "B000001",
                    "full_name": "Alice Doe",
                    "party": "D",
                    "slug": "alice-doe",
                }
            ],
            "senator_rows": [
                {
                    "state": "CA",
                    "bioguide_id": "S000001",
                    "full_name": "Bob Roe",
                    "party": "R",
                    "slug": "bob-roe",
                    "seat": 1,
                }
            ],
        }

    def test_round_trip_returns_zip_bundle_inputs(self):
        from src.pipeline.publish_snapshot_run import ZipBundleInputs
        result = runtime.zip_bundle_from_dict(self._minimal_bundle())
        assert isinstance(result, ZipBundleInputs)

    def test_zip5_codes_preserved(self):
        result = runtime.zip_bundle_from_dict(self._minimal_bundle())
        assert result.zip5_codes == ["90210"]

    def test_missing_top_level_key_raises(self):
        data = self._minimal_bundle()
        del data["senator_rows"]
        with pytest.raises(ValueError, match="senator_rows"):
            runtime.zip_bundle_from_dict(data)

    def test_malformed_row_raises(self):
        data = self._minimal_bundle()
        data["zip_district_rows"] = [{"zip5": "90210"}]  # missing state, district, population_share
        with pytest.raises(ValueError):
            runtime.zip_bundle_from_dict(data)

    def test_load_zip_bundle_reads_file(self, tmp_path):
        import json
        bundle_path = tmp_path / "bundle.json"
        bundle_path.write_text(json.dumps(self._minimal_bundle()), encoding="utf-8")
        from src.pipeline.publish_snapshot_run import ZipBundleInputs
        result = runtime.load_zip_bundle(bundle_path)
        assert isinstance(result, ZipBundleInputs)


# ---------------------------------------------------------------------------
# 9. smoke_process_disclosures delegates to run_disclosures_parse_load_runtime
# ---------------------------------------------------------------------------


class TestSmokeProcessDisclosuresDelegates:
    _MOD = "src.runtime.smoke_process_disclosures"

    def _fake_parse_load_result(self):
        """Minimal DisclosuresParseLoadResult-shaped mock."""
        load_summary = MagicMock()
        load_summary.total_written = 7
        load_summary.ok = True

        load_result = MagicMock()
        load_result.run_id = 42
        load_result.data_source = {"slug": "financial-disclosures"}
        load_result.load_summary = load_summary

        parse_result = MagicMock()
        parse_result.processed_count = 10
        parse_result.succeeded_count = 9
        parse_result.failed_count = 1

        result = MagicMock()
        result.parse_result = parse_result
        result.transform_count = 8
        result.load_result = load_result
        return result

    def test_delegates_to_pipeline(self):
        conn = MagicMock()
        fake_result = self._fake_parse_load_result()

        with patch(
            f"{self._MOD}.run_disclosures_parse_load_runtime",
            return_value=fake_result,
        ) as mock_run:
            summary = runtime.smoke_process_disclosures(conn, Path("/tmp/artifacts"))

        mock_run.assert_called_once()

        assert summary["run_id"] == 42
        assert summary["source_slug"] == "financial-disclosures"
        assert summary["parsed"] == 10
        assert summary["parse_succeeded"] == 9
        assert summary["parse_failed"] == 1
        assert summary["transformed"] == 8
        assert summary["total_written"] == 7
        assert summary["load_ok"] is True

    def test_is_callable(self):
        assert callable(runtime.smoke_process_disclosures)

    def test_summary_keys_are_complete(self):
        conn = MagicMock()
        fake_result = self._fake_parse_load_result()

        with patch(
            f"{self._MOD}.run_disclosures_parse_load_runtime",
            return_value=fake_result,
        ):
            summary = runtime.smoke_process_disclosures(conn, Path("/tmp/artifacts"))

        expected_keys = {
            "run_id", "source_slug", "parsed", "parse_succeeded",
            "parse_failed", "transformed", "total_written", "load_ok",
        }
        assert set(summary.keys()) == expected_keys


# ---------------------------------------------------------------------------
# 10. local_congress_bundle_root path helper
# ---------------------------------------------------------------------------


class TestLocalCongressBundleRoot:
    def test_returns_path(self):
        assert isinstance(runtime.local_congress_bundle_root(), Path)

    def test_is_under_repo_root(self):
        assert runtime.local_congress_bundle_root().is_relative_to(runtime.repo_root())

    def test_ends_with_expected_segments(self):
        p = runtime.local_congress_bundle_root()
        assert p.parent.name == "bundles"
        assert p.name == "congress"

    def test_all_local_paths_share_same_repo_root(self):
        root = runtime.repo_root()
        assert runtime.local_congress_bundle_root().is_relative_to(root)
        assert runtime.local_artifact_root().is_relative_to(root)
        assert runtime.local_publish_root().is_relative_to(root)


# ---------------------------------------------------------------------------
# 11a. local_disclosure_bundle_root path helper
# ---------------------------------------------------------------------------


class TestLocalDisclosureBundleRoot:
    def test_returns_path(self):
        assert isinstance(runtime.local_disclosure_bundle_root(), Path)

    def test_is_under_repo_root(self):
        assert runtime.local_disclosure_bundle_root().is_relative_to(runtime.repo_root())

    def test_ends_with_expected_segments(self):
        p = runtime.local_disclosure_bundle_root()
        assert p.parent.name == "bundles"
        assert p.name == "disclosures"

    def test_all_bundle_roots_share_same_parent(self):
        congress = runtime.local_congress_bundle_root()
        disclosures = runtime.local_disclosure_bundle_root()
        assert congress.parent == disclosures.parent

    def test_all_local_paths_share_same_repo_root(self):
        root = runtime.repo_root()
        assert runtime.local_disclosure_bundle_root().is_relative_to(root)
        assert runtime.local_congress_bundle_root().is_relative_to(root)
        assert runtime.local_artifact_root().is_relative_to(root)
        assert runtime.local_publish_root().is_relative_to(root)


# ---------------------------------------------------------------------------
# 11. smoke_oracle_path chains three stages
# ---------------------------------------------------------------------------


class TestSmokeOraclePathDelegates:
    _MOD = "src.runtime.smoke_oracle"

    def _fake_disclosures_summary(self) -> dict:
        return {
            "run_id": 1,
            "source_slug": "financial-disclosures",
            "parsed": 5,
            "parse_succeeded": 4,
            "parse_failed": 1,
            "transformed": 3,
            "total_written": 15,
            "load_ok": True,
        }

    def _fake_recompute_result(self):
        r = MagicMock()
        r.run_id = 2
        r.data_source = {"slug": "conflict-recompute"}
        r.recompute_result.rule_fires = []
        r.recompute_result.evidence_cards = []
        return r

    def _fake_publish_result(self):
        r = MagicMock()
        r.run_id = 3
        r.snapshot_id = "2025-01-15"
        r.data_source = {"slug": "snapshot-publish"}
        r.publish_result.written_count = 10
        r.publish_result.succeeded = True
        return r

    def _empty_zip_bundle(self):
        from src.pipeline.publish_snapshot_run import ZipBundleInputs
        return ZipBundleInputs(
            zip5_codes=[],
            zip_district_rows=[],
            district_member_rows=[],
            senator_rows=[],
        )

    def test_delegates_three_stages(self):
        conn = MagicMock()
        local_root = Path("/tmp/artifacts")
        snapshot_date = dt.date(2025, 1, 15)
        target_dir = Path("/tmp/oracle-out")
        zip_bundle = self._empty_zip_bundle()

        with (
            patch(f"{self._MOD}.smoke_process_disclosures", return_value=self._fake_disclosures_summary()) as mock_disc,
            patch(f"{self._MOD}.run_recompute_runtime", return_value=self._fake_recompute_result()) as mock_recompute,
            patch(f"{self._MOD}.run_publish_runtime", return_value=self._fake_publish_result()) as mock_pub,
        ):
            result = runtime.smoke_oracle_path(
                conn, local_root, snapshot_date, target_dir, zip_bundle
            )

        mock_disc.assert_called_once()
        mock_recompute.assert_called_once()
        mock_pub.assert_called_once()

        assert set(result.keys()) == {"disclosures", "recompute", "publish"}

    def test_disclosures_stage_keyed_correctly(self):
        conn = MagicMock()
        zip_bundle = self._empty_zip_bundle()

        with (
            patch(f"{self._MOD}.smoke_process_disclosures", return_value=self._fake_disclosures_summary()),
            patch(f"{self._MOD}.run_recompute_runtime", return_value=self._fake_recompute_result()),
            patch(f"{self._MOD}.run_publish_runtime", return_value=self._fake_publish_result()),
        ):
            result = runtime.smoke_oracle_path(
                conn, Path("/tmp"), dt.date(2025, 1, 1), Path("/tmp/out"), zip_bundle
            )

        assert result["disclosures"]["source_slug"] == "financial-disclosures"
        assert result["publish"]["snapshot_id"] == "2025-01-15"

    def test_is_callable(self):
        assert callable(runtime.smoke_oracle_path)


# ---------------------------------------------------------------------------
# 12. Local oracle topology: archive -> bundle -> oracle -> recompute -> publish
# ---------------------------------------------------------------------------


class TestLocalOracleTopology:
    """Verify the local oracle pipeline is wired correctly.

    Story: local archive -> local bundle -> oracle -> recompute -> publish
           -> verify -> roundtrip verify

    run_oracle_local orchestrates six stages.  Tests patch at the
    oracle_local module boundary and return typed fakes so each assertion is
    about the topology contract, not production implementation details.

    LocalOracleRunResult includes verify (PublishVerifyResult) and roundtrip
    (PublishRoundtripResult); both are asserted below.
    """

    _MOD = "src.runtime.oracle_local"

    def _options(self, tmp_path: Path) -> runtime.LocalOracleOptions:
        return runtime.LocalOracleOptions(
            congress_options=runtime.CongressOracleOptions(congress=119),
            snapshot_date=dt.date(2026, 1, 15),
            target_dir=tmp_path / "out",
            snapshot_id="2026-01-15",
        )

    def _fake_oracle_result(self, tmp_path: Path) -> runtime.LocalOracleRunResult:
        """Return a structurally complete LocalOracleRunResult for topology tests."""
        from src.runtime.oracle_contracts import CongressStageSummary, LocalOracleRunResult

        congress = CongressStageSummary(
            run_id=10,
            source_slug="congress-gov-api",
            total_inserted=50,
            total_written=50,
            load_ok=True,
        )
        verify = PublishVerifyResult(
            stages=tuple(
                PublishVerifyStageResult(stage=name, checked=1, issues=())
                for name in PUBLISH_STAGES
            )
        )
        roundtrip = PublishRoundtripResult(
            stages=tuple(
                PublishRoundtripStageResult(stage=name, checked=1, issues=())
                for name in ROUNDTRIP_STAGES
            )
        )
        return LocalOracleRunResult(
            snapshot_id="2026-01-15",
            congress=congress,
            disclosures={
                "run_id": 11,
                "source_slug": "financial-disclosures",
                "parse_succeeded": 5,
                "parse_failed": 1,
                "total_written": 8,
                "load_ok": True,
            },
            recompute={
                "run_id": 12,
                "source_slug": "conflict-recompute",
                "rule_fires": 3,
                "evidence_cards": 1,
            },
            publish={
                "run_id": 13,
                "snapshot_id": "2026-01-15",
                "source_slug": "snapshot-publish",
                "written_count": 4,
                "succeeded": True,
            },
            verify=verify,
            roundtrip=roundtrip,
        )

    def test_six_stage_result_shape(self, tmp_path: Path):
        """run_oracle_local returns a LocalOracleRunResult with all six stage fields."""
        fake_result = self._fake_oracle_result(tmp_path)

        with patch.object(runtime, "run_oracle_local", return_value=fake_result):
            result = runtime.run_oracle_local(
                conn=MagicMock(),
                congress_archive=tmp_path / "archive",
                disclosures_bundle=MagicMock(),
                options=self._options(tmp_path),
            )

        assert isinstance(result, runtime.LocalOracleRunResult)
        # congress (stage 0)
        assert isinstance(result.congress, runtime.CongressStageSummary)
        # disclosures / recompute / publish (stages 1-3) are dicts
        assert isinstance(result.disclosures, dict)
        assert isinstance(result.recompute, dict)
        assert isinstance(result.publish, dict)
        # verify (stage 4) and roundtrip (stage 5) are typed results
        assert isinstance(result.verify, PublishVerifyResult)
        assert isinstance(result.roundtrip, PublishRoundtripResult)

    def test_result_snapshot_id_resolved(self, tmp_path: Path):
        """Explicit snapshot_id is surfaced on the result."""
        fake_result = self._fake_oracle_result(tmp_path)

        with patch.object(runtime, "run_oracle_local", return_value=fake_result):
            result = runtime.run_oracle_local(
                conn=MagicMock(),
                congress_archive=tmp_path / "archive",
                disclosures_bundle=MagicMock(),
                options=self._options(tmp_path),
            )

        assert result.snapshot_id == "2026-01-15"

    def test_congress_stage_is_typed_summary(self, tmp_path: Path):
        """The congress field must be a CongressStageSummary — not a raw dict."""
        fake_result = self._fake_oracle_result(tmp_path)

        with patch.object(runtime, "run_oracle_local", return_value=fake_result):
            result = runtime.run_oracle_local(
                conn=MagicMock(),
                congress_archive=tmp_path / "archive",
                disclosures_bundle=MagicMock(),
                options=self._options(tmp_path),
            )

        assert isinstance(result.congress, runtime.CongressStageSummary)
        assert result.congress.run_id == 10
        assert result.congress.load_ok is True
        assert result.congress.total_written == 50

    def test_all_stage_summary_keys_present(self, tmp_path: Path):
        """Every stage summary carries the keys callers depend on."""
        fake_result = self._fake_oracle_result(tmp_path)

        with patch.object(runtime, "run_oracle_local", return_value=fake_result):
            result = runtime.run_oracle_local(
                conn=MagicMock(),
                congress_archive=tmp_path / "archive",
                disclosures_bundle=MagicMock(),
                options=self._options(tmp_path),
            )

        assert {"run_id", "parse_succeeded", "parse_failed", "total_written", "load_ok"} <= set(result.disclosures)
        assert {"run_id", "rule_fires", "evidence_cards"} <= set(result.recompute)
        assert {"run_id", "snapshot_id", "written_count", "succeeded"} <= set(result.publish)

    def test_verify_stage_is_publish_verify_result(self, tmp_path: Path):
        """verify field must be a PublishVerifyResult."""
        fake_result = self._fake_oracle_result(tmp_path)

        with patch.object(runtime, "run_oracle_local", return_value=fake_result):
            result = runtime.run_oracle_local(
                conn=MagicMock(),
                congress_archive=tmp_path / "archive",
                disclosures_bundle=MagicMock(),
                options=self._options(tmp_path),
            )

        assert isinstance(result.verify, PublishVerifyResult)
        assert len(result.verify.stages) == len(PUBLISH_STAGES)

    def test_roundtrip_stage_is_publish_roundtrip_result(self, tmp_path: Path):
        """roundtrip field must be a PublishRoundtripResult."""
        fake_result = self._fake_oracle_result(tmp_path)

        with patch.object(runtime, "run_oracle_local", return_value=fake_result):
            result = runtime.run_oracle_local(
                conn=MagicMock(),
                congress_archive=tmp_path / "archive",
                disclosures_bundle=MagicMock(),
                options=self._options(tmp_path),
            )

        assert isinstance(result.roundtrip, PublishRoundtripResult)
        assert len(result.roundtrip.stages) == len(ROUNDTRIP_STAGES)

    def test_local_oracle_inputs_surface_available(self, tmp_path: Path):
        """LocalOracleInputs is importable from src.runtime and usable."""
        inputs = runtime.LocalOracleInputs(
            congress_archive=tmp_path / "archive",
            disclosures_bundle=MagicMock(),
        )
        assert isinstance(inputs.congress_archive, Path)

    def test_is_callable(self):
        assert callable(runtime.run_oracle_local)


# ---------------------------------------------------------------------------
# 13. Verify topology: real temp publish trees, no mocks
# ---------------------------------------------------------------------------


class TestVerifyTopology:
    """Verify the verify_publish_local stage against real temp trees.

    Story: publish writes a snapshot tree; verify_publish_local inspects it.

    All tests use real filesystem temp directories — no mocks — so the path
    contracts between publish and verify are concrete and executable.
    """

    _SNAPSHOT_ID = "2026-04-15"

    def _manifest_path(self, root: Path) -> Path:
        return root / "snapshots" / self._SNAPSHOT_ID / "manifest.json"

    def _write_manifest(self, root: Path, entries: list[dict]) -> None:
        """Write a minimal but structurally valid snapshot manifest."""
        import datetime
        import json

        manifest_file = self._manifest_path(root)
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_data = {
            "snapshot_id": self._SNAPSHOT_ID,
            "created_at": datetime.datetime(2026, 4, 15, 0, 0, 0, tzinfo=datetime.timezone.utc).isoformat(),
            "entries": entries,
            "total_files": len(entries),
            "total_bytes": sum(e["size_bytes"] for e in entries),
            "root_sha256": manifest_root_sha256(entries),
        }
        manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    # -- local archive: verify_publish_local is importable and callable ------

    def test_verify_publish_local_callable(self):
        assert callable(runtime.verify_publish_local)

    # -- local bundle: result contracts are the exported public types --------

    def test_result_is_publish_verify_result(self, tmp_path: Path):
        self._write_manifest(tmp_path, [])
        result = runtime.verify_publish_local(tmp_path)
        assert isinstance(result, PublishVerifyResult)

    def test_stage_results_are_publish_verify_stage_result(self, tmp_path: Path):
        self._write_manifest(tmp_path, [])
        result = runtime.verify_publish_local(tmp_path)
        for stage in result.stages:
            assert isinstance(stage, PublishVerifyStageResult)

    # -- local oracle: stage ordering matches PUBLISH_STAGES -----------------

    def test_four_stages_in_canonical_order(self, tmp_path: Path):
        """Stage names must match PUBLISH_STAGES in order."""
        self._write_manifest(tmp_path, [])
        result = runtime.verify_publish_local(tmp_path)
        assert len(result.stages) == len(PUBLISH_STAGES)
        for stage_result, expected_name in zip(result.stages, PUBLISH_STAGES):
            assert stage_result.stage == expected_name

    # -- publish: empty snapshot tree passes all stages ----------------------

    def test_empty_snapshot_ok(self, tmp_path: Path):
        """A manifest with no artifact entries is structurally sound."""
        self._write_manifest(tmp_path, [])
        result = runtime.verify_publish_local(tmp_path)
        assert result.ok, f"Expected ok but got errors: {result.all_issues()}"
        assert result.total_errors == 0

    def test_empty_snapshot_total_checked(self, tmp_path: Path):
        """Manifest stage checks one file; artifact stages each check zero."""
        self._write_manifest(tmp_path, [])
        result = runtime.verify_publish_local(tmp_path)
        manifest_stage = result.stage_result("manifest")
        assert manifest_stage is not None
        assert manifest_stage.checked == 1
        for name in ("profiles", "evidence", "zip"):
            s = result.stage_result(name)
            assert s is not None
            assert s.checked == 0

    # -- verify: missing manifest is detected --------------------------------

    def test_no_manifest_is_not_ok(self, tmp_path: Path):
        """A publish root with no snapshot directory fails verification."""
        result = runtime.verify_publish_local(tmp_path)
        assert not result.ok

    def test_no_manifest_stage_result_lookup(self, tmp_path: Path):
        """stage_result() returns None for unknown stage names."""
        self._write_manifest(tmp_path, [])
        result = runtime.verify_publish_local(tmp_path)
        assert result.stage_result("does-not-exist") is None

    def test_publish_verify_issue_type_available(self, tmp_path: Path):
        """PublishVerifyIssue is importable and used in real results."""
        # Corrupt the manifest so the manifest stage produces an issue.
        manifest_file = self._manifest_path(tmp_path)
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text("not valid json", encoding="utf-8")

        result = runtime.verify_publish_local(tmp_path)
        assert not result.ok
        issues = result.all_issues()
        assert len(issues) >= 1
        assert all(isinstance(i, PublishVerifyIssue) for i in issues)


# ---------------------------------------------------------------------------
# 14. Roundtrip verify topology: publish -> verify_publish_local in one call
# ---------------------------------------------------------------------------


class TestRoundtripVerifyTopology:
    """Verify the verify_publish_roundtrip_local surface.

    Story: verify_publish_roundtrip_local opens a DB connection and runs the
    five-stage DB-backed roundtrip verifier against a local publish tree.

    The DB boundary is patched at verify_roundtrip in publish_roundtrip so
    the test is deterministic and network-free while keeping the command
    orchestration path real.
    """

    _MOD = "src.runtime.commands"

    def _ok_roundtrip_result(self) -> PublishRoundtripResult:
        """Return a fully-passing five-stage roundtrip result."""
        stages = tuple(
            PublishRoundtripStageResult(stage=name, checked=1, issues=())
            for name in ROUNDTRIP_STAGES
        )
        return PublishRoundtripResult(stages=stages)

    def _error_roundtrip_result(self) -> PublishRoundtripResult:
        """Return a five-stage result with one error in the snapshot stage."""
        issue = PublishRoundtripIssue(stage="snapshot", message="missing", severity="error")
        error_stage = PublishRoundtripStageResult(stage="snapshot", checked=0, issues=(issue,))
        ok_stages = tuple(
            PublishRoundtripStageResult(stage=name, checked=0, issues=())
            for name in ROUNDTRIP_STAGES[1:]
        )
        return PublishRoundtripResult(stages=(error_stage, *ok_stages))

    # -- local archive: callable and exported --------------------------------

    def test_verify_publish_roundtrip_local_callable(self):
        assert callable(runtime.verify_publish_roundtrip_local)

    # -- local bundle: result type is PublishRoundtripResult -----------------

    def test_result_is_publish_roundtrip_result(self, tmp_path: Path):
        ok_result = self._ok_roundtrip_result()

        with patch(f"{self._MOD}._verify_roundtrip", return_value=ok_result):
            result = runtime.verify_publish_roundtrip_local(MagicMock(), tmp_path)

        assert isinstance(result, PublishRoundtripResult)

    # -- local oracle: verify_roundtrip is delegated to ----------------------

    def test_verify_roundtrip_called_once(self, tmp_path: Path):
        ok_result = self._ok_roundtrip_result()

        with patch(f"{self._MOD}._verify_roundtrip", return_value=ok_result) as mock_rt:
            runtime.verify_publish_roundtrip_local(MagicMock(), tmp_path)

        mock_rt.assert_called_once()

    def test_publish_root_forwarded_to_verify_roundtrip(self, tmp_path: Path):
        ok_result = self._ok_roundtrip_result()

        with patch(f"{self._MOD}._verify_roundtrip", return_value=ok_result) as mock_rt:
            runtime.verify_publish_roundtrip_local(MagicMock(), tmp_path)

        _args, _ = mock_rt.call_args
        assert tmp_path in _args

    # -- publish: five stages in canonical order -----------------------------

    def test_five_stages_in_roundtrip_stages_order(self, tmp_path: Path):
        ok_result = self._ok_roundtrip_result()

        with patch(f"{self._MOD}._verify_roundtrip", return_value=ok_result):
            result = runtime.verify_publish_roundtrip_local(MagicMock(), tmp_path)

        assert len(result.stages) == len(ROUNDTRIP_STAGES)
        for stage_result, expected_name in zip(result.stages, ROUNDTRIP_STAGES):
            assert stage_result.stage == expected_name

    # -- verify: ok reflects all five stages ---------------------------------

    def test_ok_when_all_stages_pass(self, tmp_path: Path):
        ok_result = self._ok_roundtrip_result()

        with patch(f"{self._MOD}._verify_roundtrip", return_value=ok_result):
            result = runtime.verify_publish_roundtrip_local(MagicMock(), tmp_path)

        assert result.ok

    def test_not_ok_when_snapshot_stage_fails(self, tmp_path: Path):
        error_result = self._error_roundtrip_result()

        with patch(f"{self._MOD}._verify_roundtrip", return_value=error_result):
            result = runtime.verify_publish_roundtrip_local(MagicMock(), tmp_path)

        assert not result.ok

    # -- roundtrip: stage_result lookup works --------------------------------

    def test_stage_result_lookup_by_name(self, tmp_path: Path):
        ok_result = self._ok_roundtrip_result()

        with patch(f"{self._MOD}._verify_roundtrip", return_value=ok_result):
            result = runtime.verify_publish_roundtrip_local(MagicMock(), tmp_path)

        snapshot = result.stage_result("snapshot")
        assert snapshot is not None
        assert snapshot.stage == "snapshot"

    def test_stage_result_returns_none_for_unknown_name(self, tmp_path: Path):
        ok_result = self._ok_roundtrip_result()

        with patch(f"{self._MOD}._verify_roundtrip", return_value=ok_result):
            result = runtime.verify_publish_roundtrip_local(MagicMock(), tmp_path)

        assert result.stage_result("does-not-exist") is None


# ---------------------------------------------------------------------------
# 15. Local read topology: published-read inspect helpers are importable
# ---------------------------------------------------------------------------


class TestLocalReadTopology:
    """Verify the published-read inspect surface.

    Story: after verify, operators call load_local_* helpers to read individual
    artifacts back from the published tree.

    These are pure import/shape tests — no filesystem writes required.
    """

    _INSPECT_HELPERS = [
        "load_local_manifest",
        "load_local_member_profile",
        "load_local_evidence_card",
        "load_local_zip_feed",
    ]

    def test_all_inspect_helpers_callable(self):
        for name in self._INSPECT_HELPERS:
            fn = getattr(runtime, name)
            assert callable(fn), f"{name} should be callable"

    def test_inspect_helpers_in_all(self):
        exported = set(runtime.__all__)
        for name in self._INSPECT_HELPERS:
            assert name in exported, f"{name} missing from runtime.__all__"
