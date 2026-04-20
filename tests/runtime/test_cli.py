"""Tests for the CLI parser layer.

No DB, no network.  All assertions target parser output only.
"""

from __future__ import annotations

import datetime
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
    ns = parse_args([
        "load-congress",
        "--congress", "118",
        "--include-votes",
        "--house-vote-year", "2024",
        "--senate-session", "2",
    ])
    assert ns.congress == 118
    assert ns.include_votes is True
    assert ns.house_vote_year == 2024
    assert ns.senate_session == 2


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
    ns = parse_args([
        "parse-disclosures",
        "--chamber", "senate",
        "--local-root", "/data/artifacts",
        "--limit", "100",
    ])
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
    ns = parse_args(["publish", "--snapshot-date", "2025-06-30", "--zip-bundle", "/tmp/zip_bundle.json"])
    assert ns.snapshot_date == datetime.date(2025, 6, 30)


def test_publish_out_dir():
    ns = parse_args(["publish", "--out-dir", "/tmp/snap", "--zip-bundle", "/tmp/zip_bundle.json"])
    assert ns.out_dir == "/tmp/snap"


def test_publish_all_args():
    ns = parse_args(
        [
            "publish",
            "--snapshot-date", "2025-06-30",
            "--out-dir", "/tmp/snap",
            "--zip-bundle", "/tmp/zip_bundle.json",
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
    ns = parse_args([
        "process-disclosures",
        "--chamber", "house",
        "--local-root", "/data",
        "--limit", "10",
    ])
    assert ns.chamber == "house"
    assert ns.local_root == "/data"
    assert ns.limit == 10


# ---------------------------------------------------------------------------
# load-congress-local
# ---------------------------------------------------------------------------


def test_load_congress_local_command():
    ns = parse_args(["load-congress-local", "--archive", "/data/congress_119.json", "--congress", "119"])
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
    ns = parse_args([
        "run-oracle-local",
        "--congress-archive", "/data/congress_119.json",
        "--disclosures-bundle", "/data/disclosures.zip",
        "--snapshot-date", "2025-03-01",
        "--target-dir", "/out/snap",
    ])
    assert ns.command == "run-oracle-local"
    assert ns.congress_archive == "/data/congress_119.json"
    assert ns.disclosures_bundle == "/data/disclosures.zip"
    assert ns.snapshot_date == datetime.date(2025, 3, 1)
    assert ns.target_dir == "/out/snap"
    assert ns.chamber == "both"
    assert ns.limit is None
    assert ns.snapshot_id is None


def test_run_oracle_local_explicit_congress_and_artifact_root() -> None:
    ns = parse_args([
        "run-oracle-local",
        "--congress-archive", "/data/congress_118.json",
        "--disclosures-bundle", "/data/disclosures.zip",
        "--congress", "118",
        "--snapshot-date", "2025-03-01",
        "--target-dir", "/out/snap",
        "--artifact-root", "/data/artifacts",
    ])
    assert ns.congress == 118
    assert ns.artifact_root == "/data/artifacts"


def test_run_oracle_local_missing_congress_archive_exits():
    with pytest.raises(SystemExit):
        parse_args([
            "run-oracle-local",
            "--disclosures-bundle", "/data/disclosures.zip",
            "--snapshot-date", "2025-03-01",
            "--target-dir", "/out/snap",
        ])


def test_run_oracle_local_missing_disclosures_bundle_exits():
    with pytest.raises(SystemExit):
        parse_args([
            "run-oracle-local",
            "--congress-archive", "/data/congress_119.json",
            "--snapshot-date", "2025-03-01",
            "--target-dir", "/out/snap",
        ])


def test_run_oracle_local_missing_snapshot_date_exits():
    with pytest.raises(SystemExit):
        parse_args([
            "run-oracle-local",
            "--congress-archive", "/data/congress_119.json",
            "--disclosures-bundle", "/data/disclosures.zip",
            "--target-dir", "/out/snap",
        ])


def test_run_oracle_local_missing_target_dir_exits():
    with pytest.raises(SystemExit):
        parse_args([
            "run-oracle-local",
            "--congress-archive", "/data/congress_119.json",
            "--disclosures-bundle", "/data/disclosures.zip",
            "--snapshot-date", "2025-03-01",
        ])


def test_run_oracle_local_invalid_date_exits():
    with pytest.raises(SystemExit):
        parse_args([
            "run-oracle-local",
            "--congress-archive", "/data/congress_119.json",
            "--disclosures-bundle", "/data/disclosures.zip",
            "--snapshot-date", "not-a-date",
            "--target-dir", "/out/snap",
        ])


def test_run_oracle_local_chamber_house():
    ns = parse_args([
        "run-oracle-local",
        "--congress-archive", "/data/congress_119.json",
        "--disclosures-bundle", "/data/d.zip",
        "--snapshot-date", "2025-03-01",
        "--target-dir", "/out",
        "--chamber", "house",
    ])
    assert ns.chamber == "house"


def test_run_oracle_local_invalid_chamber_exits():
    with pytest.raises(SystemExit):
        parse_args([
            "run-oracle-local",
            "--congress-archive", "/data/congress_119.json",
            "--disclosures-bundle", "/data/d.zip",
            "--snapshot-date", "2025-03-01",
            "--target-dir", "/out",
            "--chamber", "invalid",
        ])


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


def test_run_oracle_local_limit():
    ns = parse_args([
        "run-oracle-local",
        "--congress-archive", "/data/congress_119.json",
        "--disclosures-bundle", "/data/d.zip",
        "--snapshot-date", "2025-03-01",
        "--target-dir", "/out",
        "--limit", "10",
    ])
    assert ns.limit == 10


def test_run_oracle_local_snapshot_id():
    ns = parse_args([
        "run-oracle-local",
        "--congress-archive", "/data/congress_119.json",
        "--disclosures-bundle", "/data/d.zip",
        "--snapshot-date", "2025-03-01",
        "--target-dir", "/out",
        "--snapshot-id", "snap-v1",
    ])
    assert ns.snapshot_id == "snap-v1"


def test_run_oracle_local_all_args():
    ns = parse_args([
        "run-oracle-local",
        "--congress-archive", "/data/congress_119.json",
        "--disclosures-bundle", "/data/disclosures.zip",
        "--snapshot-date", "2025-06-15",
        "--target-dir", "/out/snap",
        "--chamber", "senate",
        "--limit", "100",
        "--snapshot-id", "2025-06-15-senate",
    ])
    assert ns.congress_archive == "/data/congress_119.json"
    assert ns.disclosures_bundle == "/data/disclosures.zip"
    assert ns.snapshot_date == datetime.date(2025, 6, 15)
    assert ns.target_dir == "/out/snap"
    assert ns.chamber == "senate"
    assert ns.limit == 100
    assert ns.snapshot_id == "2025-06-15-senate"


def test_plan_history_backfill_command() -> None:
    ns = parse_args([
        "plan-history-backfill",
        "--congress",
        "119",
        "--target-root",
        "/out/history",
        "--start-date",
        "2025-01-03",
        "--end-date",
        "2025-02-01",
    ])
    assert ns.command == "plan-history-backfill"
    assert ns.congress == 119
    assert ns.target_root == "/out/history"
    assert ns.start_date == datetime.date(2025, 1, 3)
    assert ns.end_date == datetime.date(2025, 2, 1)


def test_plan_history_backfill_requires_congress() -> None:
    with pytest.raises(SystemExit):
        parse_args(["plan-history-backfill", "--target-root", "/out/history"])


def test_aggregate_history_command() -> None:
    ns = parse_args([
        "aggregate-history",
        "--source-root",
        "/out/history/2025-01-06",
        "--source-root",
        "/out/history/2025-01-13",
        "--target-root",
        "/out/aggregate",
    ])
    assert ns.command == "aggregate-history"
    assert ns.source_root == ["/out/history/2025-01-06", "/out/history/2025-01-13"]
    assert ns.target_root == "/out/aggregate"


def test_aggregate_history_requires_source_root() -> None:
    with pytest.raises(SystemExit):
        parse_args(["aggregate-history", "--target-root", "/out/aggregate"])


def test_run_history_backfill_local_command() -> None:
    ns = parse_args([
        "run-history-backfill-local",
        "--congress-archive", "/data/congress_119.json",
        "--disclosures-bundle", "/data/disclosures.zip",
        "--congress", "119",
        "--target-root", "/out/history",
        "--aggregate-root", "/out/history-aggregate",
        "--start-date", "2025-01-03",
        "--end-date", "2025-02-01",
        "--chamber", "senate",
        "--limit", "25",
        "--artifact-root", "/data/artifacts",
        "--overwrite",
        "--continue-on-error",
    ])
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
    ns = parse_args(["verify-publish-roundtrip", "--publish-root", "/var/data/snapshots/2025-06-15"])
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
# Unknown subcommand exits cleanly
# ---------------------------------------------------------------------------


def test_unknown_subcommand_exits():
    with pytest.raises(SystemExit):
        parse_args(["unknown-command"])
