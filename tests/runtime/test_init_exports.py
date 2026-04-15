"""Assert the runtime package exposes its intended operator-facing surface.

Tests cover three properties:
  1. Every name in __all__ is actually importable from src.runtime.
  2. Each surface group exports the expected names — no accidental omissions.
  3. No private or internal names leak into __all__.
"""

import src.runtime as runtime


# ---------------------------------------------------------------------------
# 1. __all__ is complete and importable
# ---------------------------------------------------------------------------


def test_all_names_are_importable():
    for name in runtime.__all__:
        assert hasattr(runtime, name), f"src.runtime.__all__ lists {name!r} but it is not importable"


# ---------------------------------------------------------------------------
# 2. Surface groups are present
# ---------------------------------------------------------------------------

_APP_SURFACE = {
    "OpenPactRuntime",
    "bootstrap_database",
    "build_runtime",
    "load_initial_migration_sql",
    "load_schema_sql",
    "open_runtime_connection",
}

_CONTEXT_SURFACE = {
    "RuntimeContext",
    "build_runtime_context",
    "null_issuer_sector_resolver",
    "open_connection",
}

_PATHS_SURFACE = {
    "crosswalks_dir",
    "db_migrations_dir",
    "db_schema_path",
    "local_artifact_root",
    "local_publish_root",
    "repo_root",
    "taxonomy_dir",
}

_SOURCE_SURFACE = {
    "CONFLICT_RECOMPUTE",
    "CONGRESS_CORE",
    "DISCLOSURE_LOAD",
    "HOUSE_DISCLOSURES",
    "SENATE_DISCLOSURES",
    "SNAPSHOT_PUBLISH",
    "SourceSpec",
    "all_sources",
    "source_by_slug",
}

_RUNTIME_FLOW_SURFACE = {
    "CongressLoadResult",
    "DisclosureArtifactIngestResult",
    "DisclosuresLoadRuntimeResult",
    "ParseSessionResult",
    "PublishRuntimeResult",
    "RuntimeRecomputeResult",
    "current_data_sources",
    "default_snapshot_id",
    "get_runtime_status",
    "get_runtime_status_summary",
    "latest_ingestion_runs",
    "latest_parse_runs",
    "latest_source_artifacts",
    "load_congress",
    "load_disclosures",
    "load_local_evidence_card",
    "load_local_manifest",
    "load_local_member_profile",
    "load_local_zip_feed",
    "load_zip_bundle",
    "publish_snapshot",
    "recompute_snapshot",
    "run_congress_load_runtime",
    "run_disclosure_artifact_ingest",
    "run_disclosures_load_runtime",
    "run_parse_session",
    "run_publish_runtime",
    "run_recompute_runtime",
    "runtime_status_dict",
    "smoke_publish",
    "smoke_recompute",
    "zip_bundle_from_dict",
}

_EXPECTED = _APP_SURFACE | _CONTEXT_SURFACE | _PATHS_SURFACE | _SOURCE_SURFACE | _RUNTIME_FLOW_SURFACE


def test_app_surface_exported():
    exported = set(runtime.__all__)
    missing = _APP_SURFACE - exported
    assert not missing, f"app surface missing from __all__: {missing}"


def test_context_surface_exported():
    exported = set(runtime.__all__)
    missing = _CONTEXT_SURFACE - exported
    assert not missing, f"context surface missing from __all__: {missing}"


def test_paths_surface_exported():
    exported = set(runtime.__all__)
    missing = _PATHS_SURFACE - exported
    assert not missing, f"paths surface missing from __all__: {missing}"


def test_source_surface_exported():
    exported = set(runtime.__all__)
    missing = _SOURCE_SURFACE - exported
    assert not missing, f"source surface missing from __all__: {missing}"


def test_runtime_flow_surface_exported():
    exported = set(runtime.__all__)
    missing = _RUNTIME_FLOW_SURFACE - exported
    assert not missing, f"runtime-flow surface missing from __all__: {missing}"


# ---------------------------------------------------------------------------
# 3. No internal names leak out
# ---------------------------------------------------------------------------

_KNOWN_INTERNAL_PREFIXES = ("_",)
_KNOWN_INTERNAL_NAMES = {
    # submodule names — callers should import surfaces, not raw modules
    "app",
    "congress",
    "context",
    "disclosures",
    "disclosures_artifacts",
    "inspect",
    "parse_runs",
    "paths",
    "publish",
    "recompute",
    "smoke",
    "sources",
    "status",
    "zip_bundle",
}


def test_no_private_names_in_all():
    for name in runtime.__all__:
        assert not name.startswith("_"), f"private name {name!r} in __all__"


def test_no_submodule_names_in_all():
    exported = set(runtime.__all__)
    leaked = _KNOWN_INTERNAL_NAMES & exported
    assert not leaked, f"submodule names should not appear in __all__: {leaked}"


def test_all_is_exactly_expected_surface():
    exported = set(runtime.__all__)
    extra = exported - _EXPECTED
    assert not extra, f"unexpected names in __all__ (tighten or update _EXPECTED): {extra}"
    missing = _EXPECTED - exported
    assert not missing, f"expected names missing from __all__: {missing}"
