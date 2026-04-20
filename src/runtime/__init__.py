# App surface
from .app import OpenPactRuntime, build_runtime, open_runtime_connection
from .bootstrap import bootstrap_database, load_initial_migration_sql, load_schema_sql
from .commands import (
    load_congress,
    load_congress_local,
    load_disclosures,
    process_disclosures_local,
    publish_snapshot,
    recompute_snapshot,
    run_history_backfill_local_command,
    run_oracle_local_command,
    verify_history_aggregate_local,
    verify_publish_local,
    verify_publish_roundtrip_local,
)

# Context surface
from .context import (
    RuntimeContext,
    build_runtime_context,
    null_issuer_sector_resolver,
    open_connection,
)
from .inspect import (
    load_local_current_member_lookup,
    load_local_evidence_card,
    load_local_history_bootstrap,
    load_local_history_preset_range,
    load_local_homepage_bootstrap,
    load_local_homepage_feed,
    load_local_manifest,
    load_local_member_change_summary,
    load_local_member_history_chart,
    load_local_member_history,
    load_local_member_history_page,
    load_local_member_page,
    load_local_member_preset_compare,
    load_local_member_profile,
    load_local_snapshot_preset_compare,
    load_local_member_trend_summary,
    load_local_movement_window,
    load_local_snapshot_index,
    load_local_zip_entry,
    load_local_zip_feed,
    list_local_snapshot_ids,
    search_local_current_member_lookup,
)
from .history_backfill import (
    CongressDateWindow,
    HistoryBackfillAttempt,
    HistoryBackfillExecutionResult,
    HistoricalSnapshotTarget,
    HistoryBackfillPlan,
    LocalHistoryAggregateSummary,
    LocalHistoryBackfillResult,
    congress_term_bounds,
    execute_history_backfill,
    derive_weekly_snapshot_dates,
    plan_congress_history_backfill,
    resolve_congress_date_window,
    run_local_history_backfill,
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
    HOUSE_DISCLOSURES,
    SENATE_DISCLOSURES,
    SNAPSHOT_PUBLISH,
    SourceSpec,
    all_sources,
    source_by_slug,
)

# Result types (returned by operator commands)
from .congress import CongressLoadResult
from .disclosures import DisclosuresLoadRuntimeResult
from .oracle_contracts import (
    CongressOracleOptions,
    LocalOracleOptions,
    LocalOracleRunResult,
)
from .publish import PublishRuntimeResult
from .publish_roundtrip_types import PublishRoundtripResult
from .publish_verify_types import PublishVerifyResult
from .recompute import RuntimeRecomputeResult

# Status surface
from .status_command import get_runtime_status, get_runtime_status_summary

# Zip bundle surface
from .zip_bundle import load_zip_bundle, zip_bundle_from_dict

# ---------------------------------------------------------------------------
# Internal re-exports: not part of __all__, but importable via src.runtime.X
# for internal callers (tests, smoke helpers, orchestration modules).
# ---------------------------------------------------------------------------
from .congress import run_congress_load_runtime as run_congress_load_runtime  # noqa: F401
from .congress_archive import run_congress_archive_load as run_congress_archive_load  # noqa: F401
from .disclosures import run_disclosures_load_runtime as run_disclosures_load_runtime  # noqa: F401
from .disclosures_artifacts import DisclosureArtifactIngestResult as DisclosureArtifactIngestResult  # noqa: F401
from .disclosures_artifacts import run_disclosure_artifact_ingest as run_disclosure_artifact_ingest  # noqa: F401
from .disclosures_load_from_parse import DisclosuresParseLoadResult as DisclosuresParseLoadResult  # noqa: F401
from .disclosures_load_from_parse import run_disclosures_parse_load_runtime as run_disclosures_parse_load_runtime  # noqa: F401
from .disclosures_parse import DisclosureParseRuntimeResult as DisclosureParseRuntimeResult  # noqa: F401
from .disclosures_parse import run_disclosure_parse_runtime as run_disclosure_parse_runtime  # noqa: F401
from .oracle_contracts import CongressStageSummary as CongressStageSummary  # noqa: F401
from .oracle_contracts import LocalOracleInputs as LocalOracleInputs  # noqa: F401
from .oracle_local import run_oracle_local as run_oracle_local  # noqa: F401
from .parse_runs import ParseSessionResult as ParseSessionResult  # noqa: F401
from .parse_runs import run_parse_session as run_parse_session  # noqa: F401
from .publish import default_snapshot_id as default_snapshot_id  # noqa: F401
from .publish import run_publish_runtime as run_publish_runtime  # noqa: F401
from .publish_roundtrip_types import ROUNDTRIP_STAGES as ROUNDTRIP_STAGES  # noqa: F401
from .publish_roundtrip_types import PublishRoundtripIssue as PublishRoundtripIssue  # noqa: F401
from .publish_roundtrip_types import PublishRoundtripStageResult as PublishRoundtripStageResult  # noqa: F401
from .publish_verify_types import PUBLISH_STAGES as PUBLISH_STAGES  # noqa: F401
from .publish_verify_types import PublishVerifyIssue as PublishVerifyIssue  # noqa: F401
from .publish_verify_types import PublishVerifyStageResult as PublishVerifyStageResult  # noqa: F401
from .recompute import run_recompute_runtime as run_recompute_runtime  # noqa: F401
from .smoke import smoke_publish as smoke_publish  # noqa: F401
from .smoke import smoke_recompute as smoke_recompute  # noqa: F401
from .smoke_oracle import smoke_oracle_path as smoke_oracle_path  # noqa: F401
from .smoke_process_disclosures import smoke_process_disclosures as smoke_process_disclosures  # noqa: F401
from .status import current_data_sources as current_data_sources  # noqa: F401
from .status import latest_ingestion_runs as latest_ingestion_runs  # noqa: F401
from .status import latest_parse_runs as latest_parse_runs  # noqa: F401
from .status import latest_source_artifacts as latest_source_artifacts  # noqa: F401
from .status import runtime_status_dict as runtime_status_dict  # noqa: F401

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
    "null_issuer_sector_resolver",
    "open_connection",
    # inspect (published-read helpers)
    "load_local_current_member_lookup",
    "load_local_evidence_card",
    "load_local_homepage_bootstrap",
    "load_local_history_bootstrap",
    "load_local_history_preset_range",
    "load_local_homepage_feed",
    "load_local_manifest",
    "load_local_member_change_summary",
    "load_local_member_history_chart",
    "load_local_member_history",
    "load_local_member_history_page",
    "load_local_member_page",
    "load_local_member_preset_compare",
    "load_local_member_profile",
    "load_local_snapshot_preset_compare",
    "load_local_member_trend_summary",
    "load_local_movement_window",
    "load_local_snapshot_index",
    "load_local_zip_entry",
    "load_local_zip_feed",
    "list_local_snapshot_ids",
    "search_local_current_member_lookup",
    # history backfill planning
    "CongressDateWindow",
    "HistoryBackfillAttempt",
    "HistoryBackfillExecutionResult",
    "HistoricalSnapshotTarget",
    "HistoryBackfillPlan",
    "LocalHistoryAggregateSummary",
    "LocalHistoryBackfillResult",
    "congress_term_bounds",
    "execute_history_backfill",
    "derive_weekly_snapshot_dates",
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
    "SNAPSHOT_PUBLISH",
    "SourceSpec",
    "all_sources",
    "source_by_slug",
    # result types (returned by operator commands)
    "CongressLoadResult",
    "CongressOracleOptions",
    "DisclosuresLoadRuntimeResult",
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
