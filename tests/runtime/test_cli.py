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
# Unknown subcommand exits cleanly
# ---------------------------------------------------------------------------


def test_unknown_subcommand_exits():
    with pytest.raises(SystemExit):
        parse_args(["unknown-command"])
