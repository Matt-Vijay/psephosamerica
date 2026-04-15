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

_COMMAND_SURFACE = {
    "load_congress",
    "load_congress_local",
    "load_disclosures",
    "process_disclosures_local",
    "publish_snapshot",
    "recompute_snapshot",
    "run_oracle_local_command",
    "verify_publish_local",
    "verify_publish_roundtrip_local",
}

_CONTEXT_SURFACE = {
    "RuntimeContext",
    "build_runtime_context",
    "null_issuer_sector_resolver",
    "open_connection",
}

_INSPECT_SURFACE = {
    "load_local_evidence_card",
    "load_local_manifest",
    "load_local_member_profile",
    "load_local_zip_feed",
}

_PATHS_SURFACE = {
    "crosswalks_dir",
    "db_migrations_dir",
    "db_schema_path",
    "local_artifact_root",
    "local_congress_bundle_root",
    "local_disclosure_bundle_root",
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

_RESULT_SURFACE = {
    "CongressLoadResult",
    "CongressOracleOptions",
    "DisclosuresLoadRuntimeResult",
    "LocalOracleOptions",
    "LocalOracleRunResult",
    "PublishRuntimeResult",
    "PublishRoundtripResult",
    "PublishVerifyResult",
    "RuntimeRecomputeResult",
}

_STATUS_SURFACE = {
    "get_runtime_status",
    "get_runtime_status_summary",
}

_ZIP_BUNDLE_SURFACE = {
    "load_zip_bundle",
    "zip_bundle_from_dict",
}

_EXPECTED = (
    _APP_SURFACE
    | _COMMAND_SURFACE
    | _CONTEXT_SURFACE
    | _INSPECT_SURFACE
    | _PATHS_SURFACE
    | _SOURCE_SURFACE
    | _RESULT_SURFACE
    | _STATUS_SURFACE
    | _ZIP_BUNDLE_SURFACE
)


def test_app_surface_exported():
    exported = set(runtime.__all__)
    missing = _APP_SURFACE - exported
    assert not missing, f"app surface missing from __all__: {missing}"


def test_command_surface_exported():
    exported = set(runtime.__all__)
    missing = _COMMAND_SURFACE - exported
    assert not missing, f"command surface missing from __all__: {missing}"


def test_context_surface_exported():
    exported = set(runtime.__all__)
    missing = _CONTEXT_SURFACE - exported
    assert not missing, f"context surface missing from __all__: {missing}"


def test_inspect_surface_exported():
    exported = set(runtime.__all__)
    missing = _INSPECT_SURFACE - exported
    assert not missing, f"inspect surface missing from __all__: {missing}"


def test_paths_surface_exported():
    exported = set(runtime.__all__)
    missing = _PATHS_SURFACE - exported
    assert not missing, f"paths surface missing from __all__: {missing}"


def test_source_surface_exported():
    exported = set(runtime.__all__)
    missing = _SOURCE_SURFACE - exported
    assert not missing, f"source surface missing from __all__: {missing}"


def test_result_surface_exported():
    exported = set(runtime.__all__)
    missing = _RESULT_SURFACE - exported
    assert not missing, f"result surface missing from __all__: {missing}"


def test_status_surface_exported():
    exported = set(runtime.__all__)
    missing = _STATUS_SURFACE - exported
    assert not missing, f"status surface missing from __all__: {missing}"


def test_zip_bundle_surface_exported():
    exported = set(runtime.__all__)
    missing = _ZIP_BUNDLE_SURFACE - exported
    assert not missing, f"zip bundle surface missing from __all__: {missing}"


# ---------------------------------------------------------------------------
# 3. No internal names leak out
# ---------------------------------------------------------------------------

_KNOWN_INTERNAL_PREFIXES = ("_",)
_KNOWN_INTERNAL_NAMES = {
    # submodule names — callers should import surfaces, not raw modules
    "app",
    "congress",
    "congress_archive",
    "context",
    "disclosures",
    "disclosures_artifacts",
    "disclosures_parse",
    "inspect",
    "main",
    "oracle_contracts",
    "oracle_local",
    "output",
    "parse_runs",
    "paths",
    "publish",
    "publish_roundtrip",
    "publish_roundtrip_profiles",
    "publish_roundtrip_types",
    "publish_verify",
    "publish_verify_evidence",
    "publish_verify_manifest",
    "publish_verify_profiles",
    "publish_verify_types",
    "publish_verify_zip",
    "recompute",
    "smoke",
    "smoke_oracle",
    "sources",
    "status",
    "zip_bundle",
}

# Names that are importable from src.runtime but intentionally excluded
# from __all__ because they are internal implementation details.
_KNOWN_INTERNAL_EXPORTS = {
    # verify/roundtrip stage-level types
    "PUBLISH_STAGES",
    "PublishVerifyIssue",
    "PublishVerifyStageResult",
    "ROUNDTRIP_STAGES",
    "PublishRoundtripIssue",
    "PublishRoundtripStageResult",
    # internal pipeline run functions
    "run_congress_archive_load",
    "run_congress_load_runtime",
    "run_oracle_local",
    "run_disclosure_artifact_ingest",
    "run_disclosure_parse_runtime",
    "run_disclosures_parse_load_runtime",
    "run_disclosures_load_runtime",
    "run_parse_session",
    "run_publish_runtime",
    "run_recompute_runtime",
    # internal result types
    "CongressStageSummary",
    "DisclosureArtifactIngestResult",
    "DisclosureParseRuntimeResult",
    "DisclosuresParseLoadResult",
    "LocalOracleInputs",
    "ParseSessionResult",
    # internal status queries
    "current_data_sources",
    "latest_ingestion_runs",
    "latest_parse_runs",
    "latest_source_artifacts",
    "runtime_status_dict",
    # smoke testing utilities
    "smoke_oracle_path",
    "smoke_process_disclosures",
    "smoke_publish",
    "smoke_recompute",
    # internal helpers
    "default_snapshot_id",
}


def test_no_private_names_in_all():
    for name in runtime.__all__:
        assert not name.startswith("_"), f"private name {name!r} in __all__"


def test_no_submodule_names_in_all():
    exported = set(runtime.__all__)
    leaked = _KNOWN_INTERNAL_NAMES & exported
    assert not leaked, f"submodule names should not appear in __all__: {leaked}"


def test_no_internal_exports_in_all():
    exported = set(runtime.__all__)
    leaked = _KNOWN_INTERNAL_EXPORTS & exported
    assert not leaked, f"internal names should not appear in __all__: {leaked}"


def test_internal_exports_still_importable():
    """Internal names are excluded from __all__ but must remain importable."""
    for name in _KNOWN_INTERNAL_EXPORTS:
        assert hasattr(runtime, name), (
            f"{name!r} was removed from __all__ but is no longer importable — "
            f"keep the import in __init__.py so internal callers still work"
        )


def test_all_is_exactly_expected_surface():
    exported = set(runtime.__all__)
    extra = exported - _EXPECTED
    assert not extra, f"unexpected names in __all__ (tighten or update _EXPECTED): {extra}"
    missing = _EXPECTED - exported
    assert not missing, f"expected names missing from __all__: {missing}"
