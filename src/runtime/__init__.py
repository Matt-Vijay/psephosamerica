# App surface
from .app import OpenPactRuntime, build_runtime, open_runtime_connection
from .bootstrap import bootstrap_database, load_initial_migration_sql, load_schema_sql

# Context surface
from .context import (
    RuntimeContext,
    build_runtime_context,
    null_contribution_sector_resolver,
    null_issuer_sector_resolver,
    open_connection,
)
from .inspect import (
    load_local_current_member_lookup,
    load_local_evidence_card,
    load_local_history_backfill_report,
    load_local_history_event,
    load_local_history_event_page,
    load_local_history_bootstrap,
    load_local_history_coverage,
    load_local_history_preset_range,
    load_local_homepage_bootstrap,
    load_local_homepage_feed,
    load_local_manifest,
    load_local_member_change_summary,
    load_local_member_history_coverage,
    load_local_member_history_coverage_index,
    load_local_member_history_chart,
    load_local_member_history,
    load_local_member_history_page,
    load_local_member_timeline_index,
    load_local_member_timeline_page,
    load_local_member_timeline_dimension,
    load_local_member_timeline_year,
    load_local_member_page,
    load_local_member_preset_compare,
    load_local_member_profile,
    load_local_snapshot_preset_compare,
    load_local_member_trend_summary,
    load_local_movement_window,
    load_local_prediction_bootstrap,
    load_local_prediction_committee_context,
    load_local_prediction_committee_readiness,
    load_local_prediction_member_context,
    load_local_prediction_member_readiness,
    load_local_prediction_readiness,
    load_local_prediction_readiness_index,
    load_local_prediction_sector_context,
    load_local_prediction_sector_readiness,
    load_local_prediction_source_context,
    load_local_prediction_source_index,
    load_local_prediction_topology,
    load_local_snapshot_index,
    load_local_zip_entry,
    load_local_zip_feed,
    list_local_snapshot_ids,
    search_local_current_member_lookup,
)

# Paths surface
from .paths import (
    crosswalks_dir,
    db_migrations_dir,
    db_schema_path,
    local_artifact_root,
    local_congress_bundle_root,
    local_disclosure_bundle_root,
    local_publish_root,
    repo_root,
    taxonomy_dir,
)

# Source surface
from .sources import (
    CONFLICT_RECOMPUTE,
    CONGRESS_CORE,
    DISCLOSURE_LOAD,
    FEC_BULK,
    HOUSE_DISCLOSURES,
    MEMBER_FEC_CROSSWALK,
    SENATE_DISCLOSURES,
    SNAPSHOT_PUBLISH,
    SourceSpec,
    all_sources,
    source_by_slug,
)
from .smoke_process_disclosures import (
    smoke_process_disclosures as smoke_process_disclosures,
)

# Result types (returned by operator commands)
from .congress import CongressLoadResult
from .disclosures import DisclosuresLoadRuntimeResult
from .fec import FecBulkFilePaths, FecLocalLoadResult
from .member_fec_crosswalk import MemberFecCrosswalkLoadResult
from .oracle_contracts import (
    CongressOracleOptions,
    LocalOracleOptions,
    LocalOracleRunResult,
)
from .publish_roundtrip_types import PublishRoundtripResult
from .publish_verify_types import PublishVerifyResult
from .recompute import RuntimeRecomputeResult

# Status surface
_LAZY_COMMAND_EXPORTS = {
    "load_congress",
    "load_congress_local",
    "load_disclosures",
    "load_fec_local",
    "load_member_fec_crosswalk_local",
    "process_disclosures_local",
    "publish_snapshot",
    "recompute_snapshot",
    "run_history_backfill_local_command",
    "run_oracle_local_command",
    "verify_history_aggregate_local",
    "verify_publish_local",
    "verify_publish_roundtrip_local",
}

_LAZY_HISTORY_BACKFILL_EXPORTS = {
    "CongressDateWindow",
    "check_history_backfill_inputs",
    "HistoryBackfillAttempt",
    "HistoryBackfillCongressArchiveInputsPayload",
    "HistoryBackfillDisclosuresBundleInputsPayload",
    "HistoryBackfillExecutionResult",
    "HistoricalSnapshotTarget",
    "HistoryBackfillInputReadinessPayload",
    "HistoryBackfillPlan",
    "LocalHistoryAggregateSummary",
    "LocalHistoryBackfillResult",
    "congress_term_bounds",
    "execute_history_backfill",
    "derive_weekly_snapshot_dates",
    "load_history_backfill_report",
    "plan_congress_history_backfill",
    "resolve_congress_date_window",
    "run_local_history_backfill",
}

_LAZY_RESULT_EXPORTS = {
    "PublishRuntimeResult": (".publish", "PublishRuntimeResult"),
}

_LAZY_STATUS_EXPORTS = {
    "get_runtime_status": (".status_command", "get_runtime_status"),
    "get_runtime_status_summary": (".status_command", "get_runtime_status_summary"),
}

_LAZY_ZIP_BUNDLE_EXPORTS = {
    "load_zip_bundle": (".zip_bundle", "load_zip_bundle"),
    "zip_bundle_from_dict": (".zip_bundle", "zip_bundle_from_dict"),
}

_LAZY_INTERNAL_EXPORTS = {
    "run_congress_load_runtime": (".congress", "run_congress_load_runtime"),
    "run_congress_archive_load": (".congress_archive", "run_congress_archive_load"),
    "run_disclosures_load_runtime": (".disclosures", "run_disclosures_load_runtime"),
    "DisclosureArtifactIngestResult": (".disclosures_artifacts", "DisclosureArtifactIngestResult"),
    "run_disclosure_artifact_ingest": (".disclosures_artifacts", "run_disclosure_artifact_ingest"),
    "DisclosuresParseLoadResult": (".disclosures_load_from_parse", "DisclosuresParseLoadResult"),
    "run_disclosures_parse_load_runtime": (
        ".disclosures_load_from_parse",
        "run_disclosures_parse_load_runtime",
    ),
    "DisclosureParseRuntimeResult": (".disclosures_parse", "DisclosureParseRuntimeResult"),
    "run_disclosure_parse_runtime": (".disclosures_parse", "run_disclosure_parse_runtime"),
    "CongressStageSummary": (".oracle_contracts", "CongressStageSummary"),
    "LocalOracleInputs": (".oracle_contracts", "LocalOracleInputs"),
    "run_oracle_local": (".oracle_local", "run_oracle_local"),
    "ParseSessionResult": (".parse_runs", "ParseSessionResult"),
    "run_parse_session": (".parse_runs", "run_parse_session"),
    "default_snapshot_id": (".publish", "default_snapshot_id"),
    "run_publish_runtime": (".publish", "run_publish_runtime"),
    "ROUNDTRIP_STAGES": (".publish_roundtrip_types", "ROUNDTRIP_STAGES"),
    "PublishRoundtripIssue": (".publish_roundtrip_types", "PublishRoundtripIssue"),
    "PublishRoundtripStageResult": (".publish_roundtrip_types", "PublishRoundtripStageResult"),
    "PUBLISH_STAGES": (".publish_verify_types", "PUBLISH_STAGES"),
    "PublishVerifyIssue": (".publish_verify_types", "PublishVerifyIssue"),
    "PublishVerifyStageResult": (".publish_verify_types", "PublishVerifyStageResult"),
    "run_recompute_runtime": (".recompute", "run_recompute_runtime"),
    "smoke_publish": (".smoke", "smoke_publish"),
    "smoke_recompute": (".smoke", "smoke_recompute"),
    "smoke_oracle_path": (".smoke_oracle", "smoke_oracle_path"),
    "current_data_sources": (".status", "current_data_sources"),
    "latest_ingestion_runs": (".status", "latest_ingestion_runs"),
    "latest_parse_runs": (".status", "latest_parse_runs"),
    "latest_source_artifacts": (".status", "latest_source_artifacts"),
    "runtime_status_dict": (".status", "runtime_status_dict"),
}


def __getattr__(name: str) -> object:
    if name in _LAZY_COMMAND_EXPORTS:
        from . import commands as _commands

        return getattr(_commands, name)
    if name in _LAZY_HISTORY_BACKFILL_EXPORTS:
        from . import history_backfill as _history_backfill
        from . import history_backfill_types as _history_backfill_types

        if hasattr(_history_backfill, name):
            return getattr(_history_backfill, name)
        return getattr(_history_backfill_types, name)
    if name in _LAZY_RESULT_EXPORTS:
        import importlib

        module_name, attr_name = _LAZY_RESULT_EXPORTS[name]
        module = importlib.import_module(module_name, __name__)
        return getattr(module, attr_name)
    if name in _LAZY_STATUS_EXPORTS:
        import importlib

        module_name, attr_name = _LAZY_STATUS_EXPORTS[name]
        module = importlib.import_module(module_name, __name__)
        return getattr(module, attr_name)
    if name in _LAZY_ZIP_BUNDLE_EXPORTS:
        import importlib

        module_name, attr_name = _LAZY_ZIP_BUNDLE_EXPORTS[name]
        module = importlib.import_module(module_name, __name__)
        return getattr(module, attr_name)
    if name in _LAZY_INTERNAL_EXPORTS:
        import importlib

        module_name, attr_name = _LAZY_INTERNAL_EXPORTS[name]
        module = importlib.import_module(module_name, __name__)
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # app
    "OpenPactRuntime",
    "bootstrap_database",
    "build_runtime",
    "load_initial_migration_sql",
    "load_schema_sql",
    "open_runtime_connection",
    # commands (operator actions)
    "load_congress",
    "load_congress_local",
    "load_disclosures",
    "load_fec_local",
    "load_member_fec_crosswalk_local",
    "process_disclosures_local",
    "publish_snapshot",
    "recompute_snapshot",
    "run_history_backfill_local_command",
    "run_oracle_local_command",
    "verify_history_aggregate_local",
    "verify_publish_local",
    "verify_publish_roundtrip_local",
    # context
    "RuntimeContext",
    "build_runtime_context",
    "null_contribution_sector_resolver",
    "null_issuer_sector_resolver",
    "open_connection",
    # inspect (published-read helpers)
    "load_local_current_member_lookup",
    "load_local_evidence_card",
    "load_local_history_backfill_report",
    "load_local_history_event",
    "load_local_history_event_page",
    "load_local_homepage_bootstrap",
    "load_local_history_bootstrap",
    "load_local_history_coverage",
    "load_local_history_preset_range",
    "load_local_homepage_feed",
    "load_local_manifest",
    "load_local_member_change_summary",
    "load_local_member_history_coverage",
    "load_local_member_history_coverage_index",
    "load_local_member_history_chart",
    "load_local_member_history",
    "load_local_member_history_page",
    "load_local_member_timeline_index",
    "load_local_member_timeline_page",
    "load_local_member_timeline_dimension",
    "load_local_member_timeline_year",
    "load_local_member_page",
    "load_local_member_preset_compare",
    "load_local_member_profile",
    "load_local_snapshot_preset_compare",
    "load_local_member_trend_summary",
    "load_local_movement_window",
    "load_local_prediction_bootstrap",
    "load_local_prediction_committee_context",
    "load_local_prediction_committee_readiness",
    "load_local_prediction_member_context",
    "load_local_prediction_member_readiness",
    "load_local_prediction_readiness",
    "load_local_prediction_readiness_index",
    "load_local_prediction_sector_context",
    "load_local_prediction_sector_readiness",
    "load_local_prediction_source_context",
    "load_local_prediction_source_index",
    "load_local_prediction_topology",
    "load_local_snapshot_index",
    "load_local_zip_entry",
    "load_local_zip_feed",
    "list_local_snapshot_ids",
    "search_local_current_member_lookup",
    # history backfill planning
    "CongressDateWindow",
    "check_history_backfill_inputs",
    "HistoryBackfillAttempt",
    "HistoryBackfillExecutionResult",
    "HistoricalSnapshotTarget",
    "HistoryBackfillPlan",
    "HistoryBackfillCongressArchiveInputsPayload",
    "HistoryBackfillDisclosuresBundleInputsPayload",
    "HistoryBackfillInputReadinessPayload",
    "LocalHistoryAggregateSummary",
    "LocalHistoryBackfillResult",
    "congress_term_bounds",
    "execute_history_backfill",
    "derive_weekly_snapshot_dates",
    "load_history_backfill_report",
    "plan_congress_history_backfill",
    "resolve_congress_date_window",
    "run_local_history_backfill",
    # paths
    "crosswalks_dir",
    "db_migrations_dir",
    "db_schema_path",
    "local_artifact_root",
    "local_congress_bundle_root",
    "local_disclosure_bundle_root",
    "local_publish_root",
    "repo_root",
    "taxonomy_dir",
    # sources
    "CONFLICT_RECOMPUTE",
    "CONGRESS_CORE",
    "DISCLOSURE_LOAD",
    "HOUSE_DISCLOSURES",
    "SENATE_DISCLOSURES",
    "FEC_BULK",
    "MEMBER_FEC_CROSSWALK",
    "SNAPSHOT_PUBLISH",
    "SourceSpec",
    "all_sources",
    "source_by_slug",
    # result types (returned by operator commands)
    "CongressLoadResult",
    "CongressOracleOptions",
    "DisclosuresLoadRuntimeResult",
    "FecBulkFilePaths",
    "FecLocalLoadResult",
    "MemberFecCrosswalkLoadResult",
    "LocalOracleOptions",
    "LocalOracleRunResult",
    "PublishRuntimeResult",
    "PublishRoundtripResult",
    "PublishVerifyResult",
    "RuntimeRecomputeResult",
    # status
    "get_runtime_status",
    "get_runtime_status_summary",
    # zip bundle
    "load_zip_bundle",
    "zip_bundle_from_dict",
]
