"""Top-level parser assembly: build_parser() and parse_args()."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from src.runtime.cli.bill_semantics import (
    _add_materialize_bill_semantics,
    _add_verify_bill_semantics,
    _add_verify_bill_semantics_plan,
)
from src.runtime.cli.core import (
    _add_bootstrap_db,
    _add_load_congress,
    _add_load_congress_local,
    _add_publish,
    _add_recompute,
    _add_run_oracle_local,
    _add_status,
    _add_verify_publish,
    _add_verify_publish_roundtrip,
)
from src.runtime.cli.disclosures import (
    _add_load_disclosures,
    _add_materialize_disclosures_bundle,
    _add_parse_disclosures,
    _add_process_disclosures,
    _add_process_disclosures_local,
)
from src.runtime.cli.fec import (
    _add_load_fec_local,
    _add_load_member_fec_crosswalk_local,
    _add_materialize_fec_bulk_files,
    _add_materialize_member_fec_crosswalk,
    _add_verify_fec_inputs,
)
from src.runtime.cli.history import (
    _add_aggregate_history,
    _add_check_history_backfill_inputs,
    _add_materialize_congress_archive,
    _add_materialize_history_backfill_inputs,
    _add_plan_history_backfill,
    _add_run_history_backfill_local,
    _add_run_history_launch_local,
    _add_verify_history_aggregate,
    _add_write_congress_archive_manifest,
)
from src.runtime.cli.operator import (
    _add_prediction_operator_packet_export,
    _add_prediction_operator_packet_manifest,
    _add_prediction_operator_resume_plan,
    _add_prediction_operator_status,
    _add_verify_prediction_operator_handoff,
    _add_verify_prediction_operator_packet_directory,
    _add_verify_prediction_operator_packet_export,
    _add_verify_prediction_operator_packet_manifest,
    _add_verify_prediction_operator_resume_plan,
    _add_verify_prediction_operator_resume_run,
    _add_verify_prediction_operator_runbook,
    _add_verify_prediction_operator_status,
)
from src.runtime.cli.prediction import (
    _add_prediction_backtest,
    _add_prediction_input_inventory,
    _add_prediction_offline_readiness_summary,
    _add_prediction_source_url_audit,
    _add_verify_prediction_backtest,
    _add_verify_prediction_input_inventory,
    _add_verify_prediction_offline_readiness_summary,
    _add_verify_prediction_resume_script,
    _add_verify_prediction_source_url_audit,
)
from src.runtime.cli.prediction_eval import (
    _add_prediction_eval_report,
    _add_prediction_eval_window_plan,
    _add_prediction_eval_window_summary,
    _add_verify_prediction_backfill_plan,
    _add_verify_prediction_benchmark,
    _add_verify_prediction_eval_manifest,
    _add_verify_prediction_eval_window_plan,
    _add_verify_prediction_eval_window_run,
    _add_verify_prediction_eval_window_summary,
)
from src.runtime.cli.runtime_env import (
    _add_runtime_env_preflight,
    _add_verify_runtime_env_preflight,
)
from src.runtime.cli.statements import (
    _add_materialize_public_statement_rows,
    _add_materialize_public_statement_rss,
    _add_verify_public_statement_rows,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psephosamerica",
        description="Psephos America operator runtime.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    _add_bootstrap_db(sub)
    _add_runtime_env_preflight(sub)
    _add_verify_runtime_env_preflight(sub)
    _add_status(sub)
    _add_load_congress(sub)
    _add_materialize_fec_bulk_files(sub)
    _add_materialize_member_fec_crosswalk(sub)
    _add_materialize_public_statement_rss(sub)
    _add_materialize_public_statement_rows(sub)
    _add_load_fec_local(sub)
    _add_load_member_fec_crosswalk_local(sub)
    _add_verify_fec_inputs(sub)
    _add_load_disclosures(sub)
    _add_parse_disclosures(sub)
    _add_process_disclosures(sub)
    _add_recompute(sub)
    _add_verify_public_statement_rows(sub)
    _add_publish(sub)
    _add_load_congress_local(sub)
    _add_process_disclosures_local(sub)
    _add_run_oracle_local(sub)
    _add_plan_history_backfill(sub)
    _add_check_history_backfill_inputs(sub)
    _add_write_congress_archive_manifest(sub)
    _add_materialize_congress_archive(sub)
    _add_materialize_history_backfill_inputs(sub)
    _add_materialize_disclosures_bundle(sub)
    _add_materialize_bill_semantics(sub)
    _add_verify_bill_semantics(sub)
    _add_verify_bill_semantics_plan(sub)
    _add_run_history_launch_local(sub)
    _add_run_history_backfill_local(sub)
    _add_aggregate_history(sub)
    _add_prediction_backtest(sub)
    _add_prediction_input_inventory(sub)
    _add_verify_prediction_input_inventory(sub)
    _add_prediction_eval_report(sub)
    _add_prediction_eval_window_plan(sub)
    _add_verify_prediction_eval_window_plan(sub)
    _add_prediction_eval_window_summary(sub)
    _add_verify_prediction_eval_window_summary(sub)
    _add_verify_prediction_eval_window_run(sub)
    _add_prediction_source_url_audit(sub)
    _add_verify_prediction_source_url_audit(sub)
    _add_verify_prediction_eval_manifest(sub)
    _add_verify_prediction_backtest(sub)
    _add_verify_prediction_benchmark(sub)
    _add_verify_prediction_backfill_plan(sub)
    _add_prediction_offline_readiness_summary(sub)
    _add_verify_prediction_offline_readiness_summary(sub)
    _add_verify_prediction_resume_script(sub)
    _add_verify_prediction_operator_handoff(sub)
    _add_verify_prediction_operator_runbook(sub)
    _add_prediction_operator_status(sub)
    _add_verify_prediction_operator_status(sub)
    _add_prediction_operator_packet_manifest(sub)
    _add_verify_prediction_operator_packet_manifest(sub)
    _add_prediction_operator_packet_export(sub)
    _add_verify_prediction_operator_packet_export(sub)
    _add_verify_prediction_operator_packet_directory(sub)
    _add_prediction_operator_resume_plan(sub)
    _add_verify_prediction_operator_resume_plan(sub)
    _add_verify_prediction_operator_resume_run(sub)
    _add_verify_publish(sub)
    _add_verify_publish_roundtrip(sub)
    _add_verify_history_aggregate(sub)

    return parser


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(list(argv))
    if (
        args.command in {"materialize-history-backfill-inputs", "run-history-launch-local"}
        and args.disclosures_year is None
        and not args.reuse_existing_inputs
    ):
        parser.error(
            "--disclosures-year is required unless --reuse-existing-inputs is set "
            "to infer years from an existing bundle"
        )
    if args.command == "load-congress" and (
        args.house_vote_year is not None or args.senate_session is not None
    ):
        args.include_votes = True
    if args.command == "materialize-congress-archive" and (
        args.house_vote_year is not None or args.senate_session is not None
    ):
        args.include_votes = True
    if args.command == "materialize-history-backfill-inputs" and (
        args.house_vote_year is not None or args.senate_session is not None
    ):
        args.include_votes = True
    if args.command == "run-history-launch-local" and (
        args.house_vote_year is not None or args.senate_session is not None
    ):
        args.include_votes = True
    return args
