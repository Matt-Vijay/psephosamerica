"""CLI argument parser for the Open Pact operator runtime.

Pure parser layer: no DB access, no network calls, no command execution.
Entry points: build_parser(), parse_args(argv).
"""

from __future__ import annotations

import argparse
import datetime
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openpact",
        description="Open Pact operator runtime.",
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


# ---------------------------------------------------------------------------
# Subcommand definitions
# ---------------------------------------------------------------------------


def _add_bootstrap_db(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    description = "Apply the canonical bootstrap schema from db/schema.sql to the target database."
    p = sub.add_parser(
        "bootstrap-db",
        help=description,
        description=description,
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show the bootstrap plan without applying schema SQL.",
    )


def _add_runtime_env_preflight(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "runtime-env-preflight",
        help="Check required runtime environment variables without printing secret values.",
    )
    p.add_argument(
        "--require-env",
        action="append",
        default=None,
        metavar="NAME",
        help="Environment variable that must be non-empty in this process; repeatable.",
    )
    p.add_argument(
        "--dotenv",
        default=None,
        metavar="PATH",
        help="Optional .env-style file to inspect for presence only; values are never printed.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the sanitized env preflight artifact.",
    )
    p.add_argument(
        "--template-output",
        default=None,
        metavar="PATH",
        help="Optional .env template path with required keys and empty values only.",
    )


def _add_verify_runtime_env_preflight(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-runtime-env-preflight",
        help="Verify a sanitized runtime-env-preflight artifact.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a runtime-env-preflight JSON artifact.",
    )
    p.add_argument(
        "--require-next-actions",
        action="store_true",
        help="Return non-green when missing env vars have no next actions.",
    )
    p.add_argument(
        "--require-template-output",
        action="store_true",
        help="Return non-green when a dotenv/template handoff is missing or stale.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the artifact appears to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the env-preflight verification payload.",
    )


def _add_status(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "status",
        help="Show summary counts plus latest ingestion, parse, artifact, and active source state.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="Number of recent runs to display (default: 20).",
    )


def _add_load_congress(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "load-congress",
        help="Fetch and load member, committee, bill, and vote data from Congress.gov.",
    )
    p.add_argument(
        "--congress",
        type=int,
        default=None,
        metavar="NUMBER",
        help="Congress number to load (e.g. 118). Defaults to current Congress.",
    )
    p.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help="Congress.gov API key. Falls back to OPENPACT_CONGRESS_API_KEY env var.",
    )
    p.add_argument(
        "--include-votes",
        action="store_true",
        default=False,
        help="Also fetch and load vote records for the requested Congress.",
    )
    p.add_argument(
        "--house-vote-year",
        type=int,
        default=None,
        metavar="YEAR",
        help="Calendar year to fetch House roll-call votes for (e.g. 2024).",
    )
    p.add_argument(
        "--senate-session",
        type=int,
        default=None,
        metavar="SESSION",
        help="Senate session number to fetch votes for (e.g. 1 or 2).",
    )


def _add_load_fec_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "load-fec-local",
        help="Load local FEC bulk committee, candidate-linkage, and contribution files.",
    )
    p.add_argument(
        "--committee-master",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC Committee Master file, usually cm.txt.",
    )
    p.add_argument(
        "--candidate-committee-linkage",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC Candidate-Committee Linkage file, usually ccl.txt.",
    )
    p.add_argument(
        "--individual-contributions",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC individual contributions file, usually itcont.txt.",
    )
    p.add_argument(
        "--committee-source-url",
        default=None,
        metavar="URL",
        help="Optional official FEC source URL for the committee-master artifact.",
    )
    p.add_argument(
        "--linkage-source-url",
        default=None,
        metavar="URL",
        help="Optional official FEC source URL for the candidate-linkage artifact.",
    )
    p.add_argument(
        "--contribution-source-url",
        default=None,
        metavar="URL",
        help="Optional official FEC source URL for the contribution artifact.",
    )
    p.add_argument(
        "--contribution-chunk-size",
        type=int,
        default=50_000,
        metavar="N",
        help="Number of parsed contribution rows to load per DB batch.",
    )


def _add_materialize_fec_bulk_files(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "materialize-fec-bulk-files",
        help="Download/extract official FEC bulk ZIPs into local load-fec inputs.",
    )
    p.add_argument(
        "--cycle",
        type=int,
        required=True,
        metavar="YEAR",
        help="FEC election cycle year, e.g. 2024.",
    )
    p.add_argument(
        "--output-dir",
        default="data/fec",
        metavar="DIR",
        help="Directory for cm.txt, ccl.txt, and itcont.txt. Defaults to data/fec.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Plan official URLs and output paths without downloading.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite existing output files.",
    )
    p.add_argument(
        "--no-filter-individual-contributions",
        dest="filter_individual_contributions",
        action="store_false",
        default=True,
        help=(
            "Extract the full individual-contribution file instead of filtering "
            "to candidate committees present in ccl.txt."
        ),
    )
    p.add_argument(
        "--member-fec-crosswalk",
        default="data/crosswalks/member_fec.csv",
        metavar="PATH",
        help=(
            "Optional member_fec.csv used to narrow contribution filtering to "
            "committees linked to known congressional members."
        ),
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="Network timeout per FEC ZIP download.",
    )
    p.add_argument(
        "--committee-url",
        default=None,
        metavar="URL",
        help="Override official Committee Master ZIP URL.",
    )
    p.add_argument(
        "--linkage-url",
        default=None,
        metavar="URL",
        help="Override official Candidate-Committee Linkage ZIP URL.",
    )
    p.add_argument(
        "--contribution-url",
        default=None,
        metavar="URL",
        help="Override official Individual Contributions ZIP URL.",
    )
    p.add_argument(
        "--summary-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the materialization summary.",
    )


def _add_load_member_fec_crosswalk_local(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "load-member-fec-crosswalk-local",
        help="Load a local bioguide_id to FEC candidate ID crosswalk CSV.",
    )
    p.add_argument(
        "--crosswalk",
        required=True,
        metavar="PATH",
        help="CSV path with bioguide_id and fec_candidate_id columns.",
    )
    p.add_argument(
        "--member-terms",
        default=None,
        metavar="PATH",
        help="Optional companion CSV with historical member terms keyed by bioguide_id.",
    )
    p.add_argument(
        "--source-url",
        default=None,
        metavar="URL",
        help="Optional source URL for the crosswalk artifact.",
    )


def _add_verify_fec_inputs(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-fec-inputs",
        help="Verify local FEC bulk files and optional member-FEC crosswalk before load.",
    )
    p.add_argument(
        "--committee-master",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC Committee Master file, usually cm.txt.",
    )
    p.add_argument(
        "--candidate-committee-linkage",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC Candidate-Committee Linkage file, usually ccl.txt.",
    )
    p.add_argument(
        "--individual-contributions",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC individual contributions file, usually itcont.txt.",
    )
    p.add_argument(
        "--member-fec-crosswalk",
        default=None,
        metavar="PATH",
        help="Optional bioguide_id,fec_candidate_id crosswalk CSV to verify too.",
    )
    p.add_argument(
        "--require-member-fec-crosswalk",
        action="store_true",
        default=False,
        help="Fail unless --member-fec-crosswalk exists and has valid rows.",
    )
    p.add_argument(
        "--min-committee-rows",
        type=int,
        default=None,
        metavar="N",
        help="Minimum non-empty Committee Master rows required.",
    )
    p.add_argument(
        "--min-linkage-rows",
        type=int,
        default=None,
        metavar="N",
        help="Minimum non-empty Candidate-Committee Linkage rows required.",
    )
    p.add_argument(
        "--min-contribution-rows",
        type=int,
        default=None,
        metavar="N",
        help="Minimum non-empty individual-contribution rows required.",
    )
    p.add_argument(
        "--min-member-fec-rows",
        type=int,
        default=None,
        metavar="N",
        help="Minimum member-FEC crosswalk rows required.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the FEC input verification payload.",
    )


def _add_materialize_member_fec_crosswalk(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "materialize-member-fec-crosswalk",
        help="Build data/crosswalks/member_fec.csv from public legislator IDs.",
    )
    p.add_argument(
        "--output",
        default="data/crosswalks/member_fec.csv",
        metavar="PATH",
        help="Output CSV path. Defaults to data/crosswalks/member_fec.csv.",
    )
    p.add_argument(
        "--terms-output",
        default=None,
        metavar="PATH",
        help="Optional companion member terms CSV path materialized from the same source.",
    )
    p.add_argument(
        "--source-file",
        default=None,
        metavar="PATH",
        help="Optional local legislators-current.yaml source file.",
    )
    p.add_argument(
        "--source-url",
        default=(
            "https://raw.githubusercontent.com/unitedstates/congress-legislators/"
            "main/legislators-current.yaml"
        ),
        metavar="URL",
        help="HTTPS source URL used when --source-file is absent and recorded in summary.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report source/output paths without downloading or writing.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite an existing output CSV.",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="Network timeout when downloading the source YAML.",
    )
    p.add_argument(
        "--summary-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the materialization summary.",
    )


def _add_materialize_public_statement_rows(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "materialize-public-statement-rows",
        help="Convert official raw member statements into recompute-ready rows.",
    )
    p.add_argument(
        "--input",
        required=True,
        metavar="PATH",
        help="Raw .json, .jsonl, or .csv official public-statement records.",
    )
    p.add_argument(
        "--output",
        default="data/prepared/public-statement-sector-rows.jsonl",
        metavar="PATH",
        help="Prepared JSONL output path for recompute --statement-rows.",
    )
    p.add_argument(
        "--taxonomy",
        default="data/taxonomy/sectors.yaml",
        metavar="PATH",
        help="Sector taxonomy used for alias normalization and keyword matching.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate and summarize rows without writing output.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite an existing output JSONL.",
    )
    p.add_argument(
        "--summary-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the materialization summary.",
    )


def _add_materialize_public_statement_rss(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "materialize-public-statement-rss",
        help="Fetch raw official member statement records from legislator RSS feeds.",
    )
    p.add_argument(
        "--output",
        default="data/raw/public-statements.jsonl",
        metavar="PATH",
        help="Raw statement JSONL output path.",
    )
    p.add_argument(
        "--source-file",
        default=None,
        metavar="PATH",
        help="Optional local legislators-current.yaml source file.",
    )
    p.add_argument(
        "--source-url",
        default=(
            "https://raw.githubusercontent.com/unitedstates/congress-legislators/"
            "main/legislators-current.yaml"
        ),
        metavar="URL",
        help="HTTPS legislators YAML source URL used when --source-file is absent.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Count available official RSS feeds without fetching feed items.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite an existing raw statement JSONL.",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Network timeout per source/feed request.",
    )
    p.add_argument(
        "--max-feeds",
        type=int,
        default=None,
        metavar="N",
        help="Optional cap on member RSS feeds fetched.",
    )
    p.add_argument(
        "--max-items-per-feed",
        type=int,
        default=None,
        metavar="N",
        help="Optional cap on RSS items per member feed.",
    )
    p.add_argument(
        "--summary-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the materialization summary.",
    )


def _add_load_disclosures(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "load-disclosures",
        help="Discover and store raw House and Senate disclosure artifacts.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber to load disclosures for (default: both).",
    )
    p.add_argument(
        "--year",
        type=int,
        default=None,
        metavar="YEAR",
        help="Filing year to load (e.g. 2024). Defaults to the current year.",
    )
    p.add_argument(
        "--local-root",
        default=None,
        metavar="PATH",
        help="Directory to mirror downloaded raw artifacts into. Defaults to the local artifact root.",
    )


def _add_parse_disclosures(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "parse-disclosures",
        help="Run the text-extract parse pipeline over stored, unparsed disclosure artifacts.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber to parse (default: both).",
    )
    p.add_argument(
        "--local-root",
        default=None,
        metavar="PATH",
        help="Directory containing downloaded artifacts. Defaults to local artifact root.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of artifacts to parse in this run (default: no limit).",
    )


def _add_process_disclosures(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "process-disclosures",
        help="Run the parse-transform-load pipeline over stored disclosure artifacts.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber to process (default: both).",
    )
    p.add_argument(
        "--local-root",
        default=None,
        metavar="PATH",
        help="Directory containing downloaded artifacts. Defaults to local artifact root.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of artifacts to process in this run (default: no limit).",
    )


def _add_recompute(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "recompute",
        help="Run a full conflict-of-interest recompute pass and write score snapshots.",
    )
    p.add_argument(
        "--snapshot-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Date key for the snapshot (default: today).",
    )
    p.add_argument(
        "--statement-rows",
        default=None,
        metavar="PATH",
        help=(
            "Optional .json, .jsonl, or .csv prepared public-statement rows "
            "for member-sector ontology edges."
        ),
    )


def _add_verify_public_statement_rows(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "verify-public-statement-rows",
        help="Verify prepared source-backed public-statement rows for recompute.",
    )
    p.add_argument(
        "--statement-rows",
        required=True,
        metavar="PATH",
        help="Prepared .json, .jsonl, or .csv public-statement row artifact.",
    )
    p.add_argument(
        "--min-rows",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green unless at least N rows are present.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the verification audit payload.",
    )


def _add_prediction_backtest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-backtest",
        help="Backtest vote predictions using only pre-cutoff features against future votes.",
    )
    p.add_argument(
        "--model",
        choices=["baseline", "ontology"],
        default="baseline",
        help="Prediction scorer to evaluate (default: baseline).",
    )
    p.add_argument(
        "--feature-cutoff",
        type=_parse_date,
        default=datetime.date(2024, 12, 31),
        metavar="YYYY-MM-DD",
        help="Latest vote date allowed in features (default: 2024-12-31).",
    )
    p.add_argument(
        "--label-start",
        type=_parse_date,
        default=datetime.date(2025, 1, 1),
        metavar="YYYY-MM-DD",
        help="First vote date to evaluate (default: 2025-01-01).",
    )
    p.add_argument(
        "--label-end",
        type=_parse_date,
        default=datetime.date(2026, 12, 31),
        metavar="YYYY-MM-DD",
        help="Last vote date to evaluate (default: 2026-12-31).",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the full per-vote backtest artifact.",
    )
    p.add_argument(
        "--bill-semantics-root",
        default=None,
        metavar="PATH",
        help="Optional materialized bill-semantics root for ontology model features.",
    )
    p.add_argument(
        "--congress-archive-manifest",
        default=None,
        metavar="PATH",
        help="Optional Congress archive manifest to record in backtest run provenance.",
    )
    p.add_argument(
        "--fail-on-mixed-bill-semantics-models",
        action="store_true",
        default=False,
        help="Return non-green when a bill-semantics cache contains multiple model names.",
    )
    p.add_argument(
        "--fail-on-missing-bill-semantics-root",
        action="store_true",
        default=False,
        help="Return non-green when ontology backtest runs without --bill-semantics-root.",
    )


def _add_prediction_input_inventory(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-input-inventory",
        help="Report cutoff-window prediction input counts and source coverage without scoring.",
    )
    p.add_argument(
        "--training-feature-cutoff",
        type=_parse_date,
        default=datetime.date(2022, 12, 31),
        metavar="YYYY-MM-DD",
        help="Latest vote date allowed in learned-model training features.",
    )
    p.add_argument(
        "--train-start",
        type=_parse_date,
        default=datetime.date(2023, 1, 1),
        metavar="YYYY-MM-DD",
        help="First historical vote date used as learned-model labels.",
    )
    p.add_argument(
        "--train-end",
        type=_parse_date,
        default=datetime.date(2024, 12, 31),
        metavar="YYYY-MM-DD",
        help="Last historical vote date used as learned-model labels.",
    )
    p.add_argument(
        "--feature-cutoff",
        type=_parse_date,
        default=datetime.date(2024, 12, 31),
        metavar="YYYY-MM-DD",
        help="Latest vote date allowed in evaluation features.",
    )
    p.add_argument(
        "--label-start",
        type=_parse_date,
        default=datetime.date(2025, 1, 1),
        metavar="YYYY-MM-DD",
        help="First future vote date to evaluate.",
    )
    p.add_argument(
        "--label-end",
        type=_parse_date,
        default=datetime.date(2026, 12, 31),
        metavar="YYYY-MM-DD",
        help="Last future vote date to evaluate.",
    )
    p.add_argument(
        "--congress-archive-manifest",
        default=None,
        metavar="PATH",
        help=(
            "Optional local Congress archive manifest whose path and SHA-256 should be "
            "recorded in run_metadata for prediction-input provenance."
        ),
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the prediction input inventory payload.",
    )


def _add_verify_prediction_input_inventory(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-input-inventory",
        help="Verify a prediction-input-inventory artifact and its cutoff metadata.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-input-inventory JSON artifact written by --output.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Require cutoff and source-state run_metadata on the artifact.",
    )
    p.add_argument(
        "--require-congress-archive-manifest",
        action="store_true",
        default=False,
        help=(
            "Require run_metadata to record a readable Congress archive manifest path "
            "with a matching SHA-256."
        ),
    )
    p.add_argument(
        "--require-clean-inventory",
        action="store_true",
        default=False,
        help="Return non-green when inventory has warning or blocking reasons.",
    )
    p.add_argument(
        "--require-portable-jurisdiction-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when portable legislative source rows are missing "
            "explicit jurisdiction_id values."
        ),
    )
    p.add_argument(
        "--require-portable-body-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when portable non-Congress rows are missing explicit "
            "legislative_body_id values."
        ),
    )
    p.add_argument(
        "--require-portable-session-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when portable non-Congress rows are missing explicit "
            "legislative_session_id values."
        ),
    )
    p.add_argument(
        "--require-source-family",
        dest="require_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Return non-green when the inventory source_family_ids do not include "
            "FAMILY. Repeat to require multiple families such as congress_vote and "
            "congress_bill."
        ),
    )
    p.add_argument(
        "--min-training-labels",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory has fewer training labels than N.",
    )
    p.add_argument(
        "--min-evaluation-labels",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory has fewer evaluation labels than N.",
    )
    p.add_argument(
        "--min-training-feature-vote-history-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=("Return non-green when training feature vote-history source coverage is below RATE."),
    )
    p.add_argument(
        "--min-evaluation-feature-vote-history-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Return non-green when evaluation feature vote-history source coverage is below RATE."
        ),
    )
    p.add_argument(
        "--min-training-label-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when training label source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-evaluation-label-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when evaluation label source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-training-label-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when training label official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-evaluation-label-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when evaluation label official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when bill source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when bill official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-sponsor-availability-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when cutoff-available bill sponsor coverage is below RATE.",
    )
    p.add_argument(
        "--require-no-bill-sponsor-introduced-date-fallbacks",
        action="store_true",
        default=False,
        help=(
            "Return non-green when sponsor availability depends on primary-sponsor "
            "introduced_date fallback instead of explicit sponsor dates."
        ),
    )
    p.add_argument(
        "--min-ontology-source-anchor-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when ontology source-anchor coverage is below RATE.",
    )
    p.add_argument(
        "--min-ontology-official-source-anchor-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when ontology official source-anchor coverage is below RATE.",
    )
    p.add_argument(
        "--min-fec-member-attribution-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when FEC member attribution is below RATE.",
    )
    p.add_argument(
        "--min-fec-contributions",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when FEC contribution rows are fewer than N.",
    )
    p.add_argument(
        "--min-member-attributed-fec-contributions",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when member-attributed FEC contribution rows are fewer than N.",
    )
    p.add_argument(
        "--min-members-with-fec-candidate-id",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when members with FEC candidate IDs are fewer than N.",
    )
    p.add_argument(
        "--min-public-statement-signals",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when public-statement signal rows are fewer than N.",
    )
    p.add_argument(
        "--min-members-with-public-statement-signals",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when members with public-statement signals are fewer than N.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the inventory verification audit payload.",
    )


def _add_prediction_eval_report(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-eval-report",
        help="Compare baseline, ontology, and learned vote predictors on temporal windows.",
    )
    p.add_argument(
        "--training-feature-cutoff",
        type=_parse_date,
        default=datetime.date(2022, 12, 31),
        metavar="YYYY-MM-DD",
        help="Latest vote date allowed in learned-model training features.",
    )
    p.add_argument(
        "--train-start",
        type=_parse_date,
        default=datetime.date(2023, 1, 1),
        metavar="YYYY-MM-DD",
        help="First historical vote date used as learned-model labels.",
    )
    p.add_argument(
        "--train-end",
        type=_parse_date,
        default=datetime.date(2024, 12, 31),
        metavar="YYYY-MM-DD",
        help="Last historical vote date used as learned-model labels.",
    )
    p.add_argument(
        "--feature-cutoff",
        type=_parse_date,
        default=datetime.date(2024, 12, 31),
        metavar="YYYY-MM-DD",
        help="Latest vote date allowed in evaluation features.",
    )
    p.add_argument(
        "--label-start",
        type=_parse_date,
        default=datetime.date(2025, 1, 1),
        metavar="YYYY-MM-DD",
        help="First future vote date to evaluate.",
    )
    p.add_argument(
        "--label-end",
        type=_parse_date,
        default=datetime.date(2026, 12, 31),
        metavar="YYYY-MM-DD",
        help="Last future vote date to evaluate.",
    )
    p.add_argument(
        "--bill-semantics-root",
        default=None,
        metavar="PATH",
        help="Optional materialized bill-semantics root for ontology model features.",
    )
    p.add_argument(
        "--congress-archive-manifest",
        default=None,
        metavar="PATH",
        help="Optional Congress archive manifest to record in eval report provenance.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the integrated prediction evaluation report.",
    )
    p.add_argument(
        "--dataset-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the standalone cutoff-safe train/eval dataset.",
    )
    p.add_argument(
        "--manifest-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for an auditable prediction-eval run manifest.",
    )
    p.add_argument(
        "--strict-readiness",
        action="store_true",
        default=False,
        help="Return non-green unless the report readiness status is ready.",
    )
    p.add_argument(
        "--min-training-examples",
        type=int,
        default=None,
        metavar="N",
        help="Optional minimum training example count required for a green report.",
    )
    p.add_argument(
        "--min-evaluation-examples",
        type=int,
        default=None,
        metavar="N",
        help="Optional minimum evaluation example count required for a green report.",
    )
    p.add_argument(
        "--min-bill-semantic-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum bill-semantic coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-bill-metadata-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum loaded-bill-metadata coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-training-bill-semantic-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum training bill-semantic coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-evaluation-bill-semantic-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum evaluation bill-semantic coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-training-feature-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum training feature-source coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-evaluation-feature-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum evaluation feature-source coverage rate required for a green report.",
    )
    p.add_argument(
        "--min-training-feature-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Optional minimum training URL-backed feature-source coverage rate "
            "required for a green report."
        ),
    )
    p.add_argument(
        "--min-training-feature-official-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Optional minimum training official-source-backed feature coverage "
            "rate required for a green report."
        ),
    )
    p.add_argument(
        "--min-evaluation-feature-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Optional minimum evaluation URL-backed feature-source coverage rate "
            "required for a green report."
        ),
    )
    p.add_argument(
        "--min-evaluation-feature-official-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Optional minimum evaluation official-source-backed feature coverage "
            "rate required for a green report."
        ),
    )
    p.add_argument(
        "--min-evaluation-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Optional minimum evaluation label source-url coverage required for a green report.",
    )
    p.add_argument(
        "--fail-on-unknown-bill-semantic-availability",
        action="store_true",
        default=False,
        help=("Return non-green when any cached bill semantic payload lacks an available_at date."),
    )
    p.add_argument(
        "--fail-on-unknown-bill-signal-availability",
        action="store_true",
        default=False,
        help="Return non-green when any bill signal row lacks cutoff-safe availability dates.",
    )
    p.add_argument(
        "--fail-on-unknown-ontology-edge-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green when any ontology edge lacks a timestamp usable "
            "for cutoff-safe evaluation."
        ),
    )
    p.add_argument(
        "--fail-on-unknown-contribution-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green when any contribution signal row lacks a cutoff-safe "
            "contribution_date, date, or as_of_date timestamp."
        ),
    )
    p.add_argument(
        "--fail-on-unknown-statement-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green when any public-statement signal row lacks a cutoff-safe "
            "statement_date, date, or as_of_date timestamp."
        ),
    )
    p.add_argument(
        "--fail-on-mixed-bill-semantics-models",
        action="store_true",
        default=False,
        help="Return non-green when a bill-semantics cache contains multiple model names.",
    )


def _add_prediction_eval_window_plan(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-eval-window-plan",
        help="Plan annual cutoff-safe prediction eval report windows.",
    )
    p.add_argument(
        "--start-label-year",
        type=int,
        default=2024,
        metavar="YEAR",
        help="First evaluation label calendar year to include.",
    )
    p.add_argument(
        "--end-label-year",
        type=int,
        default=2026,
        metavar="YEAR",
        help="Last evaluation label calendar year to include.",
    )
    p.add_argument(
        "--train-years",
        type=int,
        default=2,
        metavar="N",
        help="Number of historical label years to train on before each eval year.",
    )
    p.add_argument(
        "--label-years",
        type=int,
        default=1,
        metavar="N",
        help="Number of evaluation label years per planned window.",
    )
    p.add_argument(
        "--max-feature-cutoff",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Skip windows whose feature cutoff is after this date.",
    )
    p.add_argument(
        "--output-dir",
        default="out",
        metavar="PATH",
        help="Directory embedded in each planned output command.",
    )
    p.add_argument(
        "--bill-semantics-root",
        default=None,
        metavar="PATH",
        help="Optional bill-semantics root embedded in planned eval report commands.",
    )
    p.add_argument(
        "--congress-archive-manifest",
        default=None,
        metavar="PATH",
        help="Optional Congress archive manifest embedded in planned inventory/eval commands.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the window plan payload.",
    )


def _add_verify_prediction_eval_window_plan(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "verify-prediction-eval-window-plan",
        help="Verify a prediction-eval-window-plan artifact before running planned windows.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-eval-window-plan JSON artifact.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Return non-green when the plan artifact lacks matching run metadata.",
    )
    p.add_argument(
        "--require-commands",
        action="store_true",
        default=False,
        help="Return non-green when planned eval/inventory commands are incomplete.",
    )
    p.add_argument(
        "--min-window-count",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when the plan contains fewer than N windows.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the window-plan verification payload.",
    )


def _add_prediction_eval_window_summary(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "prediction-eval-window-summary",
        help="Aggregate multiple prediction-eval-report artifacts into one longitudinal summary.",
    )
    p.add_argument(
        "--report",
        action="append",
        required=True,
        metavar="PATH",
        help="Prediction eval report artifact to aggregate. Repeat for each window.",
    )
    p.add_argument(
        "--require-report-run-metadata",
        action="store_true",
        default=False,
        help="Return non-green when any report lacks prediction-eval-report run metadata.",
    )
    p.add_argument(
        "--require-ready-reports",
        action="store_true",
        default=False,
        help="Return non-green when any report readiness status is not ready.",
    )
    p.add_argument(
        "--require-non-overlapping-label-windows",
        action="store_true",
        default=False,
        help="Return non-green when report label windows overlap.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the aggregated window summary.",
    )


def _add_verify_prediction_eval_window_summary(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "verify-prediction-eval-window-summary",
        help="Verify a prediction-eval-window-summary artifact.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-eval-window-summary JSON artifact.",
    )
    p.add_argument(
        "--plan",
        default=None,
        metavar="PATH",
        help="Optional prediction-eval-window-plan artifact to compare against.",
    )
    p.add_argument(
        "--require-plan-match",
        action="store_true",
        default=False,
        help="Return non-green unless the summary windows match --plan.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Return non-green when the summary lacks provenance metadata.",
    )
    p.add_argument(
        "--require-current-report-hashes",
        action="store_true",
        default=False,
        help="Return non-green when source report hashes no longer match disk.",
    )
    p.add_argument(
        "--require-non-overlapping-label-windows",
        action="store_true",
        default=False,
        help="Return non-green when summarized label windows overlap.",
    )
    p.add_argument(
        "--min-window-count",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when the summary has fewer than N windows.",
    )
    p.add_argument(
        "--min-total-evaluation-labels",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when the summary has fewer than N evaluation labels.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the window-summary verification payload.",
    )


def _add_verify_prediction_eval_window_run(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "verify-prediction-eval-window-run",
        help="Verify all expected artifacts from a prediction-eval-window-plan run.",
    )
    p.add_argument(
        "--plan",
        required=True,
        metavar="PATH",
        help="Path to a prediction-eval-window-plan JSON artifact.",
    )
    p.add_argument(
        "--summary-verify",
        default=None,
        metavar="PATH",
        help=(
            "Optional verify-prediction-eval-window-summary artifact. Defaults to the "
            "plan verify_summary_command --output path."
        ),
    )
    p.add_argument(
        "--require-window-verifiers",
        action="store_true",
        default=False,
        help="Return non-green unless every per-window verifier artifact exists and is passing.",
    )
    p.add_argument(
        "--require-summary-verify",
        action="store_true",
        default=False,
        help="Return non-green unless the summary verifier artifact exists and is passing.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the window-run verification payload.",
    )


def _add_verify_prediction_eval_manifest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-eval-manifest",
        help="Verify prediction eval report/dataset artifacts against a run manifest.",
    )
    p.add_argument(
        "--manifest",
        required=True,
        metavar="PATH",
        help="Path to a prediction-eval manifest written by --manifest-output.",
    )
    p.add_argument(
        "--require-artifact-run-metadata",
        action="store_true",
        default=False,
        help="Require report and dataset artifacts to include auditable run_metadata.",
    )
    p.add_argument(
        "--require-congress-archive-manifest",
        action="store_true",
        default=False,
        help="Require eval manifest inputs to record a readable Congress archive manifest with matching SHA-256.",
    )
    p.add_argument(
        "--require-ready-quality",
        action="store_true",
        default=False,
        help="Return non-green unless manifest quality is ready and warning-free.",
    )
    p.add_argument(
        "--require-failure-analysis",
        action="store_true",
        default=False,
        help="Return non-green unless the manifest carries failure-analysis counts and top groups.",
    )
    p.add_argument(
        "--require-backfill-recommendations",
        action="store_true",
        default=False,
        help="Return non-green unless failure analysis includes at least one backfill recommendation.",
    )
    p.add_argument(
        "--require-fail-on-unknown-bill-semantic-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the manifest thresholds prove eval was run "
            "with unknown bill-semantic availability as a failure."
        ),
    )
    p.add_argument(
        "--require-fail-on-unknown-bill-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the manifest thresholds prove eval was run "
            "with unknown bill-signal availability as a failure."
        ),
    )
    p.add_argument(
        "--require-fail-on-unknown-ontology-edge-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the manifest thresholds prove eval was run "
            "with unknown ontology-edge availability as a failure."
        ),
    )
    p.add_argument(
        "--require-fail-on-unknown-contribution-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the manifest thresholds prove eval was run "
            "with unknown contribution-signal availability as a failure."
        ),
    )
    p.add_argument(
        "--require-fail-on-unknown-statement-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the manifest thresholds prove eval was run "
            "with unknown statement-signal availability as a failure."
        ),
    )
    p.add_argument(
        "--require-bill-semantics-cache",
        action="store_true",
        default=False,
        help="Return non-green unless the manifest records a bill-semantics cache root and index hash.",
    )
    p.add_argument(
        "--require-bill-semantics-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Return non-green unless the manifest's bill-semantics cache contains this model name; repeatable.",
    )
    p.add_argument(
        "--require-bill-semantics-source-inputs-sha256",
        action="store_true",
        default=False,
        help="Return non-green unless the manifest's bill-semantics cache records source_inputs_sha256.",
    )
    p.add_argument(
        "--require-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Require a model_name to appear in the eval report artifact; repeatable.",
    )
    p.add_argument(
        "--require-ontology-feature-signals",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval dataset feature matrix includes required "
            "ontology feature families, train/eval official URL-backed anchors, and learned "
            "coefficients for each family."
        ),
    )
    p.add_argument(
        "--require-source-family",
        dest="require_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Return non-green unless the eval report source_family_ids include FAMILY. "
            "Repeat for multiple families."
        ),
    )
    p.add_argument(
        "--min-bill-semantic-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest bill semantic coverage is below RATE.",
    )
    _add_prediction_eval_manifest_coverage_threshold_args(p)
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the eval-manifest verification audit payload.",
    )


def _add_prediction_source_url_audit(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-source-url-audit",
        help="Audit missing URL-backed feature sources from a prediction eval report.",
    )
    p.add_argument(
        "--eval-report",
        required=True,
        metavar="PATH",
        help="Prediction eval report JSON artifact to inspect.",
    )
    p.add_argument(
        "--fail-on-gaps",
        action="store_true",
        default=False,
        help="Return non-green when any feature source URL gaps are present.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the source URL audit payload.",
    )


def _add_verify_prediction_source_url_audit(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-source-url-audit",
        help="Verify a prediction-source-url-audit artifact and source-report hash.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-source-url-audit JSON artifact written by --output.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Require auditable run_metadata with matching source_state.",
    )
    p.add_argument(
        "--require-no-gaps",
        action="store_true",
        default=False,
        help="Return non-green when any feature source URL gaps remain.",
    )
    p.add_argument(
        "--require-no-official-source-gaps",
        action="store_true",
        default=False,
        help="Return non-green when any feature source lacks an official URL.",
    )
    p.add_argument(
        "--require-no-portable-context-gaps",
        action="store_true",
        default=False,
        help=(
            "Return non-green when portable state/local source samples are missing "
            "legislative body or session IDs."
        ),
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the source URL audit verification payload.",
    )


def _add_verify_prediction_backtest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-backtest",
        help="Verify a prediction-backtest artifact and its semantic-cache metadata.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-backtest JSON artifact written by --output.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Require cutoff, semantic-cache, and source-state run_metadata.",
    )
    p.add_argument(
        "--require-evaluated-predictions",
        action="store_true",
        default=False,
        help="Return non-green when the backtest has zero evaluated predictions.",
    )
    p.add_argument(
        "--require-prediction-source-urls",
        action="store_true",
        default=False,
        help="Return non-green when any prediction label is missing a source URL.",
    )
    p.add_argument(
        "--require-official-prediction-source-urls",
        action="store_true",
        default=False,
        help="Return non-green unless every prediction label source URL is official.",
    )
    p.add_argument(
        "--require-congress-archive-manifest",
        action="store_true",
        default=False,
        help="Return non-green unless run_metadata records a readable Congress archive manifest with matching SHA-256.",
    )
    p.add_argument(
        "--require-bill-semantics-cache",
        action="store_true",
        default=False,
        help="Return non-green unless the backtest artifact records a bill-semantics cache.",
    )
    p.add_argument(
        "--require-bill-semantics-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Return non-green unless the backtest's bill-semantics cache contains this model name; repeatable.",
    )
    p.add_argument(
        "--require-bill-semantics-source-inputs-sha256",
        action="store_true",
        default=False,
        help="Return non-green unless the backtest's bill-semantics cache records source_inputs_sha256.",
    )
    p.add_argument(
        "--require-model-name",
        default=None,
        metavar="NAME",
        help="Return non-green unless the backtest artifact has this model_name.",
    )
    p.add_argument(
        "--require-ontology-feature-signals",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless ontology predictions carry every required "
            "feature family with official URL-backed anchors, with optional loaded "
            "signals marked present or unavailable."
        ),
    )
    p.add_argument(
        "--require-source-family",
        dest="require_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Return non-green unless the backtest source_family_ids include FAMILY. "
            "Repeat for multiple families."
        ),
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the backtest verification audit payload.",
    )


def _add_verify_prediction_benchmark(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-benchmark",
        help="Run strict verification over a complete prediction benchmark artifact set.",
    )
    p.add_argument(
        "--inventory",
        required=True,
        metavar="PATH",
        help="Path to a prediction-input-inventory artifact.",
    )
    p.add_argument(
        "--backtest",
        required=True,
        metavar="PATH",
        help="Path to a prediction-backtest artifact.",
    )
    p.add_argument(
        "--eval-manifest",
        required=True,
        metavar="PATH",
        help="Path to a prediction-eval manifest artifact.",
    )
    p.add_argument(
        "--bill-semantics-plan",
        required=True,
        metavar="PATH",
        help="Path to a dry-run bill-semantics target plan.",
    )
    p.add_argument(
        "--source-url-audit",
        default=None,
        metavar="PATH",
        help="Optional prediction-source-url-audit artifact to verify with the benchmark bundle.",
    )
    p.add_argument(
        "--eval-window-run-verify",
        default=None,
        metavar="PATH",
        help=(
            "Optional verify-prediction-eval-window-run artifact proving rolling "
            "cutoff-window benchmark execution."
        ),
    )
    p.add_argument(
        "--require-eval-window-run-verify",
        action="store_true",
        default=False,
        help="Return non-green unless --eval-window-run-verify is provided and still verifies.",
    )
    p.add_argument(
        "--require-source-url-audit",
        action="store_true",
        default=False,
        help="Return non-green unless --source-url-audit is provided and verifies.",
    )
    p.add_argument(
        "--require-source-url-audit-no-gaps",
        action="store_true",
        default=False,
        help="Return non-green when the source URL audit contains URL gaps.",
    )
    p.add_argument(
        "--require-source-url-audit-no-official-source-gaps",
        action="store_true",
        default=False,
        help="Return non-green when the source URL audit contains official-source gaps.",
    )
    p.add_argument(
        "--require-source-url-audit-no-portable-context-gaps",
        action="store_true",
        default=False,
        help=(
            "Return non-green when the source URL audit contains portable "
            "state/local samples missing legislative body or session IDs."
        ),
    )
    p.add_argument(
        "--bill-semantics-root",
        default=None,
        metavar="PATH",
        help="Optional bill-semantics cache root to verify with the benchmark bundle.",
    )
    p.add_argument(
        "--fail-on-unmatched-targets",
        action="store_true",
        default=False,
        help="Return non-green when the bill-semantics plan has unmatched targets.",
    )
    p.add_argument(
        "--require-bill-semantics-plan-source-anchors",
        action="store_true",
        default=False,
        help="Return non-green when matched bill-semantics plan targets lack source anchors.",
    )
    p.add_argument(
        "--require-bill-semantics-cache",
        action="store_true",
        default=False,
        help="Return non-green unless --bill-semantics-root is provided and verifies.",
    )
    p.add_argument(
        "--require-bill-semantics-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Return non-green unless the verified semantic cache contains this model name; repeatable.",
    )
    p.add_argument(
        "--require-bill-semantics-source-inputs-sha256",
        action="store_true",
        default=False,
        help="Return non-green unless the semantic cache index records source_inputs_sha256.",
    )
    p.add_argument(
        "--require-clean-inventory",
        action="store_true",
        default=False,
        help="Return non-green when inventory has warning or blocking reasons.",
    )
    p.add_argument(
        "--require-inventory-congress-archive-manifest",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the inventory artifact records a Congress archive "
            "manifest path with a matching SHA-256."
        ),
    )
    p.add_argument(
        "--require-portable-jurisdiction-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when the inventory has portable legislative source "
            "rows without explicit jurisdiction_id values."
        ),
    )
    p.add_argument(
        "--require-portable-body-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when the inventory has portable non-Congress rows "
            "without explicit legislative_body_id values."
        ),
    )
    p.add_argument(
        "--require-portable-session-ids",
        action="store_true",
        default=False,
        help=(
            "Return non-green when the inventory has portable non-Congress rows "
            "without explicit legislative_session_id values."
        ),
    )
    p.add_argument(
        "--require-source-family",
        dest="require_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Forward a required inventory source family to "
            "verify-prediction-input-inventory. Repeat for multiple families."
        ),
    )
    p.add_argument(
        "--min-training-labels",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory has fewer training labels than N.",
    )
    p.add_argument(
        "--min-evaluation-labels",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory has fewer evaluation labels than N.",
    )
    p.add_argument(
        "--min-training-feature-vote-history-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Return non-green when inventory training feature vote-history source "
            "coverage is below RATE."
        ),
    )
    p.add_argument(
        "--min-evaluation-feature-vote-history-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Return non-green when inventory evaluation feature vote-history source "
            "coverage is below RATE."
        ),
    )
    p.add_argument(
        "--min-training-label-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory training label source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-evaluation-label-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory evaluation label source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-training-label-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory training label official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-evaluation-label-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory evaluation label official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory bill source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-official-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory bill official source URL coverage is below RATE.",
    )
    p.add_argument(
        "--min-bill-sponsor-availability-rate",
        type=float,
        default=None,
        metavar="RATE",
        help=(
            "Return non-green when inventory cutoff-available bill sponsor coverage is below RATE."
        ),
    )
    p.add_argument(
        "--require-no-bill-sponsor-introduced-date-fallbacks",
        action="store_true",
        default=False,
        help=(
            "Return non-green when inventory sponsor availability depends on "
            "primary-sponsor introduced_date fallback instead of explicit sponsor dates."
        ),
    )
    p.add_argument(
        "--min-ontology-source-anchor-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory ontology source-anchor coverage is below RATE.",
    )
    p.add_argument(
        "--min-ontology-official-source-anchor-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory ontology official source-anchor coverage is below RATE.",
    )
    p.add_argument(
        "--min-fec-member-attribution-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when inventory FEC member attribution is below RATE.",
    )
    p.add_argument(
        "--min-fec-contributions",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory FEC contribution rows are fewer than N.",
    )
    p.add_argument(
        "--min-member-attributed-fec-contributions",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory member-attributed FEC contribution rows are fewer than N.",
    )
    p.add_argument(
        "--min-members-with-fec-candidate-id",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory members with FEC candidate IDs are fewer than N.",
    )
    p.add_argument(
        "--min-public-statement-signals",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory public-statement signal rows are fewer than N.",
    )
    p.add_argument(
        "--min-members-with-public-statement-signals",
        type=int,
        default=None,
        metavar="N",
        help="Return non-green when inventory members with public-statement signals are fewer than N.",
    )
    p.add_argument(
        "--require-evaluated-backtest",
        action="store_true",
        default=False,
        help="Return non-green when the backtest has zero evaluated predictions.",
    )
    p.add_argument(
        "--require-backtest-source-urls",
        action="store_true",
        default=False,
        help="Return non-green when the bundled backtest has prediction labels without source URLs.",
    )
    p.add_argument(
        "--require-backtest-official-source-urls",
        action="store_true",
        default=False,
        help="Return non-green unless bundled backtest prediction source URLs are official.",
    )
    p.add_argument(
        "--require-backtest-congress-archive-manifest",
        action="store_true",
        default=False,
        help="Forward Congress archive manifest provenance checks to verify-prediction-backtest.",
    )
    p.add_argument(
        "--require-backtest-model-name",
        default=None,
        metavar="NAME",
        help="Return non-green unless the bundled backtest has this model_name.",
    )
    p.add_argument(
        "--require-backtest-ontology-feature-signals",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless bundled ontology backtest predictions carry required "
            "feature families with official URL-backed anchors."
        ),
    )
    p.add_argument(
        "--require-backtest-source-family",
        dest="require_backtest_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Forward a required backtest source family to verify-prediction-backtest. "
            "Repeat for multiple families."
        ),
    )
    p.add_argument(
        "--require-ready-quality",
        action="store_true",
        default=False,
        help="Return non-green unless the eval manifest quality block is ready.",
    )
    p.add_argument(
        "--require-eval-failure-analysis",
        action="store_true",
        default=False,
        help="Return non-green unless the eval manifest carries failure-analysis counts and top groups.",
    )
    p.add_argument(
        "--require-eval-backfill-recommendations",
        action="store_true",
        default=False,
        help="Return non-green unless the eval manifest includes at least one backfill recommendation.",
    )
    p.add_argument(
        "--require-eval-congress-archive-manifest",
        action="store_true",
        default=False,
        help="Forward Congress archive manifest provenance checks to verify-prediction-eval-manifest.",
    )
    p.add_argument(
        "--require-eval-fail-on-unknown-bill-semantic-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval manifest proves unknown "
            "bill-semantic availability was configured as a failure."
        ),
    )
    p.add_argument(
        "--require-eval-fail-on-unknown-bill-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval manifest proves unknown "
            "bill-signal availability was configured as a failure."
        ),
    )
    p.add_argument(
        "--require-eval-fail-on-unknown-ontology-edge-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval manifest proves unknown "
            "ontology-edge availability was configured as a failure."
        ),
    )
    p.add_argument(
        "--require-eval-fail-on-unknown-contribution-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval manifest proves unknown "
            "contribution-signal availability was configured as a failure."
        ),
    )
    p.add_argument(
        "--require-eval-fail-on-unknown-statement-signal-availability",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval manifest proves unknown "
            "statement-signal availability was configured as a failure."
        ),
    )
    p.add_argument(
        "--require-eval-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Require a model_name to appear in the eval report artifact; repeatable.",
    )
    p.add_argument(
        "--require-eval-ontology-feature-signals",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless the eval dataset feature matrix includes required "
            "ontology feature families, train/eval official URL-backed anchors, and learned "
            "coefficients for each family."
        ),
    )
    p.add_argument(
        "--require-eval-source-family",
        dest="require_eval_source_families",
        action="append",
        default=[],
        metavar="FAMILY",
        help=(
            "Forward a required eval-manifest source family to "
            "verify-prediction-eval-manifest. Repeat for multiple families."
        ),
    )
    p.add_argument(
        "--min-bill-semantic-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when the eval manifest bill semantic coverage is below RATE.",
    )
    _add_prediction_eval_manifest_coverage_threshold_args(p)
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the benchmark verification audit payload.",
    )
    p.add_argument(
        "--backfill-plan-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the standalone benchmark backfill plan.",
    )
    p.add_argument(
        "--check-backfill-runtime-requirements",
        action="store_true",
        default=False,
        help="Return non-green when generated backfill-plan env/file prerequisites are absent locally.",
    )


def _add_verify_prediction_backfill_plan(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-backfill-plan",
        help="Verify a benchmark backfill_plan artifact or embedded benchmark plan.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a verify-prediction-benchmark artifact or standalone backfill plan.",
    )
    p.add_argument(
        "--require-source-requirements",
        action="store_true",
        default=False,
        help="Return non-green when any backfill step lacks source requirements.",
    )
    p.add_argument(
        "--require-runtime-requirements",
        action="store_true",
        default=False,
        help="Return non-green when any backfill step lacks env/file runtime requirements.",
    )
    p.add_argument(
        "--check-runtime-requirements",
        action="store_true",
        default=False,
        help="Return non-green when listed env/file runtime requirements are missing locally.",
    )
    p.add_argument(
        "--require-supported-suggested-commands",
        action="store_true",
        default=False,
        help="Return non-green when supported backfill actions lack suggested commands.",
    )
    p.add_argument(
        "--require-blocker-links",
        action="store_true",
        default=False,
        help="Return non-green when semantic materialization is not blocked by missing bill metadata it depends on.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        default=False,
        help="Return non-green when the backfill-plan artifact lacks provenance metadata.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the backfill-plan verification audit payload.",
    )


def _add_prediction_offline_readiness_summary(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-offline-readiness-summary",
        help="Summarize local prediction readiness from existing verifier artifacts.",
    )
    p.add_argument(
        "--benchmark-verify",
        required=True,
        metavar="PATH",
        help="Path to a verify-prediction-benchmark output artifact.",
    )
    p.add_argument(
        "--backfill-runtime-verify",
        required=True,
        metavar="PATH",
        help="Path to a verify-prediction-backfill-plan runtime-check artifact.",
    )
    p.add_argument(
        "--backfill-plan",
        default=None,
        metavar="PATH",
        help="Optional prediction-backfill-plan artifact used to inline suggested commands by step.",
    )
    p.add_argument(
        "--env-preflight",
        default=None,
        metavar="PATH",
        help="Optional runtime-env-preflight artifact to include in the readiness snapshot.",
    )
    p.add_argument(
        "--env-preflight-verify",
        default=None,
        metavar="PATH",
        help="Optional verify-runtime-env-preflight artifact to gate env preflight quality.",
    )
    p.add_argument(
        "--congress-load-summary",
        default=None,
        metavar="PATH",
        help="Optional load-congress/load-congress-local artifact to include as a core Congress input check.",
    )
    p.add_argument(
        "--require-congress-load-summary",
        action="store_true",
        default=False,
        help=(
            "Return non-green unless Congress member, bill, and vote prediction inputs "
            "are proven by --congress-load-summary."
        ),
    )
    p.add_argument(
        "--fec-inputs-verify",
        default=None,
        metavar="PATH",
        help="Optional verify-fec-inputs artifact to include as an offline input check.",
    )
    p.add_argument(
        "--public-statement-rows-verify",
        default=None,
        metavar="PATH",
        help="Optional verify-public-statement-rows artifact to include as an offline input check.",
    )
    p.add_argument(
        "--require-eval-window-run",
        action="store_true",
        default=False,
        help="Return non-green unless the benchmark verifier includes a green eval-window run component.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the offline readiness summary payload.",
    )
    p.add_argument(
        "--resume-script-output",
        default=None,
        metavar="PATH",
        help="Optional shell script path containing secret-free resume commands grouped by unlock phase.",
    )


def _add_verify_prediction_offline_readiness_summary(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-offline-readiness-summary",
        help="Verify a prediction-offline-readiness-summary artifact.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-offline-readiness-summary JSON artifact.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green when run_metadata or source_state is missing/stale.",
    )
    p.add_argument(
        "--require-env-preflight-verify",
        action="store_true",
        help="Return non-green when the embedded env-preflight verifier is absent or failed.",
    )
    p.add_argument(
        "--require-eval-window-run",
        action="store_true",
        help="Return non-green when the embedded benchmark eval-window run summary is absent or failed.",
    )
    p.add_argument(
        "--require-resume-script-match",
        action="store_true",
        help="Return non-green when the generated resume script is missing or stale.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the readiness artifact appears to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the offline-readiness verification payload.",
    )


def _add_verify_prediction_resume_script(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-resume-script",
        help="Verify a generated prediction resume script against its readiness summary.",
    )
    p.add_argument(
        "--readiness-summary",
        required=True,
        metavar="PATH",
        help="Path to the prediction-offline-readiness-summary artifact.",
    )
    p.add_argument(
        "--readiness-summary-verify",
        default=None,
        metavar="PATH",
        help="Optional verify-prediction-offline-readiness-summary artifact to gate the summary itself.",
    )
    p.add_argument(
        "--script",
        required=True,
        metavar="PATH",
        help="Path to the generated prediction resume shell script.",
    )
    p.add_argument(
        "--require-env-guards",
        action="store_true",
        help="Return non-green if any env-gated phase lacks its shell guard.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the script appears to contain literal secrets.",
    )
    p.add_argument(
        "--require-safe-commands",
        action="store_true",
        help="Return non-green if executable lines are not approved OpenPact runtime commands.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the resume-script verification payload.",
    )


def _add_verify_prediction_operator_handoff(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-handoff",
        help="Verify the linked env, readiness, and resume verifier handoff artifacts.",
    )
    p.add_argument(
        "--env-preflight-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-runtime-env-preflight artifact.",
    )
    p.add_argument(
        "--readiness-summary-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-offline-readiness-summary artifact.",
    )
    p.add_argument(
        "--resume-script-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-resume-script artifact.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if any verifier artifact appears to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the operator handoff verification payload.",
    )
    p.add_argument(
        "--runbook-output",
        default=None,
        metavar="PATH",
        help="Optional Markdown path for a secret-free operator handoff runbook.",
    )


def _add_verify_prediction_operator_runbook(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-runbook",
        help="Verify a generated prediction operator handoff Markdown runbook.",
    )
    p.add_argument(
        "--handoff-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-operator-handoff artifact.",
    )
    p.add_argument(
        "--runbook",
        required=True,
        metavar="PATH",
        help="Path to the generated Markdown operator runbook.",
    )
    p.add_argument(
        "--require-handoff-runbook-sha",
        action="store_true",
        help="Return non-green if the runbook SHA does not match the handoff artifact.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the runbook appears to contain literal secrets.",
    )
    p.add_argument(
        "--require-verified-artifact-hashes",
        action="store_true",
        help="Return non-green if verifier artifact paths or hashes are absent.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the operator runbook verification payload.",
    )


def _add_prediction_operator_status(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-operator-status",
        help="Summarize prediction launch readiness from a verified operator runbook.",
    )
    p.add_argument(
        "--runbook-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-operator-runbook artifact.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the linked status inputs appear to contain literal secrets.",
    )
    p.add_argument(
        "--require-launch-ready",
        action="store_true",
        help="Return non-green while the underlying readiness summary is still blocked.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the compact operator status payload.",
    )


def _add_verify_prediction_operator_status(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-status",
        help="Verify a compact prediction operator status artifact.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to the prediction-operator-status artifact.",
    )
    p.add_argument(
        "--runbook-verify",
        default=None,
        metavar="PATH",
        help="Optional expected verify-prediction-operator-runbook artifact path.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green if the status artifact lacks run metadata.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the status artifact or linked inputs contain literal secrets.",
    )
    p.add_argument(
        "--require-launch-ready",
        action="store_true",
        help="Return non-green while the verified status is still blocked.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the operator status verification payload.",
    )


def _add_prediction_operator_packet_manifest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-operator-packet-manifest",
        help="Write a manifest for the verified prediction operator handoff packet.",
    )
    p.add_argument(
        "--status-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-operator-status artifact.",
    )
    p.add_argument(
        "--require-existing-files",
        action="store_true",
        help="Return non-green if any packet manifest file is missing.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if packet files appear to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the operator packet manifest.",
    )


def _add_verify_prediction_operator_packet_manifest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-packet-manifest",
        help="Verify a prediction operator packet manifest.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to the prediction-operator-packet-manifest artifact.",
    )
    p.add_argument(
        "--status-verify",
        default=None,
        metavar="PATH",
        help="Optional expected verify-prediction-operator-status artifact path.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green if the packet manifest lacks run metadata.",
    )
    p.add_argument(
        "--require-existing-files",
        action="store_true",
        help="Return non-green if any packet file is missing.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if packet files appear to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the packet manifest verification payload.",
    )


def _add_prediction_operator_packet_export(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-operator-packet-export",
        help="Copy a verified prediction operator packet into one directory.",
    )
    p.add_argument(
        "--manifest-verify",
        required=True,
        metavar="PATH",
        help="Path to the verify-prediction-operator-packet-manifest artifact.",
    )
    p.add_argument(
        "--target-dir",
        required=True,
        metavar="PATH",
        help="Directory where packet files and export manifest should be written.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if exported packet files appear to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the packet export summary.",
    )


def _add_verify_prediction_operator_packet_export(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-packet-export",
        help="Verify a copied prediction operator packet export.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to the prediction-operator-packet-export artifact.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green if the export artifact lacks run metadata.",
    )
    p.add_argument(
        "--require-export-manifest",
        action="store_true",
        help="Return non-green if the export manifest is missing or mismatched.",
    )
    p.add_argument(
        "--require-exported-files",
        action="store_true",
        help="Return non-green if any exported file is missing or hash-mismatched.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if exported files appear to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the packet export verification payload.",
    )


def _add_verify_prediction_operator_packet_directory(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-packet-directory",
        help="Verify a copied prediction operator packet directory in-place.",
    )
    p.add_argument(
        "--packet-dir",
        required=True,
        metavar="PATH",
        help="Path to the exported packet directory.",
    )
    p.add_argument(
        "--require-readme",
        action="store_true",
        help="Return non-green if README.md is missing.",
    )
    p.add_argument(
        "--require-checksums",
        action="store_true",
        help="Return non-green if SHA256SUMS is missing or mismatched.",
    )
    p.add_argument(
        "--require-exported-files",
        action="store_true",
        help="Return non-green if copied packet files are missing or hash-mismatched.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if copied packet files appear to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the packet directory verification payload.",
    )


def _add_prediction_operator_resume_plan(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "prediction-operator-resume-plan",
        help="Dry-run the prediction operator packet resume script without executing it.",
    )
    p.add_argument(
        "--packet-dir",
        required=True,
        metavar="PATH",
        help="Path to the exported operator packet directory.",
    )
    p.add_argument(
        "--dotenv",
        default=None,
        metavar="PATH",
        help="Optional dotenv file to inspect for env-key presence without printing values.",
    )
    p.add_argument(
        "--require-verified-packet",
        action="store_true",
        help="Return non-green if the packet directory fails in-place verification.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the resume script appears to contain literal secrets.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the resume-plan dry-run payload.",
    )


def _add_verify_prediction_operator_resume_plan(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-resume-plan",
        help="Verify a prediction operator resume-plan dry-run artifact.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a prediction-operator-resume-plan artifact.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green if the artifact lacks run metadata.",
    )
    p.add_argument(
        "--require-matches-current-packet",
        action="store_true",
        help="Return non-green if recomputing from the packet directory differs.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the artifact appears to contain literal secrets.",
    )
    p.add_argument(
        "--require-launch-ready",
        action="store_true",
        help="Return non-green unless the resume plan has no blocked env guards.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the resume-plan verification payload.",
    )


def _add_verify_prediction_operator_resume_run(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-prediction-operator-resume-run",
        help="Verify a saved packet run_resume.py audit artifact.",
    )
    p.add_argument(
        "--packet-dir",
        required=True,
        metavar="PATH",
        help="Path to the exported operator packet directory.",
    )
    p.add_argument(
        "--artifact",
        required=True,
        metavar="PATH",
        help="Path to a run_resume.py JSON artifact written with --output.",
    )
    p.add_argument(
        "--dotenv",
        default=None,
        metavar="PATH",
        help="Optional dotenv file to match against the artifact-recorded hash.",
    )
    p.add_argument(
        "--require-run-metadata",
        action="store_true",
        help="Return non-green if the artifact lacks run metadata.",
    )
    p.add_argument(
        "--require-ok",
        action="store_true",
        help="Return non-green unless the run_resume.py artifact is ok.",
    )
    p.add_argument(
        "--require-dry-run",
        action="store_true",
        help="Return non-green unless the saved run was a dry run.",
    )
    p.add_argument(
        "--require-no-secret-literals",
        action="store_true",
        help="Return non-green if the artifact appears to contain literal secrets.",
    )
    p.add_argument(
        "--require-congress-prediction-inputs",
        action="store_true",
        help=(
            "Return non-green unless run metadata proves Congress load produced "
            "member, bill, and vote prediction inputs."
        ),
    )
    p.add_argument(
        "--require-strict-eval-window-run",
        action="store_true",
        help=(
            "Return non-green unless run metadata proves the resume artifact came "
            "from a strict eval-window run."
        ),
    )
    p.add_argument(
        "--require-selected-source-artifact-hashes",
        action="store_true",
        help=(
            "Return non-green unless every selected resume source artifact has a "
            "recorded SHA-256 hash."
        ),
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the resume-run verification payload.",
    )


def _add_prediction_eval_manifest_coverage_threshold_args(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--min-bill-metadata-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest bill metadata coverage is below RATE.",
    )
    parser.add_argument(
        "--min-training-feature-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest training feature source coverage is below RATE.",
    )
    parser.add_argument(
        "--min-evaluation-feature-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest evaluation feature source coverage is below RATE.",
    )
    parser.add_argument(
        "--min-training-feature-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest training feature source URL coverage is below RATE.",
    )
    parser.add_argument(
        "--min-training-feature-official-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest training feature official-source coverage is below RATE.",
    )
    parser.add_argument(
        "--min-evaluation-feature-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest evaluation feature source URL coverage is below RATE.",
    )
    parser.add_argument(
        "--min-evaluation-feature-official-source-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest evaluation feature official-source coverage is below RATE.",
    )
    parser.add_argument(
        "--min-evaluation-source-url-coverage-rate",
        type=float,
        default=None,
        metavar="RATE",
        help="Return non-green when manifest evaluation label source URL coverage is below RATE.",
    )


def _add_publish(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "publish",
        help="Export an immutable public snapshot from the most recent recompute.",
    )
    p.add_argument(
        "--snapshot-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Date key of the snapshot to publish (default: today).",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        metavar="PATH",
        help="Directory to write exported artifacts into. Defaults to local publish root.",
    )
    p.add_argument(
        "--zip-bundle",
        required=True,
        default=None,
        metavar="PATH",
        help="Path to the JSON ZIP bundle used to build ZIP feeds.",
    )


# ---------------------------------------------------------------------------
# Local-oracle subcommand definitions
# ---------------------------------------------------------------------------


def _add_load_congress_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "load-congress-local",
        help="Load congress member, committee, bill, sponsor, and cosponsor data from a local archive.",
    )
    p.add_argument(
        "--archive",
        required=True,
        metavar="PATH",
        help="Path to the local congress archive directory or manifest JSON.",
    )
    p.add_argument(
        "--congress",
        type=int,
        required=True,
        metavar="NUMBER",
        help="Congress number the archive represents (e.g. 119).",
    )


def _add_process_disclosures_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "process-disclosures-local",
        help="Run the parse-transform-load pipeline over a local disclosure bundle.",
    )
    p.add_argument(
        "--bundle",
        required=True,
        metavar="PATH",
        help="Path to the local disclosure bundle (directory or archive).",
    )


def _add_run_oracle_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "run-oracle-local",
        help=(
            "Run the local oracle over a local congress archive and a prebuilt disclosures "
            "bundle. This surface does not fetch live data, request votes, or generate ZIP feeds."
        ),
    )
    p.add_argument(
        "--congress-archive",
        required=True,
        metavar="PATH",
        help="Path to the local congress archive directory or manifest JSON.",
    )
    p.add_argument(
        "--disclosures-bundle",
        required=True,
        metavar="PATH",
        help="Path to the prebuilt local disclosure bundle (directory or archive).",
    )
    p.add_argument(
        "--congress",
        type=int,
        default=None,
        metavar="NUMBER",
        help=(
            "Congress number to use for the archive stage. Defaults to the current Congress "
            "by calendar date when omitted; the resolved value is surfaced in the output."
        ),
    )
    p.add_argument(
        "--snapshot-date",
        type=_parse_date,
        required=True,
        metavar="YYYY-MM-DD",
        help="Date key for the oracle snapshot (e.g. 2025-01-15).",
    )
    p.add_argument(
        "--target-dir",
        required=True,
        metavar="PATH",
        help="Directory where published oracle artifacts are written.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber to process (default: both).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of disclosure artifacts to process (default: no limit).",
    )
    p.add_argument(
        "--snapshot-id",
        default=None,
        metavar="ID",
        help="Explicit snapshot identifier; defaults to snapshot-date ISO string.",
    )
    p.add_argument(
        "--artifact-root",
        default=None,
        metavar="PATH",
        help="Directory containing bundled disclosure artifacts when bundle storage paths are relative.",
    )


def _add_plan_history_backfill(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "plan-history-backfill",
        help="Plan weekly historical snapshot dates for a Congress and target root.",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number to plan historical weekly snapshots for.",
    )
    p.add_argument(
        "--target-root",
        required=True,
        metavar="PATH",
        help="Directory that will hold one per-snapshot publish root per planned week.",
    )
    p.add_argument(
        "--start-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Optional lower bound; clamped to the Congress term.",
    )
    p.add_argument(
        "--end-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Optional upper bound; capped at today for the active Congress.",
    )


def _add_check_history_backfill_inputs(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "check-history-backfill-inputs",
        help="Validate local backfill inputs before replaying historical snapshots.",
    )
    p.add_argument(
        "--congress-archive",
        required=True,
        metavar="PATH",
        help="Path to the local congress archive directory or manifest JSON.",
    )
    p.add_argument(
        "--disclosures-bundle",
        required=True,
        metavar="PATH",
        help="Path to the prebuilt local disclosure bundle JSON.",
    )
    p.add_argument(
        "--artifact-root",
        default=None,
        metavar="PATH",
        help="Directory containing bundled disclosure artifacts when storage paths are relative.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Requested chamber scope to check truthfully against the current replay surface.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Requested disclosure artifact limit to check truthfully against the current replay surface.",
    )


def _add_write_congress_archive_manifest(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "write-congress-archive-manifest",
        help="Scan a local Congress archive tree and write a manifest.json for validated replay.",
    )
    p.add_argument(
        "--archive-root",
        required=True,
        metavar="PATH",
        help="Path to the local Congress archive directory.",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number represented by the archive.",
    )
    p.add_argument(
        "--manifest-path",
        default=None,
        metavar="PATH",
        help="Path to write manifest.json to. Defaults to {archive-root}/manifest.json.",
    )


def _add_materialize_congress_archive(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "materialize-congress-archive",
        help="Fetch official Congress sources and write a canonical local archive tree plus manifest.",
    )
    p.add_argument(
        "--archive-root",
        required=True,
        metavar="PATH",
        help="Path to the local Congress archive directory to create.",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number to fetch and materialize.",
    )
    p.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help="Congress.gov API key. Falls back to OPENPACT_CONGRESS_API_KEY env var.",
    )
    p.add_argument(
        "--include-votes",
        action="store_true",
        default=False,
        help="Also fetch and store vote index/XML files for the requested Congress coverage.",
    )
    p.add_argument(
        "--house-vote-year",
        type=int,
        default=None,
        metavar="YEAR",
        help="Calendar year to fetch House vote XML files for.",
    )
    p.add_argument(
        "--senate-session",
        type=int,
        default=None,
        metavar="SESSION",
        help="Senate session number to fetch vote XML files for.",
    )
    p.add_argument(
        "--manifest-path",
        default=None,
        metavar="PATH",
        help="Path to write manifest.json to. Defaults to {archive-root}/manifest.json.",
    )


def _add_materialize_history_backfill_inputs(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "materialize-history-backfill-inputs",
        help="Build both local backfill input legs, then run the typed readiness check.",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number to materialize for both the archive and replay plan.",
    )
    p.add_argument(
        "--congress-archive-root",
        required=True,
        metavar="PATH",
        help="Path to the local Congress archive directory to create.",
    )
    p.add_argument(
        "--disclosures-bundle-path",
        required=True,
        metavar="PATH",
        help="Path where the canonical disclosures bundle JSON will be written.",
    )
    p.add_argument(
        "--disclosures-artifact-root",
        required=True,
        metavar="PATH",
        help="Directory where disclosure artifacts will be written.",
    )
    p.add_argument(
        "--disclosures-year",
        action="append",
        type=int,
        metavar="YEAR",
        help="Disclosure filing year to materialize. Repeat for multiple years.",
    )
    p.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help="Congress.gov API key. Falls back to OPENPACT_CONGRESS_API_KEY env var.",
    )
    p.add_argument(
        "--include-votes",
        action="store_true",
        default=False,
        help="Also fetch vote XML coverage when materializing the Congress archive.",
    )
    p.add_argument(
        "--house-vote-year",
        type=int,
        default=None,
        metavar="YEAR",
        help="Calendar year to fetch House vote XML files for.",
    )
    p.add_argument(
        "--senate-session",
        type=int,
        default=None,
        metavar="SESSION",
        help="Senate session number to fetch vote XML files for.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber scope for disclosure materialization and readiness checking.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Optional disclosure replay limit to validate against the staged inputs.",
    )
    p.add_argument(
        "--reuse-existing-inputs",
        action="store_true",
        default=False,
        help="Reuse already-staged local inputs when present instead of rematerializing them.",
    )


def _add_materialize_disclosures_bundle(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "materialize-disclosures-bundle",
        help="Fetch official disclosure indexes and PDFs, then write a canonical local bundle.json.",
    )
    p.add_argument(
        "--bundle-path",
        required=True,
        metavar="PATH",
        help="Path where the canonical disclosures bundle JSON will be written.",
    )
    p.add_argument(
        "--artifact-root",
        required=True,
        metavar="PATH",
        help="Directory where downloaded disclosure PDFs will be written.",
    )
    p.add_argument(
        "--year",
        required=True,
        action="append",
        type=int,
        metavar="YEAR",
        help="Calendar filing year to include. Repeat for multiple years.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber scope to materialize (default: both).",
    )


def _add_materialize_bill_semantics(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "materialize-bill-semantics",
        help="Use OpenAI to extract and cache source-backed semantic bill features.",
    )
    p.add_argument(
        "--output-root",
        required=True,
        metavar="PATH",
        help="Directory where bill semantic JSON artifacts and index.json will be written.",
    )
    p.add_argument(
        "--model",
        default="gpt-5.5",
        metavar="MODEL",
        help="OpenAI model for semantic extraction (default: gpt-5.5).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Optional max number of bills to materialize.",
    )
    p.add_argument(
        "--bill-key",
        action="append",
        default=None,
        metavar="KEY",
        help="Materialize only this bill key, for example 119-hr-1. Repeat for multiple bills.",
    )
    p.add_argument(
        "--missing-from-report",
        default=None,
        metavar="PATH",
        help=(
            "Materialize bill keys listed in prediction-eval-report semantic "
            "coverage missing_bill_keys fields."
        ),
    )
    p.add_argument(
        "--feature-cutoff",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Only materialize bills available at or before this feature cutoff, "
            "matching prediction inventory/eval windows."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Plan semantic materialization targets without calling OpenAI or writing cache files.",
    )
    p.add_argument(
        "--plan-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for a dry-run bill-semantics target plan.",
    )
    p.add_argument(
        "--summary-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the bill-semantics materialization summary.",
    )
    p.add_argument(
        "--fail-on-unmatched-targets",
        action="store_true",
        default=False,
        help="Return non-green when requested bill keys are not available in loaded bills.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Recompute semantic payloads even when cached files already exist.",
    )


def _add_verify_bill_semantics(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-bill-semantics",
        help="Verify a materialized bill-semantics cache index and payload hashes.",
    )
    p.add_argument(
        "--root",
        required=True,
        metavar="PATH",
        help="Bill-semantics cache root containing index.json and bills/*.json.",
    )
    p.add_argument(
        "--require-model-name",
        action="append",
        default=None,
        metavar="NAME",
        help="Return non-green unless the semantic cache contains this model name; repeatable.",
    )
    p.add_argument(
        "--require-source-inputs-sha256",
        action="store_true",
        default=False,
        help="Return non-green unless the cache index records source_inputs_sha256.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the bill-semantics verification audit payload.",
    )


def _add_verify_bill_semantics_plan(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-bill-semantics-plan",
        help="Verify a bill-semantics dry-run target plan and source eval-report hash.",
    )
    p.add_argument(
        "--plan",
        required=True,
        metavar="PATH",
        help="Path to a dry-run bill-semantics plan written by --plan-output.",
    )
    p.add_argument(
        "--fail-on-unmatched-targets",
        action="store_true",
        default=False,
        help="Return non-green when the plan contains unmatched target bill keys.",
    )
    p.add_argument(
        "--require-source-report",
        action="store_true",
        default=False,
        help="Require source eval-report path, hash, missing keys, and run metadata.",
    )
    p.add_argument(
        "--require-matched-source-anchors",
        action="store_true",
        default=False,
        help="Return non-green when matched plan bills lack source anchors.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the bill-semantics-plan verification audit payload.",
    )


def _add_run_history_launch_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "run-history-launch-local",
        help="Materialize local replay inputs, validate readiness, then run the launch-window history backfill.",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number to materialize and replay historically.",
    )
    p.add_argument(
        "--congress-archive-root",
        required=True,
        metavar="PATH",
        help="Path to the local Congress archive directory to create.",
    )
    p.add_argument(
        "--disclosures-bundle-path",
        required=True,
        metavar="PATH",
        help="Path where the canonical disclosures bundle JSON will be written.",
    )
    p.add_argument(
        "--disclosures-artifact-root",
        required=True,
        metavar="PATH",
        help="Directory where disclosure artifacts will be written.",
    )
    p.add_argument(
        "--disclosures-year",
        action="append",
        type=int,
        metavar="YEAR",
        help="Disclosure filing year to materialize. Repeat for multiple years.",
    )
    p.add_argument(
        "--target-root",
        required=True,
        metavar="PATH",
        help="Directory that will hold one per-snapshot publish root per planned week.",
    )
    p.add_argument(
        "--aggregate-root",
        default=None,
        metavar="PATH",
        help="Optional directory to write the merged history-serving publish root into.",
    )
    p.add_argument(
        "--start-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Optional lower bound; clamped to the Congress term.",
    )
    p.add_argument(
        "--end-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Optional upper bound; capped at today for the active Congress.",
    )
    p.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help="Congress.gov API key. Falls back to OPENPACT_CONGRESS_API_KEY env var.",
    )
    p.add_argument(
        "--include-votes",
        action="store_true",
        default=False,
        help="Also fetch vote XML coverage when materializing the Congress archive.",
    )
    p.add_argument(
        "--house-vote-year",
        type=int,
        default=None,
        metavar="YEAR",
        help="Calendar year to fetch House vote XML files for.",
    )
    p.add_argument(
        "--senate-session",
        type=int,
        default=None,
        metavar="SESSION",
        help="Senate session number to fetch vote XML files for.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber scope for disclosure materialization and replay.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of disclosure artifacts to process per snapshot (default: no limit).",
    )
    p.add_argument(
        "--reuse-existing-inputs",
        action="store_true",
        default=False,
        help="Reuse already-staged local inputs when present instead of rematerializing them.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Replace existing per-snapshot publish roots instead of skipping them.",
    )
    p.add_argument(
        "--continue-on-error",
        action="store_true",
        default=False,
        help="Continue attempting later snapshots after a failure.",
    )


def _add_run_history_backfill_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "run-history-backfill-local",
        help="Replay the local oracle weekly across a Congress window and optionally aggregate the results.",
    )
    p.add_argument(
        "--congress-archive",
        required=True,
        metavar="PATH",
        help="Path to the local congress archive directory or manifest JSON.",
    )
    p.add_argument(
        "--disclosures-bundle",
        required=True,
        metavar="PATH",
        help="Path to the prebuilt local disclosure bundle (directory or archive).",
    )
    p.add_argument(
        "--congress",
        required=True,
        type=int,
        metavar="NUMBER",
        help="Congress number to replay historically.",
    )
    p.add_argument(
        "--target-root",
        required=True,
        metavar="PATH",
        help="Directory that will hold one per-snapshot publish root per planned week.",
    )
    p.add_argument(
        "--aggregate-root",
        default=None,
        metavar="PATH",
        help="Optional directory to write the merged history-serving publish root into.",
    )
    p.add_argument(
        "--start-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Optional lower bound; clamped to the Congress term.",
    )
    p.add_argument(
        "--end-date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Optional upper bound; capped at today for the active Congress.",
    )
    p.add_argument(
        "--chamber",
        choices=["house", "senate", "both"],
        default="both",
        help="Chamber to process in disclosure replay steps (default: both).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of disclosure artifacts to process per snapshot (default: no limit).",
    )
    p.add_argument(
        "--artifact-root",
        default=None,
        metavar="PATH",
        help="Directory containing bundled disclosure artifacts when bundle storage paths are relative.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Replace existing per-snapshot publish roots instead of skipping them.",
    )
    p.add_argument(
        "--continue-on-error",
        action="store_true",
        default=False,
        help="Continue attempting later snapshots after a failure.",
    )


def _add_aggregate_history(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "aggregate-history",
        help="Merge many per-snapshot publish roots into one history-serving publish root.",
    )
    p.add_argument(
        "--source-root",
        required=True,
        action="append",
        metavar="PATH",
        help="Per-snapshot publish root to aggregate. Repeat for multiple snapshots.",
    )
    p.add_argument(
        "--target-root",
        required=True,
        metavar="PATH",
        help="Directory to write the aggregated history-serving publish root into.",
    )


def _add_verify_publish(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-publish",
        help="Verify the integrity of a locally published snapshot tree.",
    )
    p.add_argument(
        "--publish-root",
        required=True,
        metavar="PATH",
        help="Root directory of the published snapshot tree to verify.",
    )


def _add_verify_publish_roundtrip(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-publish-roundtrip",
        help="Verify the DB-to-publish roundtrip for a locally published snapshot tree.",
    )
    p.add_argument(
        "--publish-root",
        required=True,
        metavar="PATH",
        help="Root directory of the published snapshot tree to verify.",
    )


def _add_verify_history_aggregate(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-history-aggregate",
        help="Verify the internal consistency of a locally aggregated history-serving publish root.",
    )
    p.add_argument(
        "--publish-root",
        required=True,
        metavar="PATH",
        help="Root directory of the history aggregate publish tree to verify.",
    )


# ---------------------------------------------------------------------------
# Argument type helpers
# ---------------------------------------------------------------------------


def _parse_date(value: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'. Expected YYYY-MM-DD.")
