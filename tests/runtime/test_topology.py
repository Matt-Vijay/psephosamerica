"""End-to-end topology test: verify the runtime layer is wired correctly.

Covers:
  - RuntimeContext can be built without a live DB
  - Each run_*_runtime command delegates to its pipeline function
  - All public names in src.runtime.__all__ are importable
  - local_publish_root / repo_root / path helpers return Path objects with the
    expected repo-relative shape
  - zip_bundle_from_dict round-trips a well-formed input dict
  - source_by_slug / all_sources cover the four canonical pipeline stages
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.runtime as runtime


# ---------------------------------------------------------------------------
# 1. All __all__ names are importable from src.runtime
# ---------------------------------------------------------------------------


class TestPublicSurfaceImportable:
    def test_all_names_resolve(self):
        for name in runtime.__all__:
            assert hasattr(runtime, name), f"src.runtime.__all__ lists {name!r} but it is not accessible"

    def test_key_types_present(self):
        assert callable(runtime.build_runtime_context)
        assert callable(runtime.run_congress_load_runtime)
        assert callable(runtime.run_disclosure_artifact_ingest)
        assert callable(runtime.run_disclosures_load_runtime)
        assert callable(runtime.run_parse_session)
        assert callable(runtime.run_publish_runtime)
        assert callable(runtime.run_recompute_runtime)
        assert callable(runtime.recompute_snapshot)


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

    def test_delegates_to_pipeline(self):
        conn = MagicMock()
        fake_ds = {"id": 3, "slug": "snapshot-publish"}

        with (
            patch(f"{self._MOD}.ensure_data_source", return_value=fake_ds),
            patch(f"{self._MOD}.start_ingestion_run", return_value=20),
            patch(f"{self._MOD}.publish_snapshot_run", return_value=self._ok_publish_result()) as mock_pub,
            patch(f"{self._MOD}.finish_ingestion_run"),
            patch(f"{self._MOD}.fail_ingestion_run"),
        ):
            result = runtime.run_publish_runtime(
                conn, dt.date(2026, 4, 1), Path("/tmp/snap"), MagicMock()
            )

        mock_pub.assert_called_once()
        assert isinstance(result, runtime.PublishRuntimeResult)

    def test_default_snapshot_id_is_isoformat(self):
        assert runtime.default_snapshot_id(dt.date(2026, 4, 14)) == "2026-04-14"

    def test_run_type_is_export(self):
        conn = MagicMock()
        fake_ds = {"id": 3}

        with (
            patch(f"{self._MOD}.ensure_data_source", return_value=fake_ds),
            patch(f"{self._MOD}.start_ingestion_run", return_value=20) as mock_start,
            patch(f"{self._MOD}.publish_snapshot_run", return_value=self._ok_publish_result()),
            patch(f"{self._MOD}.finish_ingestion_run"),
            patch(f"{self._MOD}.fail_ingestion_run"),
        ):
            runtime.run_publish_runtime(conn, dt.date(2026, 4, 1), Path("/tmp"), MagicMock())

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
