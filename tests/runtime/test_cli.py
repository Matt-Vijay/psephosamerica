"""Tests for the CLI parser layer.

No DB, no network.  All assertions target parser output only.
"""

from __future__ import annotations

import datetime
import shlex
from pathlib import Path

import pytest

from src.runtime.cli import build_parser, parse_args


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


def test_build_parser_returns_argument_parser():
    import argparse

    assert isinstance(build_parser(), argparse.ArgumentParser)


def test_parser_prog_name():
    assert build_parser().prog == "openpact"


def _subcommand_parser(name: str):
    parser = build_parser()
    return parser._subparsers._group_actions[0].choices[name]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Missing subcommand raises SystemExit
# ---------------------------------------------------------------------------


def test_no_subcommand_exits():
    with pytest.raises(SystemExit):
        parse_args([])


# ---------------------------------------------------------------------------
# bootstrap-db
# ---------------------------------------------------------------------------


def test_bootstrap_db_command():
    ns = parse_args(["bootstrap-db"])
    assert ns.command == "bootstrap-db"
    assert ns.dry_run is False


def test_bootstrap_db_dry_run():
    ns = parse_args(["bootstrap-db", "--dry-run"])
    assert ns.dry_run is True


def test_bootstrap_db_help_references_schema_sql_not_migrations():
    help_text = _subcommand_parser("bootstrap-db").format_help()
    assert "db/schema.sql" in help_text
    assert "initial migration" not in help_text.lower()


def test_bootstrap_db_dry_run_help_describes_plan_not_sql_dump():
    help_text = _subcommand_parser("bootstrap-db").format_help()
    assert "plan" in help_text.lower()
    assert "print sql" not in help_text.lower()


# ---------------------------------------------------------------------------
# runtime-env-preflight
# ---------------------------------------------------------------------------


def test_runtime_env_preflight_accepts_sanitized_env_check_args():
    ns = parse_args(
        [
            "runtime-env-preflight",
            "--require-env",
            "OPENAI_API_KEY",
            "--require-env",
            "OPENPACT_POSTGRES_DSN",
            "--dotenv",
            "/tmp/openpact.env",
            "--output",
            "/tmp/env-preflight.json",
            "--template-output",
            "/tmp/openpact.env.example",
        ]
    )

    assert ns.command == "runtime-env-preflight"
    assert ns.require_env == ["OPENAI_API_KEY", "OPENPACT_POSTGRES_DSN"]
    assert ns.dotenv == "/tmp/openpact.env"
    assert ns.output == "/tmp/env-preflight.json"
    assert ns.template_output == "/tmp/openpact.env.example"


def test_verify_runtime_env_preflight_accepts_artifact_gates():
    ns = parse_args(
        [
            "verify-runtime-env-preflight",
            "--artifact",
            "/tmp/env-preflight.json",
            "--require-next-actions",
            "--require-template-output",
            "--require-no-secret-literals",
            "--output",
            "/tmp/env-preflight-verify.json",
        ]
    )

    assert ns.command == "verify-runtime-env-preflight"
    assert ns.artifact == "/tmp/env-preflight.json"
    assert ns.require_next_actions is True
    assert ns.require_template_output is True
    assert ns.require_no_secret_literals is True
    assert ns.output == "/tmp/env-preflight-verify.json"


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_command():
    ns = parse_args(["status"])
    assert ns.command == "status"
    assert ns.limit == 20


def test_status_custom_limit():
    ns = parse_args(["status", "--limit", "5"])
    assert ns.limit == 5


def test_status_limit_invalid_exits():
    with pytest.raises(SystemExit):
        parse_args(["status", "--limit", "notanumber"])


# ---------------------------------------------------------------------------
# load-congress
# ---------------------------------------------------------------------------


def test_load_congress_command():
    ns = parse_args(["load-congress"])
    assert ns.command == "load-congress"
    assert ns.congress is None
    assert ns.api_key is None


def test_load_congress_with_congress_number():
    ns = parse_args(["load-congress", "--congress", "118"])
    assert ns.congress == 118


def test_load_congress_with_api_key():
    ns = parse_args(["load-congress", "--api-key", "abc123"])
    assert ns.api_key == "abc123"


def test_load_congress_number_invalid_exits():
    with pytest.raises(SystemExit):
        parse_args(["load-congress", "--congress", "notanumber"])


def test_load_congress_include_votes_default():
    ns = parse_args(["load-congress"])
    assert ns.include_votes is False


def test_load_congress_include_votes_flag():
    ns = parse_args(["load-congress", "--include-votes"])
    assert ns.include_votes is True


def test_load_congress_house_vote_year_default():
    ns = parse_args(["load-congress"])
    assert ns.house_vote_year is None


def test_load_congress_house_vote_year():
    ns = parse_args(["load-congress", "--house-vote-year", "2024"])
    assert ns.include_votes is True
    assert ns.house_vote_year == 2024


def test_load_congress_house_vote_year_invalid_exits():
    with pytest.raises(SystemExit):
        parse_args(["load-congress", "--house-vote-year", "notanumber"])


def test_load_congress_senate_session_default():
    ns = parse_args(["load-congress"])
    assert ns.senate_session is None


def test_load_congress_senate_session():
    ns = parse_args(["load-congress", "--senate-session", "1"])
    assert ns.include_votes is True
    assert ns.senate_session == 1


def test_load_congress_senate_session_invalid_exits():
    with pytest.raises(SystemExit):
        parse_args(["load-congress", "--senate-session", "notanumber"])


def test_load_congress_all_new_flags():
    ns = parse_args(
        [
            "load-congress",
            "--congress",
            "118",
            "--include-votes",
            "--house-vote-year",
            "2024",
            "--senate-session",
            "2",
        ]
    )
    assert ns.congress == 118
    assert ns.include_votes is True
    assert ns.house_vote_year == 2024
    assert ns.senate_session == 2


# ---------------------------------------------------------------------------
# load-fec-local
# ---------------------------------------------------------------------------


def test_load_fec_local_command():
    ns = parse_args(
        [
            "load-fec-local",
            "--committee-master",
            "/data/fec/cm.txt",
            "--candidate-committee-linkage",
            "/data/fec/ccl.txt",
            "--individual-contributions",
            "/data/fec/itcont.txt",
        ]
    )
    assert ns.command == "load-fec-local"
    assert ns.committee_master == "/data/fec/cm.txt"
    assert ns.candidate_committee_linkage == "/data/fec/ccl.txt"
    assert ns.individual_contributions == "/data/fec/itcont.txt"
    assert ns.committee_source_url is None
    assert ns.linkage_source_url is None
    assert ns.contribution_source_url is None


def test_load_fec_local_requires_all_files():
    with pytest.raises(SystemExit):
        parse_args(["load-fec-local", "--committee-master", "/data/fec/cm.txt"])


def test_load_fec_local_accepts_explicit_source_urls():
    ns = parse_args(
        [
            "load-fec-local",
            "--committee-master",
            "/data/fec/cm.txt",
            "--candidate-committee-linkage",
            "/data/fec/ccl.txt",
            "--individual-contributions",
            "/data/fec/itcont.txt",
            "--committee-source-url",
            "https://www.fec.gov/files/bulk-downloads/2024/cm24.zip",
            "--linkage-source-url",
            "https://www.fec.gov/files/bulk-downloads/2024/ccl24.zip",
            "--contribution-source-url",
            "https://www.fec.gov/files/bulk-downloads/2024/indiv24.zip",
            "--contribution-chunk-size",
            "1000",
        ]
    )
    assert ns.committee_source_url == "https://www.fec.gov/files/bulk-downloads/2024/cm24.zip"
    assert ns.linkage_source_url == "https://www.fec.gov/files/bulk-downloads/2024/ccl24.zip"
    assert ns.contribution_source_url == "https://www.fec.gov/files/bulk-downloads/2024/indiv24.zip"
    assert ns.contribution_chunk_size == 1000


def test_verify_fec_inputs_command():
    ns = parse_args(
        [
            "verify-fec-inputs",
            "--committee-master",
            "/data/fec/cm.txt",
            "--candidate-committee-linkage",
            "/data/fec/ccl.txt",
            "--individual-contributions",
            "/data/fec/itcont.txt",
            "--member-fec-crosswalk",
            "/data/crosswalks/member_fec.csv",
            "--require-member-fec-crosswalk",
            "--min-committee-rows",
            "1",
            "--min-linkage-rows",
            "2",
            "--min-contribution-rows",
            "3",
            "--min-member-fec-rows",
            "4",
            "--output",
            "/tmp/fec-inputs-verify.json",
        ]
    )

    assert ns.command == "verify-fec-inputs"
    assert ns.committee_master == "/data/fec/cm.txt"
    assert ns.candidate_committee_linkage == "/data/fec/ccl.txt"
    assert ns.individual_contributions == "/data/fec/itcont.txt"
    assert ns.member_fec_crosswalk == "/data/crosswalks/member_fec.csv"
    assert ns.require_member_fec_crosswalk is True
    assert ns.min_committee_rows == 1
    assert ns.min_linkage_rows == 2
    assert ns.min_contribution_rows == 3
    assert ns.min_member_fec_rows == 4
    assert ns.output == "/tmp/fec-inputs-verify.json"


def test_materialize_fec_bulk_files_command():
    ns = parse_args(
        [
            "materialize-fec-bulk-files",
            "--cycle",
            "2024",
            "--output-dir",
            "/data/fec",
            "--dry-run",
            "--force",
            "--timeout",
            "12",
            "--committee-url",
            "https://www.fec.gov/files/bulk-downloads/2024/cm24.zip",
            "--linkage-url",
            "https://www.fec.gov/files/bulk-downloads/2024/ccl24.zip",
            "--contribution-url",
            "https://www.fec.gov/files/bulk-downloads/2024/indiv24.zip",
            "--summary-output",
            "/tmp/fec-summary.json",
        ]
    )

    assert ns.command == "materialize-fec-bulk-files"
    assert ns.cycle == 2024
    assert ns.output_dir == "/data/fec"
    assert ns.dry_run is True
    assert ns.force is True
    assert ns.filter_individual_contributions is True
    assert ns.timeout == 12
    assert ns.committee_url == "https://www.fec.gov/files/bulk-downloads/2024/cm24.zip"
    assert ns.linkage_url == "https://www.fec.gov/files/bulk-downloads/2024/ccl24.zip"
    assert ns.contribution_url == ("https://www.fec.gov/files/bulk-downloads/2024/indiv24.zip")
    assert ns.summary_output == "/tmp/fec-summary.json"


def test_materialize_fec_bulk_files_can_disable_individual_contribution_filter():
    ns = parse_args(
        [
            "materialize-fec-bulk-files",
            "--cycle",
            "2024",
            "--no-filter-individual-contributions",
            "--member-fec-crosswalk",
            "/data/crosswalks/member_fec.csv",
        ]
    )

    assert ns.filter_individual_contributions is False
    assert ns.member_fec_crosswalk == "/data/crosswalks/member_fec.csv"


def test_load_member_fec_crosswalk_local_command():
    ns = parse_args(
        [
            "load-member-fec-crosswalk-local",
            "--crosswalk",
            "/data/crosswalks/member_fec.csv",
            "--member-terms",
            "/data/crosswalks/member_terms.csv",
        ]
    )
    assert ns.command == "load-member-fec-crosswalk-local"
    assert ns.crosswalk == "/data/crosswalks/member_fec.csv"
    assert ns.member_terms == "/data/crosswalks/member_terms.csv"
    assert ns.source_url is None


def test_load_member_fec_crosswalk_local_accepts_source_url():
    ns = parse_args(
        [
            "load-member-fec-crosswalk-local",
            "--crosswalk",
            "/data/crosswalks/member_fec.csv",
            "--source-url",
            "https://github.com/unitedstates/congress-legislators",
        ]
    )
    assert ns.source_url == "https://github.com/unitedstates/congress-legislators"


def test_materialize_member_fec_crosswalk_command():
    ns = parse_args(
        [
            "materialize-member-fec-crosswalk",
            "--output",
            "/data/crosswalks/member_fec.csv",
            "--terms-output",
            "/data/crosswalks/member_terms.csv",
            "--source-file",
            "/tmp/legislators-current.yaml",
            "--source-url",
            "https://example.test/legislators-current.yaml",
            "--dry-run",
            "--force",
            "--timeout",
            "12",
            "--summary-output",
            "/tmp/member-fec-summary.json",
        ]
    )

    assert ns.command == "materialize-member-fec-crosswalk"
    assert ns.output == "/data/crosswalks/member_fec.csv"
    assert ns.terms_output == "/data/crosswalks/member_terms.csv"
    assert ns.source_file == "/tmp/legislators-current.yaml"
    assert ns.source_url == "https://example.test/legislators-current.yaml"
    assert ns.dry_run is True
    assert ns.force is True
    assert ns.timeout == 12
    assert ns.summary_output == "/tmp/member-fec-summary.json"


def test_prediction_input_inventory_command_defaults():
    ns = parse_args(["prediction-input-inventory"])
    assert ns.command == "prediction-input-inventory"
    assert ns.training_feature_cutoff == datetime.date(2022, 12, 31)
    assert ns.train_start == datetime.date(2023, 1, 1)
    assert ns.train_end == datetime.date(2024, 12, 31)
    assert ns.feature_cutoff == datetime.date(2024, 12, 31)
    assert ns.label_start == datetime.date(2025, 1, 1)
    assert ns.label_end == datetime.date(2026, 12, 31)
    assert ns.congress_archive_manifest is None
    assert ns.output is None


def test_prediction_input_inventory_command_accepts_dates_and_output():
    ns = parse_args(
        [
            "prediction-input-inventory",
            "--training-feature-cutoff",
            "2020-12-31",
            "--train-start",
            "2021-01-01",
            "--train-end",
            "2022-12-31",
            "--feature-cutoff",
            "2022-12-31",
            "--label-start",
            "2023-01-01",
            "--label-end",
            "2023-12-31",
            "--congress-archive-manifest",
            "/tmp/congress_119/manifest.json",
            "--output",
            "/tmp/inventory.json",
        ]
    )
    assert ns.training_feature_cutoff == datetime.date(2020, 12, 31)
    assert ns.congress_archive_manifest == "/tmp/congress_119/manifest.json"
    assert ns.output == "/tmp/inventory.json"


def test_verify_prediction_input_inventory_accepts_artifact_path():
    ns = parse_args(
        [
            "verify-prediction-input-inventory",
            "--artifact",
            "/tmp/inventory.json",
            "--require-run-metadata",
            "--require-congress-archive-manifest",
            "--require-clean-inventory",
            "--require-portable-jurisdiction-ids",
            "--require-portable-body-ids",
            "--require-portable-session-ids",
            "--require-source-family",
            "congress_vote",
            "--require-source-family",
            "congress_bill",
            "--min-training-labels",
            "1000",
            "--min-evaluation-labels",
            "500",
            "--min-training-feature-vote-history-source-coverage-rate",
            "0.93",
            "--min-evaluation-feature-vote-history-source-coverage-rate",
            "0.92",
            "--min-training-label-source-url-coverage-rate",
            "0.98",
            "--min-evaluation-label-source-url-coverage-rate",
            "0.97",
            "--min-training-label-official-source-url-coverage-rate",
            "0.96",
            "--min-evaluation-label-official-source-url-coverage-rate",
            "0.95",
            "--min-bill-source-url-coverage-rate",
            "0.95",
            "--min-bill-official-source-url-coverage-rate",
            "0.94",
            "--min-bill-sponsor-availability-rate",
            "0.93",
            "--require-no-bill-sponsor-introduced-date-fallbacks",
            "--min-ontology-source-anchor-coverage-rate",
            "0.9",
            "--min-ontology-official-source-anchor-coverage-rate",
            "0.88",
            "--min-fec-member-attribution-rate",
            "0.85",
            "--min-fec-contributions",
            "100",
            "--min-member-attributed-fec-contributions",
            "90",
            "--min-members-with-fec-candidate-id",
            "25",
            "--min-public-statement-signals",
            "75",
            "--min-members-with-public-statement-signals",
            "20",
            "--output",
            "/tmp/inventory-verify.json",
        ]
    )
    assert ns.command == "verify-prediction-input-inventory"
    assert ns.artifact == "/tmp/inventory.json"
    assert ns.require_run_metadata is True
    assert ns.require_congress_archive_manifest is True
    assert ns.require_clean_inventory is True
    assert ns.require_portable_jurisdiction_ids is True
    assert ns.require_portable_body_ids is True
    assert ns.require_portable_session_ids is True
    assert ns.require_source_families == ["congress_vote", "congress_bill"]
    assert ns.min_training_labels == 1000
    assert ns.min_evaluation_labels == 500
    assert ns.min_training_feature_vote_history_source_coverage_rate == 0.93
    assert ns.min_evaluation_feature_vote_history_source_coverage_rate == 0.92
    assert ns.min_training_label_source_url_coverage_rate == 0.98
    assert ns.min_evaluation_label_source_url_coverage_rate == 0.97
    assert ns.min_training_label_official_source_url_coverage_rate == 0.96
    assert ns.min_evaluation_label_official_source_url_coverage_rate == 0.95
    assert ns.min_bill_source_url_coverage_rate == 0.95
    assert ns.min_bill_official_source_url_coverage_rate == 0.94
    assert ns.min_bill_sponsor_availability_rate == 0.93
    assert ns.require_no_bill_sponsor_introduced_date_fallbacks is True
    assert ns.min_ontology_source_anchor_coverage_rate == 0.9
    assert ns.min_ontology_official_source_anchor_coverage_rate == 0.88
    assert ns.min_fec_member_attribution_rate == 0.85
    assert ns.min_fec_contributions == 100
    assert ns.min_member_attributed_fec_contributions == 90
    assert ns.min_members_with_fec_candidate_id == 25
    assert ns.min_public_statement_signals == 75
    assert ns.min_members_with_public_statement_signals == 20
    assert ns.output == "/tmp/inventory-verify.json"


# ---------------------------------------------------------------------------
# load-disclosures
# ---------------------------------------------------------------------------


def test_load_disclosures_command():
    ns = parse_args(["load-disclosures"])
    assert ns.command == "load-disclosures"
    assert ns.chamber == "both"
    assert ns.year is None


def test_load_disclosures_chamber_house():
    ns = parse_args(["load-disclosures", "--chamber", "house"])
    assert ns.chamber == "house"


def test_load_disclosures_chamber_senate():
    ns = parse_args(["load-disclosures", "--chamber", "senate"])
    assert ns.chamber == "senate"


def test_load_disclosures_invalid_chamber_exits():
    with pytest.raises(SystemExit):
        parse_args(["load-disclosures", "--chamber", "closet"])


def test_load_disclosures_year():
    ns = parse_args(["load-disclosures", "--year", "2024"])
    assert ns.year == 2024


# ---------------------------------------------------------------------------
# parse-disclosures
# ---------------------------------------------------------------------------


def test_parse_disclosures_command():
    ns = parse_args(["parse-disclosures"])
    assert ns.command == "parse-disclosures"
    assert ns.chamber == "both"
    assert ns.local_root is None
    assert ns.limit is None


def test_parse_disclosures_chamber_house():
    ns = parse_args(["parse-disclosures", "--chamber", "house"])
    assert ns.chamber == "house"


def test_parse_disclosures_chamber_senate():
    ns = parse_args(["parse-disclosures", "--chamber", "senate"])
    assert ns.chamber == "senate"


def test_parse_disclosures_invalid_chamber_exits():
    with pytest.raises(SystemExit):
        parse_args(["parse-disclosures", "--chamber", "congress"])


def test_parse_disclosures_limit():
    ns = parse_args(["parse-disclosures", "--limit", "50"])
    assert ns.limit == 50


def test_parse_disclosures_limit_invalid_exits():
    with pytest.raises(SystemExit):
        parse_args(["parse-disclosures", "--limit", "notanumber"])


def test_parse_disclosures_local_root():
    ns = parse_args(["parse-disclosures", "--local-root", "/data/artifacts"])
    assert ns.local_root == "/data/artifacts"


def test_parse_disclosures_all_args():
    ns = parse_args(
        [
            "parse-disclosures",
            "--chamber",
            "senate",
            "--local-root",
            "/data/artifacts",
            "--limit",
            "100",
        ]
    )
    assert ns.chamber == "senate"
    assert ns.local_root == "/data/artifacts"
    assert ns.limit == 100


# ---------------------------------------------------------------------------
# recompute
# ---------------------------------------------------------------------------


def test_recompute_command():
    ns = parse_args(["recompute"])
    assert ns.command == "recompute"
    assert ns.snapshot_date is None


def test_recompute_snapshot_date_parsed():
    ns = parse_args(["recompute", "--snapshot-date", "2025-01-15"])
    assert ns.snapshot_date == datetime.date(2025, 1, 15)


def test_recompute_statement_rows_path_parsed():
    ns = parse_args(["recompute", "--statement-rows", "/tmp/statements.jsonl"])
    assert ns.statement_rows == "/tmp/statements.jsonl"


def test_verify_public_statement_rows_command():
    ns = parse_args(
        [
            "verify-public-statement-rows",
            "--statement-rows",
            "/tmp/statements.jsonl",
            "--min-rows",
            "1",
            "--output",
            "/tmp/statement-verify.json",
        ]
    )
    assert ns.command == "verify-public-statement-rows"
    assert ns.statement_rows == "/tmp/statements.jsonl"
    assert ns.min_rows == 1
    assert ns.output == "/tmp/statement-verify.json"


def test_materialize_public_statement_rows_command():
    ns = parse_args(
        [
            "materialize-public-statement-rows",
            "--input",
            "/tmp/raw-statements.jsonl",
            "--output",
            "/tmp/prepared-statements.jsonl",
            "--taxonomy",
            "/tmp/sectors.yaml",
            "--dry-run",
            "--force",
            "--summary-output",
            "/tmp/statement-materialize.json",
        ]
    )
    assert ns.command == "materialize-public-statement-rows"
    assert ns.input == "/tmp/raw-statements.jsonl"
    assert ns.output == "/tmp/prepared-statements.jsonl"
    assert ns.taxonomy == "/tmp/sectors.yaml"
    assert ns.dry_run is True
    assert ns.force is True
    assert ns.summary_output == "/tmp/statement-materialize.json"


def test_materialize_public_statement_rss_command():
    ns = parse_args(
        [
            "materialize-public-statement-rss",
            "--output",
            "/tmp/raw-statements.jsonl",
            "--source-file",
            "/tmp/legislators-current.yaml",
            "--timeout",
            "12",
            "--max-feeds",
            "2",
            "--max-items-per-feed",
            "3",
            "--dry-run",
            "--force",
            "--summary-output",
            "/tmp/statement-rss.json",
        ]
    )
    assert ns.command == "materialize-public-statement-rss"
    assert ns.output == "/tmp/raw-statements.jsonl"
    assert ns.source_file == "/tmp/legislators-current.yaml"
    assert ns.timeout == 12.0
    assert ns.max_feeds == 2
    assert ns.max_items_per_feed == 3
    assert ns.dry_run is True
    assert ns.force is True
    assert ns.summary_output == "/tmp/statement-rss.json"


def test_recompute_invalid_date_exits():
    with pytest.raises(SystemExit):
        parse_args(["recompute", "--snapshot-date", "notadate"])


def test_recompute_malformed_date_exits():
    with pytest.raises(SystemExit):
        parse_args(["recompute", "--snapshot-date", "2025/01/15"])


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


def test_publish_command():
    ns = parse_args(["publish", "--zip-bundle", "/tmp/zip_bundle.json"])
    assert ns.command == "publish"
    assert ns.snapshot_date is None
    assert ns.out_dir is None
    assert ns.zip_bundle == "/tmp/zip_bundle.json"


def test_publish_snapshot_date_parsed():
    ns = parse_args(
        ["publish", "--snapshot-date", "2025-06-30", "--zip-bundle", "/tmp/zip_bundle.json"]
    )
    assert ns.snapshot_date == datetime.date(2025, 6, 30)


def test_publish_out_dir():
    ns = parse_args(["publish", "--out-dir", "/tmp/snap", "--zip-bundle", "/tmp/zip_bundle.json"])
    assert ns.out_dir == "/tmp/snap"


def test_publish_all_args():
    ns = parse_args(
        [
            "publish",
            "--snapshot-date",
            "2025-06-30",
            "--out-dir",
            "/tmp/snap",
            "--zip-bundle",
            "/tmp/zip_bundle.json",
        ]
    )
    assert ns.snapshot_date == datetime.date(2025, 6, 30)
    assert ns.out_dir == "/tmp/snap"
    assert ns.zip_bundle == "/tmp/zip_bundle.json"


def test_publish_missing_zip_bundle_exits() -> None:
    with pytest.raises(SystemExit):
        parse_args(["publish"])


# ---------------------------------------------------------------------------
# process-disclosures
# ---------------------------------------------------------------------------


def test_process_disclosures_command():
    ns = parse_args(["process-disclosures"])
    assert ns.command == "process-disclosures"
    assert ns.chamber == "both"
    assert ns.local_root is None
    assert ns.limit is None


def test_process_disclosures_chamber_house():
    ns = parse_args(["process-disclosures", "--chamber", "house"])
    assert ns.chamber == "house"


def test_process_disclosures_chamber_senate():
    ns = parse_args(["process-disclosures", "--chamber", "senate"])
    assert ns.chamber == "senate"


def test_process_disclosures_invalid_chamber_exits():
    with pytest.raises(SystemExit):
        parse_args(["process-disclosures", "--chamber", "invalid"])


def test_process_disclosures_limit():
    ns = parse_args(["process-disclosures", "--limit", "30"])
    assert ns.limit == 30


def test_process_disclosures_limit_invalid_exits():
    with pytest.raises(SystemExit):
        parse_args(["process-disclosures", "--limit", "notanumber"])


def test_process_disclosures_local_root():
    ns = parse_args(["process-disclosures", "--local-root", "/data/artifacts"])
    assert ns.local_root == "/data/artifacts"


def test_process_disclosures_all_args():
    ns = parse_args(
        [
            "process-disclosures",
            "--chamber",
            "house",
            "--local-root",
            "/data",
            "--limit",
            "10",
        ]
    )
    assert ns.chamber == "house"
    assert ns.local_root == "/data"
    assert ns.limit == 10


# ---------------------------------------------------------------------------
# load-congress-local
# ---------------------------------------------------------------------------


def test_load_congress_local_command():
    ns = parse_args(
        ["load-congress-local", "--archive", "/data/congress_119.json", "--congress", "119"]
    )
    assert ns.command == "load-congress-local"
    assert ns.archive == "/data/congress_119.json"
    assert ns.congress == 119


def test_load_congress_local_missing_archive_exits():
    with pytest.raises(SystemExit):
        parse_args(["load-congress-local", "--congress", "119"])


def test_load_congress_local_missing_congress_exits():
    with pytest.raises(SystemExit):
        parse_args(["load-congress-local", "--archive", "/data/congress_119.json"])


def test_load_congress_local_invalid_congress_exits():
    with pytest.raises(SystemExit):
        parse_args(["load-congress-local", "--archive", "/data/b.json", "--congress", "notanumber"])


# ---------------------------------------------------------------------------
# process-disclosures-local
# ---------------------------------------------------------------------------


def test_process_disclosures_local_command():
    ns = parse_args(["process-disclosures-local", "--bundle", "/data/disclosures.zip"])
    assert ns.command == "process-disclosures-local"
    assert ns.bundle == "/data/disclosures.zip"


def test_process_disclosures_local_missing_bundle_exits():
    with pytest.raises(SystemExit):
        parse_args(["process-disclosures-local"])


def test_process_disclosures_local_rejects_dead_chamber_flag():
    with pytest.raises(SystemExit):
        parse_args(["process-disclosures-local", "--bundle", "/data/d.zip", "--chamber", "house"])


def test_process_disclosures_local_rejects_dead_limit_flag():
    with pytest.raises(SystemExit):
        parse_args(["process-disclosures-local", "--bundle", "/data/d.zip", "--limit", "50"])


def test_process_disclosures_local_all_args():
    ns = parse_args(["process-disclosures-local", "--bundle", "/data/disclosures.zip"])
    assert ns.bundle == "/data/disclosures.zip"


# ---------------------------------------------------------------------------
# run-oracle-local
# ---------------------------------------------------------------------------


def test_run_oracle_local_command():
    ns = parse_args(
        [
            "run-oracle-local",
            "--congress-archive",
            "/data/congress_119.json",
            "--disclosures-bundle",
            "/data/disclosures.zip",
            "--snapshot-date",
            "2025-03-01",
            "--target-dir",
            "/out/snap",
        ]
    )
    assert ns.command == "run-oracle-local"
    assert ns.congress_archive == "/data/congress_119.json"
    assert ns.disclosures_bundle == "/data/disclosures.zip"
    assert ns.snapshot_date == datetime.date(2025, 3, 1)
    assert ns.target_dir == "/out/snap"
    assert ns.chamber == "both"
    assert ns.limit is None
    assert ns.snapshot_id is None


def test_run_oracle_local_explicit_congress_and_artifact_root() -> None:
    ns = parse_args(
        [
            "run-oracle-local",
            "--congress-archive",
            "/data/congress_118.json",
            "--disclosures-bundle",
            "/data/disclosures.zip",
            "--congress",
            "118",
            "--snapshot-date",
            "2025-03-01",
            "--target-dir",
            "/out/snap",
            "--artifact-root",
            "/data/artifacts",
        ]
    )
    assert ns.congress == 118
    assert ns.artifact_root == "/data/artifacts"


def test_run_oracle_local_missing_congress_archive_exits():
    with pytest.raises(SystemExit):
        parse_args(
            [
                "run-oracle-local",
                "--disclosures-bundle",
                "/data/disclosures.zip",
                "--snapshot-date",
                "2025-03-01",
                "--target-dir",
                "/out/snap",
            ]
        )


def test_run_oracle_local_missing_disclosures_bundle_exits():
    with pytest.raises(SystemExit):
        parse_args(
            [
                "run-oracle-local",
                "--congress-archive",
                "/data/congress_119.json",
                "--snapshot-date",
                "2025-03-01",
                "--target-dir",
                "/out/snap",
            ]
        )


def test_run_oracle_local_missing_snapshot_date_exits():
    with pytest.raises(SystemExit):
        parse_args(
            [
                "run-oracle-local",
                "--congress-archive",
                "/data/congress_119.json",
                "--disclosures-bundle",
                "/data/disclosures.zip",
                "--target-dir",
                "/out/snap",
            ]
        )


def test_run_oracle_local_missing_target_dir_exits():
    with pytest.raises(SystemExit):
        parse_args(
            [
                "run-oracle-local",
                "--congress-archive",
                "/data/congress_119.json",
                "--disclosures-bundle",
                "/data/disclosures.zip",
                "--snapshot-date",
                "2025-03-01",
            ]
        )


def test_run_oracle_local_invalid_date_exits():
    with pytest.raises(SystemExit):
        parse_args(
            [
                "run-oracle-local",
                "--congress-archive",
                "/data/congress_119.json",
                "--disclosures-bundle",
                "/data/disclosures.zip",
                "--snapshot-date",
                "not-a-date",
                "--target-dir",
                "/out/snap",
            ]
        )


def test_run_oracle_local_chamber_house():
    ns = parse_args(
        [
            "run-oracle-local",
            "--congress-archive",
            "/data/congress_119.json",
            "--disclosures-bundle",
            "/data/d.zip",
            "--snapshot-date",
            "2025-03-01",
            "--target-dir",
            "/out",
            "--chamber",
            "house",
        ]
    )
    assert ns.chamber == "house"


def test_run_oracle_local_invalid_chamber_exits():
    with pytest.raises(SystemExit):
        parse_args(
            [
                "run-oracle-local",
                "--congress-archive",
                "/data/congress_119.json",
                "--disclosures-bundle",
                "/data/d.zip",
                "--snapshot-date",
                "2025-03-01",
                "--target-dir",
                "/out",
                "--chamber",
                "invalid",
            ]
        )


def test_readme_command_list_matches_parser_surface() -> None:
    readme = Path(__file__).resolve().parents[2] / "README.md"
    lines = readme.read_text(encoding="utf-8").splitlines()

    start = lines.index("Current commands:") + 2
    commands: list[str] = []
    for line in lines[start:]:
        if not line.startswith("- `"):
            break
        commands.append(line.removeprefix("- `").removesuffix("`"))

    parser = build_parser()
    parser_commands = list(parser._subparsers._group_actions[0].choices.keys())  # type: ignore[attr-defined]

    assert commands == parser_commands


def test_readme_runtime_examples_parse() -> None:
    readme = Path(__file__).resolve().parents[2] / "README.md"
    examples: list[list[str]] = []
    for line in readme.read_text(encoding="utf-8").splitlines():
        if not line.startswith("python3 -m src.runtime.main "):
            continue
        parts = shlex.split(line)
        assert parts[:3] == ["python3", "-m", "src.runtime.main"]
        if any(part.startswith("<") and part.endswith(">") for part in parts[3:]):
            continue
        examples.append(parts[3:])

    assert examples
    for example in examples:
        parse_args(example)


def test_run_oracle_local_limit():
    ns = parse_args(
        [
            "run-oracle-local",
            "--congress-archive",
            "/data/congress_119.json",
            "--disclosures-bundle",
            "/data/d.zip",
            "--snapshot-date",
            "2025-03-01",
            "--target-dir",
            "/out",
            "--limit",
            "10",
        ]
    )
    assert ns.limit == 10


def test_run_oracle_local_snapshot_id():
    ns = parse_args(
        [
            "run-oracle-local",
            "--congress-archive",
            "/data/congress_119.json",
            "--disclosures-bundle",
            "/data/d.zip",
            "--snapshot-date",
            "2025-03-01",
            "--target-dir",
            "/out",
            "--snapshot-id",
            "snap-v1",
        ]
    )
    assert ns.snapshot_id == "snap-v1"


def test_run_oracle_local_all_args():
    ns = parse_args(
        [
            "run-oracle-local",
            "--congress-archive",
            "/data/congress_119.json",
            "--disclosures-bundle",
            "/data/disclosures.zip",
            "--snapshot-date",
            "2025-06-15",
            "--target-dir",
            "/out/snap",
            "--chamber",
            "senate",
            "--limit",
            "100",
            "--snapshot-id",
            "2025-06-15-senate",
        ]
    )
    assert ns.congress_archive == "/data/congress_119.json"
    assert ns.disclosures_bundle == "/data/disclosures.zip"
    assert ns.snapshot_date == datetime.date(2025, 6, 15)
    assert ns.target_dir == "/out/snap"
    assert ns.chamber == "senate"
    assert ns.limit == 100
    assert ns.snapshot_id == "2025-06-15-senate"


def test_plan_history_backfill_command() -> None:
    ns = parse_args(
        [
            "plan-history-backfill",
            "--congress",
            "119",
            "--target-root",
            "/out/history",
            "--start-date",
            "2025-01-03",
            "--end-date",
            "2025-02-01",
        ]
    )
    assert ns.command == "plan-history-backfill"
    assert ns.congress == 119
    assert ns.target_root == "/out/history"
    assert ns.start_date == datetime.date(2025, 1, 3)
    assert ns.end_date == datetime.date(2025, 2, 1)


def test_plan_history_backfill_requires_congress() -> None:
    with pytest.raises(SystemExit):
        parse_args(["plan-history-backfill", "--target-root", "/out/history"])


def test_check_history_backfill_inputs_command() -> None:
    ns = parse_args(
        [
            "check-history-backfill-inputs",
            "--congress-archive",
            "/data/congress_119",
            "--disclosures-bundle",
            "/data/disclosures_bundle.json",
            "--artifact-root",
            "/data/disclosures",
        ]
    )
    assert ns.command == "check-history-backfill-inputs"
    assert ns.congress_archive == "/data/congress_119"
    assert ns.disclosures_bundle == "/data/disclosures_bundle.json"
    assert ns.artifact_root == "/data/disclosures"


def test_check_history_backfill_inputs_requires_required_args() -> None:
    with pytest.raises(SystemExit):
        parse_args(["check-history-backfill-inputs", "--congress-archive", "/data/congress_119"])


def test_materialize_disclosures_bundle_command() -> None:
    ns = parse_args(
        [
            "materialize-disclosures-bundle",
            "--bundle-path",
            "/data/disclosures_bundle.json",
            "--artifact-root",
            "/data/disclosures",
            "--year",
            "2024",
            "--year",
            "2025",
            "--chamber",
            "senate",
        ]
    )
    assert ns.command == "materialize-disclosures-bundle"
    assert ns.bundle_path == "/data/disclosures_bundle.json"
    assert ns.artifact_root == "/data/disclosures"
    assert ns.year == [2024, 2025]
    assert ns.chamber == "senate"


def test_materialize_bill_semantics_command() -> None:
    ns = parse_args(
        [
            "materialize-bill-semantics",
            "--output-root",
            "/tmp/bill-semantics",
            "--model",
            "gpt-5.5",
            "--limit",
            "10",
            "--bill-key",
            "119-hr-1",
            "--missing-from-report",
            "/tmp/prediction-eval-report.json",
            "--feature-cutoff",
            "2024-12-31",
            "--dry-run",
            "--plan-output",
            "/tmp/bill-semantics-plan.json",
            "--summary-output",
            "/tmp/bill-semantics-summary.json",
            "--fail-on-unmatched-targets",
            "--overwrite",
        ]
    )
    assert ns.command == "materialize-bill-semantics"
    assert ns.output_root == "/tmp/bill-semantics"
    assert ns.model == "gpt-5.5"
    assert ns.limit == 10
    assert ns.bill_key == ["119-hr-1"]
    assert ns.missing_from_report == "/tmp/prediction-eval-report.json"
    assert ns.feature_cutoff == datetime.date(2024, 12, 31)
    assert ns.dry_run is True
    assert ns.plan_output == "/tmp/bill-semantics-plan.json"
    assert ns.summary_output == "/tmp/bill-semantics-summary.json"
    assert ns.fail_on_unmatched_targets is True
    assert ns.overwrite is True


def test_verify_bill_semantics_command() -> None:
    ns = parse_args(
        [
            "verify-bill-semantics",
            "--root",
            "/tmp/bill-semantics",
            "--require-model-name",
            "semantic-test-model",
            "--require-source-inputs-sha256",
            "--output",
            "/tmp/bill-semantics-verify.json",
        ]
    )
    assert ns.command == "verify-bill-semantics"
    assert ns.root == "/tmp/bill-semantics"
    assert ns.require_model_name == ["semantic-test-model"]
    assert ns.require_source_inputs_sha256 is True
    assert ns.output == "/tmp/bill-semantics-verify.json"


def test_verify_bill_semantics_plan_command() -> None:
    ns = parse_args(
        [
            "verify-bill-semantics-plan",
            "--plan",
            "/tmp/bill-semantics-plan.json",
            "--fail-on-unmatched-targets",
            "--require-source-report",
            "--require-matched-source-anchors",
            "--output",
            "/tmp/bill-semantics-plan-verify.json",
        ]
    )
    assert ns.command == "verify-bill-semantics-plan"
    assert ns.plan == "/tmp/bill-semantics-plan.json"
    assert ns.fail_on_unmatched_targets is True
    assert ns.require_source_report is True
    assert ns.require_matched_source_anchors is True
    assert ns.output == "/tmp/bill-semantics-plan-verify.json"


def test_write_congress_archive_manifest_command() -> None:
    ns = parse_args(
        [
            "write-congress-archive-manifest",
            "--archive-root",
            "/data/congress_119",
            "--congress",
            "119",
            "--manifest-path",
            "/data/congress_119/manifest.json",
        ]
    )
    assert ns.command == "write-congress-archive-manifest"
    assert ns.archive_root == "/data/congress_119"
    assert ns.congress == 119
    assert ns.manifest_path == "/data/congress_119/manifest.json"


def test_materialize_congress_archive_command() -> None:
    ns = parse_args(
        [
            "materialize-congress-archive",
            "--archive-root",
            "/data/congress_119",
            "--congress",
            "119",
            "--api-key",
            "abc123",
            "--include-votes",
            "--house-vote-year",
            "2025",
            "--senate-session",
            "1",
            "--manifest-path",
            "/data/congress_119/manifest.json",
        ]
    )
    assert ns.command == "materialize-congress-archive"
    assert ns.archive_root == "/data/congress_119"
    assert ns.congress == 119
    assert ns.api_key == "abc123"
    assert ns.include_votes is True
    assert ns.house_vote_year == 2025
    assert ns.senate_session == 1
    assert ns.manifest_path == "/data/congress_119/manifest.json"


def test_materialize_congress_archive_vote_coverage_flags_imply_include_votes() -> None:
    ns = parse_args(
        [
            "materialize-congress-archive",
            "--archive-root",
            "/data/congress_119",
            "--congress",
            "119",
            "--house-vote-year",
            "2025",
        ]
    )
    assert ns.include_votes is True


def test_materialize_history_backfill_inputs_command() -> None:
    ns = parse_args(
        [
            "materialize-history-backfill-inputs",
            "--congress",
            "119",
            "--congress-archive-root",
            "/data/congress_119",
            "--disclosures-bundle-path",
            "/data/disclosures_bundle.json",
            "--disclosures-artifact-root",
            "/data/disclosures",
            "--disclosures-year",
            "2024",
            "--disclosures-year",
            "2025",
            "--api-key",
            "abc123",
            "--include-votes",
            "--house-vote-year",
            "2025",
            "--senate-session",
            "1",
            "--chamber",
            "senate",
            "--limit",
            "25",
            "--reuse-existing-inputs",
        ]
    )
    assert ns.command == "materialize-history-backfill-inputs"
    assert ns.congress == 119
    assert ns.congress_archive_root == "/data/congress_119"
    assert ns.disclosures_bundle_path == "/data/disclosures_bundle.json"
    assert ns.disclosures_artifact_root == "/data/disclosures"
    assert ns.disclosures_year == [2024, 2025]
    assert ns.api_key == "abc123"
    assert ns.include_votes is True
    assert ns.house_vote_year == 2025
    assert ns.senate_session == 1
    assert ns.chamber == "senate"
    assert ns.limit == 25
    assert ns.reuse_existing_inputs is True


def test_materialize_history_backfill_inputs_allows_omitting_disclosures_year_when_reusing() -> (
    None
):
    ns = parse_args(
        [
            "materialize-history-backfill-inputs",
            "--congress",
            "119",
            "--congress-archive-root",
            "/data/congress_119",
            "--disclosures-bundle-path",
            "/data/disclosures_bundle.json",
            "--disclosures-artifact-root",
            "/data/disclosures",
            "--reuse-existing-inputs",
        ]
    )
    assert ns.disclosures_year is None
    assert ns.reuse_existing_inputs is True


def test_materialize_history_backfill_inputs_requires_disclosures_year_without_reuse() -> None:
    with pytest.raises(SystemExit):
        parse_args(
            [
                "materialize-history-backfill-inputs",
                "--congress",
                "119",
                "--congress-archive-root",
                "/data/congress_119",
                "--disclosures-bundle-path",
                "/data/disclosures_bundle.json",
                "--disclosures-artifact-root",
                "/data/disclosures",
            ]
        )


def test_run_history_launch_local_command() -> None:
    ns = parse_args(
        [
            "run-history-launch-local",
            "--congress",
            "119",
            "--congress-archive-root",
            "/data/congress_119",
            "--disclosures-bundle-path",
            "/data/disclosures_bundle.json",
            "--disclosures-artifact-root",
            "/data/disclosures",
            "--disclosures-year",
            "2024",
            "--disclosures-year",
            "2025",
            "--target-root",
            "/out/history",
            "--aggregate-root",
            "/out/history-aggregate",
            "--start-date",
            "2025-01-03",
            "--end-date",
            "2025-02-01",
            "--api-key",
            "abc123",
            "--include-votes",
            "--house-vote-year",
            "2025",
            "--senate-session",
            "1",
            "--chamber",
            "senate",
            "--limit",
            "25",
            "--reuse-existing-inputs",
            "--overwrite",
            "--continue-on-error",
        ]
    )
    assert ns.command == "run-history-launch-local"
    assert ns.congress == 119
    assert ns.congress_archive_root == "/data/congress_119"
    assert ns.disclosures_bundle_path == "/data/disclosures_bundle.json"
    assert ns.disclosures_artifact_root == "/data/disclosures"
    assert ns.disclosures_year == [2024, 2025]
    assert ns.target_root == "/out/history"
    assert ns.aggregate_root == "/out/history-aggregate"
    assert ns.start_date == datetime.date(2025, 1, 3)
    assert ns.end_date == datetime.date(2025, 2, 1)
    assert ns.api_key == "abc123"
    assert ns.include_votes is True
    assert ns.house_vote_year == 2025
    assert ns.senate_session == 1
    assert ns.chamber == "senate"
    assert ns.limit == 25
    assert ns.reuse_existing_inputs is True
    assert ns.overwrite is True
    assert ns.continue_on_error is True


def test_run_history_launch_local_allows_omitting_disclosures_year_when_reusing() -> None:
    ns = parse_args(
        [
            "run-history-launch-local",
            "--congress",
            "119",
            "--congress-archive-root",
            "/data/congress_119",
            "--disclosures-bundle-path",
            "/data/disclosures_bundle.json",
            "--disclosures-artifact-root",
            "/data/disclosures",
            "--target-root",
            "/out/history",
            "--reuse-existing-inputs",
        ]
    )
    assert ns.disclosures_year is None
    assert ns.reuse_existing_inputs is True


def test_run_history_launch_local_requires_disclosures_year_without_reuse() -> None:
    with pytest.raises(SystemExit):
        parse_args(
            [
                "run-history-launch-local",
                "--congress",
                "119",
                "--congress-archive-root",
                "/data/congress_119",
                "--disclosures-bundle-path",
                "/data/disclosures_bundle.json",
                "--disclosures-artifact-root",
                "/data/disclosures",
                "--target-root",
                "/out/history",
            ]
        )


def test_aggregate_history_command() -> None:
    ns = parse_args(
        [
            "aggregate-history",
            "--source-root",
            "/out/history/2025-01-06",
            "--source-root",
            "/out/history/2025-01-13",
            "--target-root",
            "/out/aggregate",
        ]
    )
    assert ns.command == "aggregate-history"
    assert ns.source_root == ["/out/history/2025-01-06", "/out/history/2025-01-13"]
    assert ns.target_root == "/out/aggregate"


def test_aggregate_history_requires_source_root() -> None:
    with pytest.raises(SystemExit):
        parse_args(["aggregate-history", "--target-root", "/out/aggregate"])


def test_run_history_backfill_local_command() -> None:
    ns = parse_args(
        [
            "run-history-backfill-local",
            "--congress-archive",
            "/data/congress_119.json",
            "--disclosures-bundle",
            "/data/disclosures.zip",
            "--congress",
            "119",
            "--target-root",
            "/out/history",
            "--aggregate-root",
            "/out/history-aggregate",
            "--start-date",
            "2025-01-03",
            "--end-date",
            "2025-02-01",
            "--chamber",
            "senate",
            "--limit",
            "25",
            "--artifact-root",
            "/data/artifacts",
            "--overwrite",
            "--continue-on-error",
        ]
    )
    assert ns.command == "run-history-backfill-local"
    assert ns.congress_archive == "/data/congress_119.json"
    assert ns.disclosures_bundle == "/data/disclosures.zip"
    assert ns.congress == 119
    assert ns.target_root == "/out/history"
    assert ns.aggregate_root == "/out/history-aggregate"
    assert ns.start_date == datetime.date(2025, 1, 3)
    assert ns.end_date == datetime.date(2025, 2, 1)
    assert ns.chamber == "senate"
    assert ns.limit == 25
    assert ns.artifact_root == "/data/artifacts"
    assert ns.overwrite is True
    assert ns.continue_on_error is True


def test_run_history_backfill_local_requires_required_args() -> None:
    with pytest.raises(SystemExit):
        parse_args(["run-history-backfill-local", "--congress", "119"])


# ---------------------------------------------------------------------------
# verify-publish
# ---------------------------------------------------------------------------


def test_verify_publish_command():
    ns = parse_args(["verify-publish", "--publish-root", "/out/snap"])
    assert ns.command == "verify-publish"
    assert ns.publish_root == "/out/snap"


def test_verify_publish_missing_publish_root_exits():
    with pytest.raises(SystemExit):
        parse_args(["verify-publish"])


def test_verify_publish_absolute_path():
    ns = parse_args(["verify-publish", "--publish-root", "/var/data/snapshots/2025-06-15"])
    assert ns.publish_root == "/var/data/snapshots/2025-06-15"


def test_verify_publish_relative_path():
    ns = parse_args(["verify-publish", "--publish-root", "out/snap"])
    assert ns.publish_root == "out/snap"


# ---------------------------------------------------------------------------
# verify-publish-roundtrip
# ---------------------------------------------------------------------------


def test_verify_publish_roundtrip_command():
    ns = parse_args(["verify-publish-roundtrip", "--publish-root", "/out/snap"])
    assert ns.command == "verify-publish-roundtrip"
    assert ns.publish_root == "/out/snap"


def test_verify_publish_roundtrip_missing_publish_root_exits():
    with pytest.raises(SystemExit):
        parse_args(["verify-publish-roundtrip"])


def test_verify_publish_roundtrip_absolute_path():
    ns = parse_args(
        ["verify-publish-roundtrip", "--publish-root", "/var/data/snapshots/2025-06-15"]
    )
    assert ns.publish_root == "/var/data/snapshots/2025-06-15"


def test_verify_publish_roundtrip_relative_path():
    ns = parse_args(["verify-publish-roundtrip", "--publish-root", "out/snap"])
    assert ns.publish_root == "out/snap"


# ---------------------------------------------------------------------------
# verify-history-aggregate
# ---------------------------------------------------------------------------


def test_verify_history_aggregate_command():
    ns = parse_args(["verify-history-aggregate", "--publish-root", "/out/history"])
    assert ns.command == "verify-history-aggregate"
    assert ns.publish_root == "/out/history"


def test_verify_history_aggregate_missing_publish_root_exits():
    with pytest.raises(SystemExit):
        parse_args(["verify-history-aggregate"])


# ---------------------------------------------------------------------------
# prediction-backtest
# ---------------------------------------------------------------------------


def test_prediction_backtest_defaults_to_2024_cutoff_for_2025_2026_labels():
    ns = parse_args(["prediction-backtest"])
    assert ns.command == "prediction-backtest"
    assert ns.model == "baseline"
    assert ns.feature_cutoff == datetime.date(2024, 12, 31)
    assert ns.label_start == datetime.date(2025, 1, 1)
    assert ns.label_end == datetime.date(2026, 12, 31)
    assert ns.output is None


def test_prediction_backtest_accepts_window_and_output():
    ns = parse_args(
        [
            "prediction-backtest",
            "--feature-cutoff",
            "2023-12-31",
            "--label-start",
            "2024-01-01",
            "--label-end",
            "2024-12-31",
            "--congress-archive-manifest",
            "/tmp/congress-manifest.json",
            "--output",
            "/tmp/backtest.json",
        ]
    )
    assert ns.command == "prediction-backtest"
    assert ns.feature_cutoff == datetime.date(2023, 12, 31)
    assert ns.label_start == datetime.date(2024, 1, 1)
    assert ns.label_end == datetime.date(2024, 12, 31)
    assert ns.congress_archive_manifest == "/tmp/congress-manifest.json"
    assert ns.output == "/tmp/backtest.json"


def test_prediction_backtest_accepts_ontology_model():
    ns = parse_args(["prediction-backtest", "--model", "ontology"])
    assert ns.model == "ontology"


def test_prediction_backtest_accepts_bill_semantics_root():
    ns = parse_args(
        [
            "prediction-backtest",
            "--model",
            "ontology",
            "--bill-semantics-root",
            "/tmp/bill-semantics",
            "--fail-on-mixed-bill-semantics-models",
            "--fail-on-missing-bill-semantics-root",
        ]
    )
    assert ns.bill_semantics_root == "/tmp/bill-semantics"
    assert ns.fail_on_mixed_bill_semantics_models is True
    assert ns.fail_on_missing_bill_semantics_root is True


# ---------------------------------------------------------------------------
# prediction-eval-report
# ---------------------------------------------------------------------------


def test_prediction_eval_report_defaults_to_past_training_and_current_eval_windows():
    ns = parse_args(["prediction-eval-report"])
    assert ns.command == "prediction-eval-report"
    assert ns.training_feature_cutoff == datetime.date(2022, 12, 31)
    assert ns.train_start == datetime.date(2023, 1, 1)
    assert ns.train_end == datetime.date(2024, 12, 31)
    assert ns.feature_cutoff == datetime.date(2024, 12, 31)
    assert ns.label_start == datetime.date(2025, 1, 1)
    assert ns.label_end == datetime.date(2026, 12, 31)
    assert ns.output is None


def test_prediction_eval_report_accepts_windows_output_and_semantics_root():
    ns = parse_args(
        [
            "prediction-eval-report",
            "--training-feature-cutoff",
            "2021-12-31",
            "--train-start",
            "2022-01-01",
            "--train-end",
            "2023-12-31",
            "--feature-cutoff",
            "2023-12-31",
            "--label-start",
            "2024-01-01",
            "--label-end",
            "2024-12-31",
            "--bill-semantics-root",
            "/tmp/bill-semantics",
            "--congress-archive-manifest",
            "/tmp/congress-manifest.json",
            "--output",
            "/tmp/eval-report.json",
            "--dataset-output",
            "/tmp/eval-dataset.json",
            "--manifest-output",
            "/tmp/eval-manifest.json",
            "--strict-readiness",
            "--min-training-examples",
            "1000",
            "--min-evaluation-examples",
            "500",
            "--min-bill-semantic-coverage-rate",
            "0.95",
            "--min-bill-metadata-coverage-rate",
            "0.9",
            "--min-training-bill-semantic-coverage-rate",
            "0.8",
            "--min-evaluation-bill-semantic-coverage-rate",
            "0.85",
            "--min-training-feature-source-coverage-rate",
            "0.75",
            "--min-evaluation-feature-source-coverage-rate",
            "0.7",
            "--min-training-feature-source-url-coverage-rate",
            "0.65",
            "--min-training-feature-official-source-coverage-rate",
            "0.55",
            "--min-evaluation-feature-source-url-coverage-rate",
            "0.6",
            "--min-evaluation-feature-official-source-coverage-rate",
            "0.5",
            "--min-evaluation-source-url-coverage-rate",
            "0.9",
            "--fail-on-unknown-bill-semantic-availability",
            "--fail-on-unknown-bill-signal-availability",
            "--fail-on-unknown-ontology-edge-availability",
            "--fail-on-unknown-contribution-signal-availability",
            "--fail-on-unknown-statement-signal-availability",
            "--fail-on-mixed-bill-semantics-models",
        ]
    )
    assert ns.command == "prediction-eval-report"
    assert ns.training_feature_cutoff == datetime.date(2021, 12, 31)
    assert ns.train_start == datetime.date(2022, 1, 1)
    assert ns.train_end == datetime.date(2023, 12, 31)
    assert ns.feature_cutoff == datetime.date(2023, 12, 31)
    assert ns.label_start == datetime.date(2024, 1, 1)
    assert ns.label_end == datetime.date(2024, 12, 31)
    assert ns.bill_semantics_root == "/tmp/bill-semantics"
    assert ns.congress_archive_manifest == "/tmp/congress-manifest.json"
    assert ns.output == "/tmp/eval-report.json"
    assert ns.dataset_output == "/tmp/eval-dataset.json"
    assert ns.manifest_output == "/tmp/eval-manifest.json"
    assert ns.strict_readiness is True
    assert ns.min_training_examples == 1000
    assert ns.min_evaluation_examples == 500
    assert ns.min_bill_semantic_coverage_rate == 0.95
    assert ns.min_bill_metadata_coverage_rate == 0.9
    assert ns.min_training_bill_semantic_coverage_rate == 0.8
    assert ns.min_evaluation_bill_semantic_coverage_rate == 0.85
    assert ns.min_training_feature_source_coverage_rate == 0.75
    assert ns.min_evaluation_feature_source_coverage_rate == 0.7
    assert ns.min_training_feature_source_url_coverage_rate == 0.65
    assert ns.min_training_feature_official_source_coverage_rate == 0.55
    assert ns.min_evaluation_feature_source_url_coverage_rate == 0.6
    assert ns.min_evaluation_feature_official_source_coverage_rate == 0.5
    assert ns.min_evaluation_source_url_coverage_rate == 0.9
    assert ns.fail_on_unknown_bill_semantic_availability is True
    assert ns.fail_on_unknown_bill_signal_availability is True
    assert ns.fail_on_unknown_ontology_edge_availability is True
    assert ns.fail_on_unknown_contribution_signal_availability is True
    assert ns.fail_on_unknown_statement_signal_availability is True
    assert ns.fail_on_mixed_bill_semantics_models is True


def test_prediction_eval_window_plan_defaults_to_annual_windows():
    ns = parse_args(["prediction-eval-window-plan"])
    assert ns.command == "prediction-eval-window-plan"
    assert ns.start_label_year == 2024
    assert ns.end_label_year == 2026
    assert ns.train_years == 2
    assert ns.label_years == 1
    assert ns.max_feature_cutoff is None
    assert ns.output_dir == "out"
    assert ns.bill_semantics_root is None
    assert ns.congress_archive_manifest is None
    assert ns.output is None


def test_prediction_eval_window_plan_accepts_window_controls_and_outputs():
    ns = parse_args(
        [
            "prediction-eval-window-plan",
            "--start-label-year",
            "2025",
            "--end-label-year",
            "2026",
            "--train-years",
            "3",
            "--label-years",
            "2",
            "--max-feature-cutoff",
            "2024-12-31",
            "--output-dir",
            "/tmp/prediction",
            "--bill-semantics-root",
            "/tmp/bill-semantics",
            "--congress-archive-manifest",
            "/tmp/congress-manifest.json",
            "--output",
            "/tmp/window-plan.json",
        ]
    )
    assert ns.command == "prediction-eval-window-plan"
    assert ns.start_label_year == 2025
    assert ns.end_label_year == 2026
    assert ns.train_years == 3
    assert ns.label_years == 2
    assert ns.max_feature_cutoff == datetime.date(2024, 12, 31)
    assert ns.output_dir == "/tmp/prediction"
    assert ns.bill_semantics_root == "/tmp/bill-semantics"
    assert ns.congress_archive_manifest == "/tmp/congress-manifest.json"
    assert ns.output == "/tmp/window-plan.json"


def test_verify_prediction_eval_window_plan_accepts_artifact_and_gates():
    ns = parse_args(
        [
            "verify-prediction-eval-window-plan",
            "--artifact",
            "/tmp/window-plan.json",
            "--require-run-metadata",
            "--require-commands",
            "--min-window-count",
            "2",
            "--output",
            "/tmp/window-plan-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-eval-window-plan"
    assert ns.artifact == "/tmp/window-plan.json"
    assert ns.require_run_metadata is True
    assert ns.require_commands is True
    assert ns.min_window_count == 2
    assert ns.output == "/tmp/window-plan-verify.json"


def test_prediction_eval_window_summary_accepts_reports_and_gates():
    ns = parse_args(
        [
            "prediction-eval-window-summary",
            "--report",
            "/tmp/eval-2024.json",
            "--report",
            "/tmp/eval-2025.json",
            "--require-report-run-metadata",
            "--require-ready-reports",
            "--require-non-overlapping-label-windows",
            "--output",
            "/tmp/eval-window-summary.json",
        ]
    )

    assert ns.command == "prediction-eval-window-summary"
    assert ns.report == ["/tmp/eval-2024.json", "/tmp/eval-2025.json"]
    assert ns.require_report_run_metadata is True
    assert ns.require_ready_reports is True
    assert ns.require_non_overlapping_label_windows is True
    assert ns.output == "/tmp/eval-window-summary.json"


def test_verify_prediction_eval_window_summary_accepts_artifact_and_gates():
    ns = parse_args(
        [
            "verify-prediction-eval-window-summary",
            "--artifact",
            "/tmp/eval-window-summary.json",
            "--plan",
            "/tmp/eval-window-plan.json",
            "--require-plan-match",
            "--require-run-metadata",
            "--require-current-report-hashes",
            "--require-non-overlapping-label-windows",
            "--min-window-count",
            "2",
            "--min-total-evaluation-labels",
            "100",
            "--output",
            "/tmp/eval-window-summary-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-eval-window-summary"
    assert ns.artifact == "/tmp/eval-window-summary.json"
    assert ns.plan == "/tmp/eval-window-plan.json"
    assert ns.require_plan_match is True
    assert ns.require_run_metadata is True
    assert ns.require_current_report_hashes is True
    assert ns.require_non_overlapping_label_windows is True
    assert ns.min_window_count == 2
    assert ns.min_total_evaluation_labels == 100
    assert ns.output == "/tmp/eval-window-summary-verify.json"


def test_verify_prediction_eval_window_run_accepts_plan_and_gates():
    ns = parse_args(
        [
            "verify-prediction-eval-window-run",
            "--plan",
            "/tmp/eval-window-plan.json",
            "--summary-verify",
            "/tmp/eval-window-summary-verify.json",
            "--require-window-verifiers",
            "--require-summary-verify",
            "--output",
            "/tmp/eval-window-run-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-eval-window-run"
    assert ns.plan == "/tmp/eval-window-plan.json"
    assert ns.summary_verify == "/tmp/eval-window-summary-verify.json"
    assert ns.require_window_verifiers is True
    assert ns.require_summary_verify is True
    assert ns.output == "/tmp/eval-window-run-verify.json"


def test_verify_prediction_eval_manifest_accepts_manifest_path():
    ns = parse_args(
        [
            "verify-prediction-eval-manifest",
            "--manifest",
            "/tmp/eval-manifest.json",
            "--require-artifact-run-metadata",
            "--require-congress-archive-manifest",
            "--require-ready-quality",
            "--require-failure-analysis",
            "--require-backfill-recommendations",
            "--require-fail-on-unknown-bill-semantic-availability",
            "--require-fail-on-unknown-bill-signal-availability",
            "--require-fail-on-unknown-ontology-edge-availability",
            "--require-fail-on-unknown-contribution-signal-availability",
            "--require-fail-on-unknown-statement-signal-availability",
            "--require-bill-semantics-cache",
            "--require-bill-semantics-model-name",
            "gpt-5.5",
            "--require-bill-semantics-source-inputs-sha256",
            "--require-model-name",
            "ontology_signal_model",
            "--require-ontology-feature-signals",
            "--require-source-family",
            "congress_vote",
            "--require-source-family",
            "legislative_vote",
            "--min-bill-semantic-coverage-rate",
            "0.95",
            "--min-bill-metadata-coverage-rate",
            "0.9",
            "--min-training-feature-source-coverage-rate",
            "0.85",
            "--min-evaluation-feature-source-coverage-rate",
            "0.8",
            "--min-training-feature-source-url-coverage-rate",
            "0.75",
            "--min-training-feature-official-source-coverage-rate",
            "0.7",
            "--min-evaluation-feature-source-url-coverage-rate",
            "0.7",
            "--min-evaluation-feature-official-source-coverage-rate",
            "0.65",
            "--min-evaluation-source-url-coverage-rate",
            "0.65",
            "--output",
            "/tmp/eval-manifest-verify.json",
        ]
    )
    assert ns.command == "verify-prediction-eval-manifest"
    assert ns.manifest == "/tmp/eval-manifest.json"
    assert ns.require_artifact_run_metadata is True
    assert ns.require_congress_archive_manifest is True
    assert ns.require_ready_quality is True
    assert ns.require_failure_analysis is True
    assert ns.require_backfill_recommendations is True
    assert ns.require_fail_on_unknown_bill_semantic_availability is True
    assert ns.require_fail_on_unknown_bill_signal_availability is True
    assert ns.require_fail_on_unknown_ontology_edge_availability is True
    assert ns.require_fail_on_unknown_contribution_signal_availability is True
    assert ns.require_fail_on_unknown_statement_signal_availability is True
    assert ns.require_bill_semantics_cache is True
    assert ns.require_bill_semantics_model_name == ["gpt-5.5"]
    assert ns.require_bill_semantics_source_inputs_sha256 is True
    assert ns.require_model_name == ["ontology_signal_model"]
    assert ns.require_ontology_feature_signals is True
    assert ns.require_source_families == ["congress_vote", "legislative_vote"]
    assert ns.min_bill_semantic_coverage_rate == 0.95
    assert ns.min_bill_metadata_coverage_rate == 0.9
    assert ns.min_training_feature_source_coverage_rate == 0.85
    assert ns.min_evaluation_feature_source_coverage_rate == 0.8
    assert ns.min_training_feature_source_url_coverage_rate == 0.75
    assert ns.min_training_feature_official_source_coverage_rate == 0.7
    assert ns.min_evaluation_feature_source_url_coverage_rate == 0.7
    assert ns.min_evaluation_feature_official_source_coverage_rate == 0.65
    assert ns.min_evaluation_source_url_coverage_rate == 0.65
    assert ns.output == "/tmp/eval-manifest-verify.json"


def test_verify_prediction_backtest_accepts_artifact_path():
    ns = parse_args(
        [
            "verify-prediction-backtest",
            "--artifact",
            "/tmp/backtest.json",
            "--require-run-metadata",
            "--require-evaluated-predictions",
            "--require-prediction-source-urls",
            "--require-official-prediction-source-urls",
            "--require-congress-archive-manifest",
            "--require-bill-semantics-cache",
            "--require-bill-semantics-model-name",
            "gpt-5.5",
            "--require-bill-semantics-source-inputs-sha256",
            "--require-model-name",
            "ontology_signal_model",
            "--require-ontology-feature-signals",
            "--require-source-family",
            "congress_vote",
            "--require-source-family",
            "legislative_vote",
            "--output",
            "/tmp/backtest-verify.json",
        ]
    )
    assert ns.command == "verify-prediction-backtest"
    assert ns.artifact == "/tmp/backtest.json"
    assert ns.require_run_metadata is True
    assert ns.require_evaluated_predictions is True
    assert ns.require_prediction_source_urls is True
    assert ns.require_official_prediction_source_urls is True
    assert ns.require_congress_archive_manifest is True
    assert ns.require_bill_semantics_cache is True
    assert ns.require_bill_semantics_model_name == ["gpt-5.5"]
    assert ns.require_bill_semantics_source_inputs_sha256 is True
    assert ns.require_model_name == "ontology_signal_model"
    assert ns.require_ontology_feature_signals is True
    assert ns.require_source_families == ["congress_vote", "legislative_vote"]
    assert ns.output == "/tmp/backtest-verify.json"


def test_verify_prediction_benchmark_accepts_artifact_paths():
    ns = parse_args(
        [
            "verify-prediction-benchmark",
            "--inventory",
            "/tmp/inventory.json",
            "--backtest",
            "/tmp/backtest.json",
            "--eval-manifest",
            "/tmp/eval-manifest.json",
            "--bill-semantics-plan",
            "/tmp/bill-semantics-plan.json",
            "--source-url-audit",
            "/tmp/source-url-audit.json",
            "--eval-window-run-verify",
            "/tmp/eval-window-run-verify.json",
            "--require-eval-window-run-verify",
            "--require-source-url-audit",
            "--require-source-url-audit-no-gaps",
            "--require-source-url-audit-no-official-source-gaps",
            "--require-source-url-audit-no-portable-context-gaps",
            "--bill-semantics-root",
            "/tmp/bill-semantics",
            "--fail-on-unmatched-targets",
            "--require-bill-semantics-plan-source-anchors",
            "--require-bill-semantics-cache",
            "--require-bill-semantics-model-name",
            "semantic-test-model",
            "--require-bill-semantics-source-inputs-sha256",
            "--require-clean-inventory",
            "--require-inventory-congress-archive-manifest",
            "--require-portable-jurisdiction-ids",
            "--require-portable-body-ids",
            "--require-portable-session-ids",
            "--require-source-family",
            "congress_vote",
            "--require-source-family",
            "congress_bill",
            "--require-evaluated-backtest",
            "--require-backtest-source-urls",
            "--require-backtest-official-source-urls",
            "--require-backtest-congress-archive-manifest",
            "--require-backtest-model-name",
            "ontology_signal_model",
            "--require-backtest-ontology-feature-signals",
            "--require-backtest-source-family",
            "congress_vote",
            "--require-backtest-source-family",
            "legislative_vote",
            "--require-ready-quality",
            "--require-eval-failure-analysis",
            "--require-eval-backfill-recommendations",
            "--require-eval-congress-archive-manifest",
            "--require-eval-fail-on-unknown-bill-semantic-availability",
            "--require-eval-fail-on-unknown-bill-signal-availability",
            "--require-eval-fail-on-unknown-ontology-edge-availability",
            "--require-eval-fail-on-unknown-contribution-signal-availability",
            "--require-eval-fail-on-unknown-statement-signal-availability",
            "--require-eval-model-name",
            "ontology_signal_model",
            "--require-eval-model-name",
            "learned_signal_logistic",
            "--require-eval-ontology-feature-signals",
            "--require-eval-source-family",
            "congress_vote",
            "--require-eval-source-family",
            "legislative_vote",
            "--min-bill-semantic-coverage-rate",
            "0.95",
            "--min-bill-metadata-coverage-rate",
            "0.9",
            "--min-training-feature-source-coverage-rate",
            "0.85",
            "--min-evaluation-feature-source-coverage-rate",
            "0.8",
            "--min-training-feature-source-url-coverage-rate",
            "0.75",
            "--min-training-feature-official-source-coverage-rate",
            "0.7",
            "--min-evaluation-feature-source-url-coverage-rate",
            "0.7",
            "--min-evaluation-feature-official-source-coverage-rate",
            "0.65",
            "--min-evaluation-source-url-coverage-rate",
            "0.65",
            "--min-training-labels",
            "1000",
            "--min-evaluation-labels",
            "500",
            "--min-training-feature-vote-history-source-coverage-rate",
            "0.93",
            "--min-evaluation-feature-vote-history-source-coverage-rate",
            "0.92",
            "--min-training-label-source-url-coverage-rate",
            "0.98",
            "--min-evaluation-label-source-url-coverage-rate",
            "0.97",
            "--min-training-label-official-source-url-coverage-rate",
            "0.96",
            "--min-evaluation-label-official-source-url-coverage-rate",
            "0.95",
            "--min-bill-source-url-coverage-rate",
            "0.95",
            "--min-bill-official-source-url-coverage-rate",
            "0.94",
            "--min-bill-sponsor-availability-rate",
            "0.93",
            "--require-no-bill-sponsor-introduced-date-fallbacks",
            "--min-ontology-source-anchor-coverage-rate",
            "0.9",
            "--min-ontology-official-source-anchor-coverage-rate",
            "0.88",
            "--min-fec-member-attribution-rate",
            "0.85",
            "--min-fec-contributions",
            "100",
            "--min-member-attributed-fec-contributions",
            "90",
            "--min-members-with-fec-candidate-id",
            "25",
            "--min-public-statement-signals",
            "75",
            "--min-members-with-public-statement-signals",
            "20",
            "--backfill-plan-output",
            "/tmp/backfill-plan.json",
            "--check-backfill-runtime-requirements",
            "--output",
            "/tmp/benchmark-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-benchmark"
    assert ns.inventory == "/tmp/inventory.json"
    assert ns.backtest == "/tmp/backtest.json"
    assert ns.eval_manifest == "/tmp/eval-manifest.json"
    assert ns.bill_semantics_plan == "/tmp/bill-semantics-plan.json"
    assert ns.source_url_audit == "/tmp/source-url-audit.json"
    assert ns.eval_window_run_verify == "/tmp/eval-window-run-verify.json"
    assert ns.require_eval_window_run_verify is True
    assert ns.require_source_url_audit is True
    assert ns.require_source_url_audit_no_gaps is True
    assert ns.require_source_url_audit_no_official_source_gaps is True
    assert ns.require_source_url_audit_no_portable_context_gaps is True
    assert ns.bill_semantics_root == "/tmp/bill-semantics"
    assert ns.fail_on_unmatched_targets is True
    assert ns.require_bill_semantics_plan_source_anchors is True
    assert ns.require_bill_semantics_cache is True
    assert ns.require_bill_semantics_model_name == ["semantic-test-model"]
    assert ns.require_bill_semantics_source_inputs_sha256 is True
    assert ns.require_clean_inventory is True
    assert ns.require_inventory_congress_archive_manifest is True
    assert ns.require_portable_jurisdiction_ids is True
    assert ns.require_portable_body_ids is True
    assert ns.require_portable_session_ids is True
    assert ns.require_source_families == ["congress_vote", "congress_bill"]
    assert ns.require_evaluated_backtest is True
    assert ns.require_backtest_source_urls is True
    assert ns.require_backtest_official_source_urls is True
    assert ns.require_backtest_congress_archive_manifest is True
    assert ns.require_backtest_model_name == "ontology_signal_model"
    assert ns.require_backtest_ontology_feature_signals is True
    assert ns.require_backtest_source_families == ["congress_vote", "legislative_vote"]
    assert ns.require_ready_quality is True
    assert ns.require_eval_failure_analysis is True
    assert ns.require_eval_backfill_recommendations is True
    assert ns.require_eval_congress_archive_manifest is True
    assert ns.require_eval_fail_on_unknown_bill_semantic_availability is True
    assert ns.require_eval_fail_on_unknown_bill_signal_availability is True
    assert ns.require_eval_fail_on_unknown_ontology_edge_availability is True
    assert ns.require_eval_fail_on_unknown_contribution_signal_availability is True
    assert ns.require_eval_fail_on_unknown_statement_signal_availability is True
    assert ns.require_eval_model_name == [
        "ontology_signal_model",
        "learned_signal_logistic",
    ]
    assert ns.require_eval_ontology_feature_signals is True
    assert ns.require_eval_source_families == ["congress_vote", "legislative_vote"]
    assert ns.min_bill_semantic_coverage_rate == 0.95
    assert ns.min_bill_metadata_coverage_rate == 0.9
    assert ns.min_training_feature_source_coverage_rate == 0.85
    assert ns.min_evaluation_feature_source_coverage_rate == 0.8
    assert ns.min_training_feature_source_url_coverage_rate == 0.75
    assert ns.min_training_feature_official_source_coverage_rate == 0.7
    assert ns.min_evaluation_feature_source_url_coverage_rate == 0.7
    assert ns.min_evaluation_feature_official_source_coverage_rate == 0.65
    assert ns.min_evaluation_source_url_coverage_rate == 0.65
    assert ns.min_training_labels == 1000
    assert ns.min_evaluation_labels == 500
    assert ns.min_training_feature_vote_history_source_coverage_rate == 0.93
    assert ns.min_evaluation_feature_vote_history_source_coverage_rate == 0.92
    assert ns.min_training_label_source_url_coverage_rate == 0.98
    assert ns.min_evaluation_label_source_url_coverage_rate == 0.97
    assert ns.min_training_label_official_source_url_coverage_rate == 0.96
    assert ns.min_evaluation_label_official_source_url_coverage_rate == 0.95
    assert ns.min_bill_source_url_coverage_rate == 0.95
    assert ns.min_bill_official_source_url_coverage_rate == 0.94
    assert ns.min_bill_sponsor_availability_rate == 0.93
    assert ns.require_no_bill_sponsor_introduced_date_fallbacks is True
    assert ns.min_ontology_source_anchor_coverage_rate == 0.9
    assert ns.min_ontology_official_source_anchor_coverage_rate == 0.88
    assert ns.min_fec_member_attribution_rate == 0.85
    assert ns.min_fec_contributions == 100
    assert ns.min_member_attributed_fec_contributions == 90
    assert ns.min_members_with_fec_candidate_id == 25
    assert ns.min_public_statement_signals == 75
    assert ns.min_members_with_public_statement_signals == 20
    assert ns.backfill_plan_output == "/tmp/backfill-plan.json"
    assert ns.check_backfill_runtime_requirements is True
    assert ns.output == "/tmp/benchmark-verify.json"


def test_verify_prediction_backfill_plan_accepts_artifact_path():
    ns = parse_args(
        [
            "verify-prediction-backfill-plan",
            "--artifact",
            "/tmp/benchmark-verify.json",
            "--require-source-requirements",
            "--require-runtime-requirements",
            "--check-runtime-requirements",
            "--require-supported-suggested-commands",
            "--require-blocker-links",
            "--require-run-metadata",
            "--output",
            "/tmp/backfill-plan-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-backfill-plan"
    assert ns.artifact == "/tmp/benchmark-verify.json"
    assert ns.require_source_requirements is True
    assert ns.require_runtime_requirements is True
    assert ns.check_runtime_requirements is True
    assert ns.require_supported_suggested_commands is True
    assert ns.require_blocker_links is True
    assert ns.require_run_metadata is True
    assert ns.output == "/tmp/backfill-plan-verify.json"


def test_prediction_offline_readiness_summary_accepts_verifier_artifacts():
    ns = parse_args(
        [
            "prediction-offline-readiness-summary",
            "--benchmark-verify",
            "/tmp/benchmark-verify.json",
            "--backfill-runtime-verify",
            "/tmp/backfill-runtime-verify.json",
            "--backfill-plan",
            "/tmp/backfill-plan.json",
            "--env-preflight",
            "/tmp/env-preflight.json",
            "--env-preflight-verify",
            "/tmp/env-preflight-verify.json",
            "--congress-load-summary",
            "/tmp/load-congress-local.json",
            "--require-congress-load-summary",
            "--fec-inputs-verify",
            "/tmp/fec-inputs-verify.json",
            "--public-statement-rows-verify",
            "/tmp/public-statement-rows-verify.json",
            "--require-eval-window-run",
            "--output",
            "/tmp/offline-readiness.json",
            "--resume-script-output",
            "/tmp/prediction-resume.sh",
        ]
    )

    assert ns.command == "prediction-offline-readiness-summary"
    assert ns.benchmark_verify == "/tmp/benchmark-verify.json"
    assert ns.backfill_runtime_verify == "/tmp/backfill-runtime-verify.json"
    assert ns.backfill_plan == "/tmp/backfill-plan.json"
    assert ns.env_preflight == "/tmp/env-preflight.json"
    assert ns.env_preflight_verify == "/tmp/env-preflight-verify.json"
    assert ns.congress_load_summary == "/tmp/load-congress-local.json"
    assert ns.require_congress_load_summary is True
    assert ns.fec_inputs_verify == "/tmp/fec-inputs-verify.json"
    assert ns.public_statement_rows_verify == "/tmp/public-statement-rows-verify.json"
    assert ns.require_eval_window_run is True
    assert ns.output == "/tmp/offline-readiness.json"
    assert ns.resume_script_output == "/tmp/prediction-resume.sh"


def test_verify_prediction_resume_script_accepts_readiness_summary_and_script():
    ns = parse_args(
        [
            "verify-prediction-resume-script",
            "--readiness-summary",
            "/tmp/offline-readiness.json",
            "--readiness-summary-verify",
            "/tmp/offline-readiness-verify.json",
            "--script",
            "/tmp/prediction-resume.sh",
            "--require-env-guards",
            "--require-no-secret-literals",
            "--require-safe-commands",
            "--output",
            "/tmp/prediction-resume-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-resume-script"
    assert ns.readiness_summary == "/tmp/offline-readiness.json"
    assert ns.readiness_summary_verify == "/tmp/offline-readiness-verify.json"
    assert ns.script == "/tmp/prediction-resume.sh"
    assert ns.require_env_guards is True
    assert ns.require_no_secret_literals is True
    assert ns.require_safe_commands is True
    assert ns.output == "/tmp/prediction-resume-verify.json"


def test_verify_prediction_offline_readiness_summary_accepts_artifact_gates():
    ns = parse_args(
        [
            "verify-prediction-offline-readiness-summary",
            "--artifact",
            "/tmp/offline-readiness.json",
            "--require-run-metadata",
            "--require-env-preflight-verify",
            "--require-eval-window-run",
            "--require-resume-script-match",
            "--require-no-secret-literals",
            "--output",
            "/tmp/offline-readiness-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-offline-readiness-summary"
    assert ns.artifact == "/tmp/offline-readiness.json"
    assert ns.require_run_metadata is True
    assert ns.require_env_preflight_verify is True
    assert ns.require_eval_window_run is True
    assert ns.require_resume_script_match is True
    assert ns.require_no_secret_literals is True
    assert ns.output == "/tmp/offline-readiness-verify.json"


def test_verify_prediction_operator_handoff_accepts_verifier_chain():
    ns = parse_args(
        [
            "verify-prediction-operator-handoff",
            "--env-preflight-verify",
            "/tmp/runtime-env-preflight-verify.json",
            "--readiness-summary-verify",
            "/tmp/offline-readiness-verify.json",
            "--resume-script-verify",
            "/tmp/resume-script-verify.json",
            "--require-no-secret-literals",
            "--output",
            "/tmp/operator-handoff-verify.json",
            "--runbook-output",
            "/tmp/operator-runbook.md",
        ]
    )

    assert ns.command == "verify-prediction-operator-handoff"
    assert ns.env_preflight_verify == "/tmp/runtime-env-preflight-verify.json"
    assert ns.readiness_summary_verify == "/tmp/offline-readiness-verify.json"
    assert ns.resume_script_verify == "/tmp/resume-script-verify.json"
    assert ns.require_no_secret_literals is True
    assert ns.output == "/tmp/operator-handoff-verify.json"
    assert ns.runbook_output == "/tmp/operator-runbook.md"


def test_verify_prediction_operator_runbook_accepts_handoff_and_runbook():
    ns = parse_args(
        [
            "verify-prediction-operator-runbook",
            "--handoff-verify",
            "/tmp/operator-handoff-verify.json",
            "--runbook",
            "/tmp/operator-runbook.md",
            "--require-handoff-runbook-sha",
            "--require-no-secret-literals",
            "--require-verified-artifact-hashes",
            "--output",
            "/tmp/operator-runbook-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-operator-runbook"
    assert ns.handoff_verify == "/tmp/operator-handoff-verify.json"
    assert ns.runbook == "/tmp/operator-runbook.md"
    assert ns.require_handoff_runbook_sha is True
    assert ns.require_no_secret_literals is True
    assert ns.require_verified_artifact_hashes is True
    assert ns.output == "/tmp/operator-runbook-verify.json"


def test_prediction_operator_status_accepts_runbook_verify():
    ns = parse_args(
        [
            "prediction-operator-status",
            "--runbook-verify",
            "/tmp/operator-runbook-verify.json",
            "--require-no-secret-literals",
            "--require-launch-ready",
            "--output",
            "/tmp/operator-status.json",
        ]
    )

    assert ns.command == "prediction-operator-status"
    assert ns.runbook_verify == "/tmp/operator-runbook-verify.json"
    assert ns.require_no_secret_literals is True
    assert ns.require_launch_ready is True
    assert ns.output == "/tmp/operator-status.json"


def test_verify_prediction_operator_status_accepts_status_artifact():
    ns = parse_args(
        [
            "verify-prediction-operator-status",
            "--artifact",
            "/tmp/operator-status.json",
            "--runbook-verify",
            "/tmp/operator-runbook-verify.json",
            "--require-run-metadata",
            "--require-no-secret-literals",
            "--require-launch-ready",
            "--output",
            "/tmp/operator-status-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-operator-status"
    assert ns.artifact == "/tmp/operator-status.json"
    assert ns.runbook_verify == "/tmp/operator-runbook-verify.json"
    assert ns.require_run_metadata is True
    assert ns.require_no_secret_literals is True
    assert ns.require_launch_ready is True
    assert ns.output == "/tmp/operator-status-verify.json"


def test_prediction_operator_packet_manifest_accepts_status_verify():
    ns = parse_args(
        [
            "prediction-operator-packet-manifest",
            "--status-verify",
            "/tmp/operator-status-verify.json",
            "--require-existing-files",
            "--require-no-secret-literals",
            "--output",
            "/tmp/operator-packet-manifest.json",
        ]
    )

    assert ns.command == "prediction-operator-packet-manifest"
    assert ns.status_verify == "/tmp/operator-status-verify.json"
    assert ns.require_existing_files is True
    assert ns.require_no_secret_literals is True
    assert ns.output == "/tmp/operator-packet-manifest.json"


def test_verify_prediction_operator_packet_manifest_accepts_manifest():
    ns = parse_args(
        [
            "verify-prediction-operator-packet-manifest",
            "--artifact",
            "/tmp/operator-packet-manifest.json",
            "--status-verify",
            "/tmp/operator-status-verify.json",
            "--require-run-metadata",
            "--require-existing-files",
            "--require-no-secret-literals",
            "--output",
            "/tmp/operator-packet-manifest-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-operator-packet-manifest"
    assert ns.artifact == "/tmp/operator-packet-manifest.json"
    assert ns.status_verify == "/tmp/operator-status-verify.json"
    assert ns.require_run_metadata is True
    assert ns.require_existing_files is True
    assert ns.require_no_secret_literals is True
    assert ns.output == "/tmp/operator-packet-manifest-verify.json"


def test_prediction_operator_packet_export_accepts_verified_manifest():
    ns = parse_args(
        [
            "prediction-operator-packet-export",
            "--manifest-verify",
            "/tmp/operator-packet-manifest-verify.json",
            "--target-dir",
            "/tmp/operator-packet",
            "--require-no-secret-literals",
            "--output",
            "/tmp/operator-packet-export.json",
        ]
    )

    assert ns.command == "prediction-operator-packet-export"
    assert ns.manifest_verify == "/tmp/operator-packet-manifest-verify.json"
    assert ns.target_dir == "/tmp/operator-packet"
    assert ns.require_no_secret_literals is True
    assert ns.output == "/tmp/operator-packet-export.json"


def test_verify_prediction_operator_packet_export_accepts_export_artifact():
    ns = parse_args(
        [
            "verify-prediction-operator-packet-export",
            "--artifact",
            "/tmp/operator-packet-export.json",
            "--require-run-metadata",
            "--require-export-manifest",
            "--require-exported-files",
            "--require-no-secret-literals",
            "--output",
            "/tmp/operator-packet-export-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-operator-packet-export"
    assert ns.artifact == "/tmp/operator-packet-export.json"
    assert ns.require_run_metadata is True
    assert ns.require_export_manifest is True
    assert ns.require_exported_files is True
    assert ns.require_no_secret_literals is True
    assert ns.output == "/tmp/operator-packet-export-verify.json"


def test_verify_prediction_operator_packet_directory_accepts_packet_dir():
    ns = parse_args(
        [
            "verify-prediction-operator-packet-directory",
            "--packet-dir",
            "/tmp/operator-packet",
            "--require-readme",
            "--require-checksums",
            "--require-exported-files",
            "--require-no-secret-literals",
            "--output",
            "/tmp/operator-packet-directory-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-operator-packet-directory"
    assert ns.packet_dir == "/tmp/operator-packet"
    assert ns.require_readme is True
    assert ns.require_checksums is True
    assert ns.require_exported_files is True
    assert ns.require_no_secret_literals is True
    assert ns.output == "/tmp/operator-packet-directory-verify.json"


def test_prediction_operator_resume_plan_accepts_packet_dir():
    ns = parse_args(
        [
            "prediction-operator-resume-plan",
            "--packet-dir",
            "/tmp/operator-packet",
            "--dotenv",
            "/tmp/openpact.env",
            "--require-verified-packet",
            "--require-no-secret-literals",
            "--output",
            "/tmp/operator-resume-plan.json",
        ]
    )

    assert ns.command == "prediction-operator-resume-plan"
    assert ns.packet_dir == "/tmp/operator-packet"
    assert ns.dotenv == "/tmp/openpact.env"
    assert ns.require_verified_packet is True
    assert ns.require_no_secret_literals is True
    assert ns.output == "/tmp/operator-resume-plan.json"


def test_verify_prediction_operator_resume_plan_accepts_artifact():
    ns = parse_args(
        [
            "verify-prediction-operator-resume-plan",
            "--artifact",
            "/tmp/operator-resume-plan.json",
            "--require-run-metadata",
            "--require-matches-current-packet",
            "--require-no-secret-literals",
            "--output",
            "/tmp/operator-resume-plan-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-operator-resume-plan"
    assert ns.artifact == "/tmp/operator-resume-plan.json"
    assert ns.require_run_metadata is True
    assert ns.require_matches_current_packet is True
    assert ns.require_no_secret_literals is True
    assert ns.output == "/tmp/operator-resume-plan-verify.json"


def test_verify_prediction_operator_resume_run_accepts_packet_and_artifact():
    ns = parse_args(
        [
            "verify-prediction-operator-resume-run",
            "--packet-dir",
            "/tmp/operator-packet",
            "--artifact",
            "/tmp/run-resume-dry-run.json",
            "--dotenv",
            "/tmp/openpact.env",
            "--require-run-metadata",
            "--require-ok",
            "--require-dry-run",
            "--require-no-secret-literals",
            "--require-congress-prediction-inputs",
            "--require-strict-eval-window-run",
            "--require-selected-source-artifact-hashes",
            "--output",
            "/tmp/run-resume-dry-run-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-operator-resume-run"
    assert ns.packet_dir == "/tmp/operator-packet"
    assert ns.artifact == "/tmp/run-resume-dry-run.json"
    assert ns.dotenv == "/tmp/openpact.env"
    assert ns.require_run_metadata is True
    assert ns.require_ok is True
    assert ns.require_dry_run is True
    assert ns.require_no_secret_literals is True
    assert ns.require_congress_prediction_inputs is True
    assert ns.require_strict_eval_window_run is True
    assert ns.require_selected_source_artifact_hashes is True
    assert ns.output == "/tmp/run-resume-dry-run-verify.json"


def test_prediction_source_url_audit_command():
    ns = parse_args(
        [
            "prediction-source-url-audit",
            "--eval-report",
            "/tmp/eval-report.json",
            "--fail-on-gaps",
            "--output",
            "/tmp/source-url-audit.json",
        ]
    )

    assert ns.command == "prediction-source-url-audit"
    assert ns.eval_report == "/tmp/eval-report.json"
    assert ns.fail_on_gaps is True
    assert ns.output == "/tmp/source-url-audit.json"


def test_verify_prediction_source_url_audit_command():
    ns = parse_args(
        [
            "verify-prediction-source-url-audit",
            "--artifact",
            "/tmp/source-url-audit.json",
            "--require-run-metadata",
            "--require-no-gaps",
            "--require-no-official-source-gaps",
            "--require-no-portable-context-gaps",
            "--output",
            "/tmp/source-url-audit-verify.json",
        ]
    )

    assert ns.command == "verify-prediction-source-url-audit"
    assert ns.artifact == "/tmp/source-url-audit.json"
    assert ns.require_run_metadata is True
    assert ns.require_no_gaps is True
    assert ns.require_no_official_source_gaps is True
    assert ns.require_no_portable_context_gaps is True
    assert ns.output == "/tmp/source-url-audit-verify.json"


# ---------------------------------------------------------------------------
# Unknown subcommand exits cleanly
# ---------------------------------------------------------------------------


def test_unknown_subcommand_exits():
    with pytest.raises(SystemExit):
        parse_args(["unknown-command"])
