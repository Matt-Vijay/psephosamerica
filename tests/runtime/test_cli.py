"""Tests for the CLI parser layer.

No DB, no network.  All assertions target parser output only.
"""

from __future__ import annotations

import datetime
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
    assert ns.house_vote_year == 2024


def test_load_congress_house_vote_year_invalid_exits():
    with pytest.raises(SystemExit):
        parse_args(["load-congress", "--house-vote-year", "notanumber"])


def test_load_congress_senate_session_default():
    ns = parse_args(["load-congress"])
    assert ns.senate_session is None


def test_load_congress_senate_session():
    ns = parse_args(["load-congress", "--senate-session", "1"])
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
    ns = parse_args(["publish"])
    assert ns.command == "publish"
    assert ns.snapshot_date is None
    assert ns.out_dir is None


def test_publish_snapshot_date_parsed():
    ns = parse_args(["publish", "--snapshot-date", "2025-06-30"])
    assert ns.snapshot_date == datetime.date(2025, 6, 30)


def test_publish_out_dir():
    ns = parse_args(["publish", "--out-dir", "/tmp/snap"])
    assert ns.out_dir == "/tmp/snap"


def test_publish_all_args():
    ns = parse_args(["publish", "--snapshot-date", "2025-06-30", "--out-dir", "/tmp/snap"])
    assert ns.snapshot_date == datetime.date(2025, 6, 30)
    assert ns.out_dir == "/tmp/snap"


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
    assert ns.chamber == "both"
    assert ns.limit is None


def test_process_disclosures_local_missing_bundle_exits():
    with pytest.raises(SystemExit):
        parse_args(["process-disclosures-local"])


def test_process_disclosures_local_chamber_house():
    ns = parse_args(["process-disclosures-local", "--bundle", "/data/d.zip", "--chamber", "house"])
    assert ns.chamber == "house"


def test_process_disclosures_local_chamber_senate():
    ns = parse_args(["process-disclosures-local", "--bundle", "/data/d.zip", "--chamber", "senate"])
    assert ns.chamber == "senate"


def test_process_disclosures_local_invalid_chamber_exits():
    with pytest.raises(SystemExit):
        parse_args(["process-disclosures-local", "--bundle", "/data/d.zip", "--chamber", "invalid"])


def test_process_disclosures_local_limit():
    ns = parse_args(["process-disclosures-local", "--bundle", "/data/d.zip", "--limit", "50"])
    assert ns.limit == 50


def test_process_disclosures_local_invalid_limit_exits():
    with pytest.raises(SystemExit):
        parse_args(["process-disclosures-local", "--bundle", "/data/d.zip", "--limit", "notanumber"])


def test_process_disclosures_local_all_args():
    ns = parse_args([
        "process-disclosures-local",
        "--bundle", "/data/disclosures.zip",
        "--chamber", "senate",
        "--limit", "25",
    ])
    assert ns.bundle == "/data/disclosures.zip"
    assert ns.chamber == "senate"
    assert ns.limit == 25


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
# Unknown subcommand exits cleanly
# ---------------------------------------------------------------------------


def test_unknown_subcommand_exits():
    with pytest.raises(SystemExit):
        parse_args(["unknown-command"])
