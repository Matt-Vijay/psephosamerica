"""CLI argument parser for the Open Pact operator runtime.

Pure parser layer: no DB access, no network calls, no command execution.
Entry points: build_parser(), parse_args(argv).

Split from the original monolithic ``cli.py``; this package re-exports
every top-level symbol so all historical import paths keep working.
"""

from __future__ import annotations

from src.runtime.cli._shared import (
    _parse_date as _parse_date,
)
from src.runtime.cli.core import (
    _add_bootstrap_db as _add_bootstrap_db,
    _add_load_congress as _add_load_congress,
    _add_load_congress_local as _add_load_congress_local,
    _add_publish as _add_publish,
    _add_recompute as _add_recompute,
    _add_run_oracle_local as _add_run_oracle_local,
    _add_status as _add_status,
    _add_verify_publish as _add_verify_publish,
    _add_verify_publish_roundtrip as _add_verify_publish_roundtrip,
)
from src.runtime.cli.statements import (
    _add_materialize_public_statement_rows as _add_materialize_public_statement_rows,
    _add_materialize_public_statement_rss as _add_materialize_public_statement_rss,
    _add_verify_public_statement_rows as _add_verify_public_statement_rows,
)
from src.runtime.cli.fec import (
    _add_load_fec_local as _add_load_fec_local,
    _add_load_member_fec_crosswalk_local as _add_load_member_fec_crosswalk_local,
    _add_materialize_fec_bulk_files as _add_materialize_fec_bulk_files,
    _add_materialize_member_fec_crosswalk as _add_materialize_member_fec_crosswalk,
    _add_verify_fec_inputs as _add_verify_fec_inputs,
)
from src.runtime.cli.disclosures import (
    _add_load_disclosures as _add_load_disclosures,
    _add_materialize_disclosures_bundle as _add_materialize_disclosures_bundle,
    _add_parse_disclosures as _add_parse_disclosures,
    _add_process_disclosures as _add_process_disclosures,
    _add_process_disclosures_local as _add_process_disclosures_local,
)
from src.runtime.cli.history import (
    _add_aggregate_history as _add_aggregate_history,
    _add_check_history_backfill_inputs as _add_check_history_backfill_inputs,
    _add_materialize_congress_archive as _add_materialize_congress_archive,
    _add_materialize_history_backfill_inputs as _add_materialize_history_backfill_inputs,
    _add_plan_history_backfill as _add_plan_history_backfill,
    _add_run_history_backfill_local as _add_run_history_backfill_local,
    _add_run_history_launch_local as _add_run_history_launch_local,
    _add_verify_history_aggregate as _add_verify_history_aggregate,
    _add_write_congress_archive_manifest as _add_write_congress_archive_manifest,
)
from src.runtime.cli.runtime_env import (
    _add_runtime_env_preflight as _add_runtime_env_preflight,
    _add_verify_runtime_env_preflight as _add_verify_runtime_env_preflight,
)
from src.runtime.cli.bill_semantics import (
    _add_materialize_bill_semantics as _add_materialize_bill_semantics,
    _add_verify_bill_semantics as _add_verify_bill_semantics,
    _add_verify_bill_semantics_plan as _add_verify_bill_semantics_plan,
)
from src.runtime.cli.prediction import (
    _add_prediction_backtest as _add_prediction_backtest,
    _add_prediction_input_inventory as _add_prediction_input_inventory,
    _add_prediction_offline_readiness_summary as _add_prediction_offline_readiness_summary,
    _add_prediction_source_url_audit as _add_prediction_source_url_audit,
    _add_verify_prediction_backtest as _add_verify_prediction_backtest,
    _add_verify_prediction_input_inventory as _add_verify_prediction_input_inventory,
    _add_verify_prediction_offline_readiness_summary as _add_verify_prediction_offline_readiness_summary,
    _add_verify_prediction_resume_script as _add_verify_prediction_resume_script,
    _add_verify_prediction_source_url_audit as _add_verify_prediction_source_url_audit,
)
from src.runtime.cli.prediction_eval import (
    _add_prediction_eval_manifest_coverage_threshold_args as _add_prediction_eval_manifest_coverage_threshold_args,
    _add_prediction_eval_report as _add_prediction_eval_report,
    _add_prediction_eval_window_plan as _add_prediction_eval_window_plan,
    _add_prediction_eval_window_summary as _add_prediction_eval_window_summary,
    _add_verify_prediction_backfill_plan as _add_verify_prediction_backfill_plan,
    _add_verify_prediction_benchmark as _add_verify_prediction_benchmark,
    _add_verify_prediction_eval_manifest as _add_verify_prediction_eval_manifest,
    _add_verify_prediction_eval_window_plan as _add_verify_prediction_eval_window_plan,
    _add_verify_prediction_eval_window_run as _add_verify_prediction_eval_window_run,
    _add_verify_prediction_eval_window_summary as _add_verify_prediction_eval_window_summary,
)
from src.runtime.cli.operator import (
    _add_prediction_operator_packet_export as _add_prediction_operator_packet_export,
    _add_prediction_operator_packet_manifest as _add_prediction_operator_packet_manifest,
    _add_prediction_operator_resume_plan as _add_prediction_operator_resume_plan,
    _add_prediction_operator_status as _add_prediction_operator_status,
    _add_verify_prediction_operator_handoff as _add_verify_prediction_operator_handoff,
    _add_verify_prediction_operator_packet_directory as _add_verify_prediction_operator_packet_directory,
    _add_verify_prediction_operator_packet_export as _add_verify_prediction_operator_packet_export,
    _add_verify_prediction_operator_packet_manifest as _add_verify_prediction_operator_packet_manifest,
    _add_verify_prediction_operator_resume_plan as _add_verify_prediction_operator_resume_plan,
    _add_verify_prediction_operator_resume_run as _add_verify_prediction_operator_resume_run,
    _add_verify_prediction_operator_runbook as _add_verify_prediction_operator_runbook,
    _add_verify_prediction_operator_status as _add_verify_prediction_operator_status,
)
from src.runtime.cli.parser import (
    build_parser as build_parser,
    parse_args as parse_args,
)
