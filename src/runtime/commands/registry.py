"""Operator command registry and dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.runtime.commands.bill_semantics import (
    _handle_materialize_bill_semantics,
    _handle_verify_bill_semantics,
    _handle_verify_bill_semantics_plan,
)
from src.runtime.commands.core import (
    _handle_bootstrap_db,
    _handle_load_congress,
    _handle_load_congress_local,
    _handle_publish,
    _handle_recompute,
    _handle_run_oracle_local,
    _handle_status,
    _handle_verify_publish,
    _handle_verify_publish_roundtrip,
)
from src.runtime.commands.disclosures import (
    _handle_load_disclosures,
    _handle_materialize_disclosures_bundle,
    _handle_parse_disclosures,
    _handle_process_disclosures,
    _handle_process_disclosures_local,
)
from src.runtime.commands.fec import (
    _handle_load_fec_local,
    _handle_load_member_fec_crosswalk_local,
    _handle_materialize_fec_bulk_files,
    _handle_materialize_member_fec_crosswalk,
    _handle_verify_fec_inputs,
)
from src.runtime.commands.history import (
    _handle_aggregate_history,
    _handle_check_history_backfill_inputs,
    _handle_materialize_congress_archive,
    _handle_materialize_history_backfill_inputs,
    _handle_plan_history_backfill,
    _handle_run_history_backfill_local,
    _handle_run_history_launch_local,
    _handle_verify_history_aggregate,
    _handle_write_congress_archive_manifest,
)
from src.runtime.commands.operator_packet import (
    _handle_prediction_operator_packet_export,
    _handle_prediction_operator_packet_manifest,
    _handle_prediction_operator_resume_plan,
    _handle_prediction_operator_status,
    _handle_verify_prediction_operator_handoff,
    _handle_verify_prediction_operator_packet_directory,
    _handle_verify_prediction_operator_packet_export,
    _handle_verify_prediction_operator_packet_manifest,
    _handle_verify_prediction_operator_resume_plan,
    _handle_verify_prediction_operator_resume_run,
    _handle_verify_prediction_operator_runbook,
    _handle_verify_prediction_operator_status,
)
from src.runtime.commands.prediction_benchmark import (
    _handle_verify_prediction_backfill_plan,
    _handle_verify_prediction_benchmark,
)
from src.runtime.commands.prediction_eval import (
    _handle_prediction_eval_report,
    _handle_verify_prediction_eval_manifest,
)
from src.runtime.commands.prediction_eval_windows import (
    _handle_prediction_eval_window_plan,
    _handle_prediction_eval_window_summary,
    _handle_verify_prediction_eval_window_plan,
    _handle_verify_prediction_eval_window_run,
    _handle_verify_prediction_eval_window_summary,
)
from src.runtime.commands.prediction_misc import (
    _handle_prediction_backtest,
    _handle_prediction_input_inventory,
    _handle_prediction_source_url_audit,
    _handle_verify_prediction_backtest,
    _handle_verify_prediction_input_inventory,
    _handle_verify_prediction_source_url_audit,
)
from src.runtime.commands.prediction_readiness import (
    _handle_prediction_offline_readiness_summary,
    _handle_verify_prediction_offline_readiness_summary,
    _handle_verify_prediction_resume_script,
)
from src.runtime.commands.runtime_env import (
    _handle_runtime_env_preflight,
    _handle_verify_runtime_env_preflight,
)
from src.runtime.commands.statements import (
    _handle_materialize_public_statement_rows,
    _handle_materialize_public_statement_rss,
    _handle_verify_public_statement_rows,
)

COMMAND_REGISTRY: dict[str, Callable[[Any], dict[str, Any]]] = {
    "bootstrap-db": _handle_bootstrap_db,
    "runtime-env-preflight": _handle_runtime_env_preflight,
    "verify-runtime-env-preflight": _handle_verify_runtime_env_preflight,
    "status": _handle_status,
    "load-congress": _handle_load_congress,
    "materialize-fec-bulk-files": _handle_materialize_fec_bulk_files,
    "materialize-member-fec-crosswalk": _handle_materialize_member_fec_crosswalk,
    "materialize-public-statement-rss": _handle_materialize_public_statement_rss,
    "materialize-public-statement-rows": _handle_materialize_public_statement_rows,
    "load-fec-local": _handle_load_fec_local,
    "load-member-fec-crosswalk-local": _handle_load_member_fec_crosswalk_local,
    "verify-fec-inputs": _handle_verify_fec_inputs,
    "load-disclosures": _handle_load_disclosures,
    "parse-disclosures": _handle_parse_disclosures,
    "process-disclosures": _handle_process_disclosures,
    "recompute": _handle_recompute,
    "verify-public-statement-rows": _handle_verify_public_statement_rows,
    "publish": _handle_publish,
    "load-congress-local": _handle_load_congress_local,
    "process-disclosures-local": _handle_process_disclosures_local,
    "run-oracle-local": _handle_run_oracle_local,
    "plan-history-backfill": _handle_plan_history_backfill,
    "check-history-backfill-inputs": _handle_check_history_backfill_inputs,
    "write-congress-archive-manifest": _handle_write_congress_archive_manifest,
    "materialize-congress-archive": _handle_materialize_congress_archive,
    "materialize-history-backfill-inputs": _handle_materialize_history_backfill_inputs,
    "materialize-disclosures-bundle": _handle_materialize_disclosures_bundle,
    "materialize-bill-semantics": _handle_materialize_bill_semantics,
    "verify-bill-semantics": _handle_verify_bill_semantics,
    "verify-bill-semantics-plan": _handle_verify_bill_semantics_plan,
    "run-history-launch-local": _handle_run_history_launch_local,
    "run-history-backfill-local": _handle_run_history_backfill_local,
    "aggregate-history": _handle_aggregate_history,
    "prediction-backtest": _handle_prediction_backtest,
    "prediction-input-inventory": _handle_prediction_input_inventory,
    "verify-prediction-input-inventory": _handle_verify_prediction_input_inventory,
    "prediction-eval-report": _handle_prediction_eval_report,
    "prediction-eval-window-plan": _handle_prediction_eval_window_plan,
    "verify-prediction-eval-window-plan": _handle_verify_prediction_eval_window_plan,
    "prediction-eval-window-summary": _handle_prediction_eval_window_summary,
    "verify-prediction-eval-window-summary": _handle_verify_prediction_eval_window_summary,
    "verify-prediction-eval-window-run": _handle_verify_prediction_eval_window_run,
    "prediction-source-url-audit": _handle_prediction_source_url_audit,
    "verify-prediction-source-url-audit": _handle_verify_prediction_source_url_audit,
    "verify-prediction-eval-manifest": _handle_verify_prediction_eval_manifest,
    "verify-prediction-backtest": _handle_verify_prediction_backtest,
    "verify-prediction-benchmark": _handle_verify_prediction_benchmark,
    "verify-prediction-backfill-plan": _handle_verify_prediction_backfill_plan,
    "prediction-offline-readiness-summary": (_handle_prediction_offline_readiness_summary),
    "verify-prediction-offline-readiness-summary": (
        _handle_verify_prediction_offline_readiness_summary
    ),
    "verify-prediction-resume-script": _handle_verify_prediction_resume_script,
    "verify-prediction-operator-handoff": (_handle_verify_prediction_operator_handoff),
    "verify-prediction-operator-runbook": (_handle_verify_prediction_operator_runbook),
    "prediction-operator-status": _handle_prediction_operator_status,
    "verify-prediction-operator-status": _handle_verify_prediction_operator_status,
    "prediction-operator-packet-manifest": (_handle_prediction_operator_packet_manifest),
    "verify-prediction-operator-packet-manifest": (
        _handle_verify_prediction_operator_packet_manifest
    ),
    "prediction-operator-packet-export": _handle_prediction_operator_packet_export,
    "verify-prediction-operator-packet-export": (_handle_verify_prediction_operator_packet_export),
    "verify-prediction-operator-packet-directory": (
        _handle_verify_prediction_operator_packet_directory
    ),
    "prediction-operator-resume-plan": _handle_prediction_operator_resume_plan,
    "verify-prediction-operator-resume-plan": (_handle_verify_prediction_operator_resume_plan),
    "verify-prediction-operator-resume-run": _handle_verify_prediction_operator_resume_run,
    "verify-publish": _handle_verify_publish,
    "verify-publish-roundtrip": _handle_verify_publish_roundtrip,
    "verify-history-aggregate": _handle_verify_history_aggregate,
}


def dispatch_command(args: Any) -> dict[str, Any]:
    handler = COMMAND_REGISTRY.get(args.command)
    if handler is None:
        return {"ok": False, "error": f"unknown command: {args.command!r}"}
    return handler(args)
