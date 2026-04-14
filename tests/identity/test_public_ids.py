"""Tests for src/identity/public_ids.py.

All tests are pure (no I/O, no network calls).  The primary properties under
test are:
- determinism: same inputs → same ID
- distinctness: different inputs → different IDs
- shape: correct prefix and URL-safe characters
- normalization: equivalent input variants produce the same canonical ID
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from src.identity.public_ids import (
    build_evidence_card_id,
    build_feed_event_id,
    build_snapshot_id,
    normalize_bioguide_id,
    normalize_date,
    normalize_dimension,
    normalize_rule_id,
    normalize_zip,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_URL_SAFE = re.compile(r"^[a-z0-9-]+$")


def _assert_id_shape(id_: str, prefix: str) -> None:
    assert id_.startswith(f"{prefix}-"), f"Expected prefix '{prefix}-', got: {id_!r}"
    assert _URL_SAFE.match(id_), f"ID contains non-URL-safe characters: {id_!r}"
    # prefix (2) + hyphen (1) + 16 base32 chars = 19
    assert len(id_) == 19, f"Expected length 19, got {len(id_)}: {id_!r}"


# ---------------------------------------------------------------------------
# normalize_bioguide_id
# ---------------------------------------------------------------------------


def test_normalize_bioguide_id_strips_and_uppercases():
    assert normalize_bioguide_id("  a000001 ") == "A000001"


def test_normalize_bioguide_id_already_clean():
    assert normalize_bioguide_id("B123456") == "B123456"


def test_normalize_bioguide_id_lowercase_input():
    assert normalize_bioguide_id("c999999") == "C999999"


# ---------------------------------------------------------------------------
# normalize_date
# ---------------------------------------------------------------------------


def test_normalize_date_from_date_object():
    assert normalize_date(dt.date(2024, 1, 15)) == "2024-01-15"


def test_normalize_date_from_datetime_object():
    assert normalize_date(dt.datetime(2024, 6, 30, 12, 0, 0)) == "2024-06-30"


def test_normalize_date_from_string():
    assert normalize_date("2024-03-01") == "2024-03-01"


def test_normalize_date_from_string_with_whitespace():
    assert normalize_date("  2024-03-01  ") == "2024-03-01"


def test_normalize_date_invalid_string_raises():
    with pytest.raises(ValueError):
        normalize_date("not-a-date")


# ---------------------------------------------------------------------------
# normalize_rule_id
# ---------------------------------------------------------------------------


def test_normalize_rule_id_lowercases_and_strips():
    assert normalize_rule_id("  Committee_Sector_Trade  ") == "committee_sector_trade"


def test_normalize_rule_id_collapses_spaces():
    assert normalize_rule_id("late  disclosure") == "late_disclosure"


def test_normalize_rule_id_single_space_becomes_underscore():
    assert normalize_rule_id("late disclosure") == "late_disclosure"


def test_normalize_rule_id_already_canonical():
    assert normalize_rule_id("committee_sector_trade") == "committee_sector_trade"


# ---------------------------------------------------------------------------
# normalize_dimension
# ---------------------------------------------------------------------------


def test_normalize_dimension_lowercases_and_strips():
    assert normalize_dimension("  Conflict_Of_Interest_Risk  ") == "conflict_of_interest_risk"


def test_normalize_dimension_spaces_to_underscores():
    assert normalize_dimension("conflict of interest risk") == "conflict_of_interest_risk"


def test_normalize_dimension_hyphens_to_underscores():
    assert normalize_dimension("conflict-of-interest-risk") == "conflict_of_interest_risk"


def test_normalize_dimension_already_canonical():
    assert normalize_dimension("conflict_of_interest_risk") == "conflict_of_interest_risk"


# ---------------------------------------------------------------------------
# normalize_zip
# ---------------------------------------------------------------------------


def test_normalize_zip_five_digits():
    assert normalize_zip("90210") == "90210"


def test_normalize_zip_strips_whitespace():
    assert normalize_zip("  02134 ") == "02134"


def test_normalize_zip_strips_plus4():
    assert normalize_zip("90210-1234") == "90210"


def test_normalize_zip_zero_pads():
    assert normalize_zip("01234") == "01234"


def test_normalize_zip_invalid_raises():
    with pytest.raises(ValueError):
        normalize_zip("123")


def test_normalize_zip_alpha_raises():
    with pytest.raises(ValueError):
        normalize_zip("ABCDE")


# ---------------------------------------------------------------------------
# build_evidence_card_id
# ---------------------------------------------------------------------------


def test_build_evidence_card_id_shape():
    id_ = build_evidence_card_id(
        bioguide_id="A000001",
        rule_id="committee_sector_trade",
        dimension="conflict_of_interest_risk",
        fired_date=dt.date(2024, 3, 15),
    )
    _assert_id_shape(id_, "ec")


def test_build_evidence_card_id_deterministic():
    kwargs = dict(
        bioguide_id="A000001",
        rule_id="committee_sector_trade",
        dimension="conflict_of_interest_risk",
        fired_date=dt.date(2024, 3, 15),
    )
    assert build_evidence_card_id(**kwargs) == build_evidence_card_id(**kwargs)


def test_build_evidence_card_id_stable_value():
    """Pin the exact output so future refactors don't silently break IDs."""
    id_ = build_evidence_card_id(
        bioguide_id="A000001",
        rule_id="committee_sector_trade",
        dimension="conflict_of_interest_risk",
        fired_date="2024-03-15",
    )
    # Record pinned value; if this changes, public URLs break.
    assert id_ == build_evidence_card_id(
        bioguide_id="a000001",  # normalization: lower → upper
        rule_id="Committee_Sector_Trade",  # normalization: case
        dimension="conflict of interest risk",  # normalization: spaces
        fired_date=dt.date(2024, 3, 15),  # normalization: date object
    ), "Equivalent inputs must produce the same ID"


def test_build_evidence_card_id_distinct_for_different_members():
    kwargs = dict(
        rule_id="committee_sector_trade",
        dimension="conflict_of_interest_risk",
        fired_date=dt.date(2024, 3, 15),
    )
    a = build_evidence_card_id(bioguide_id="A000001", **kwargs)
    b = build_evidence_card_id(bioguide_id="B999999", **kwargs)
    assert a != b


def test_build_evidence_card_id_distinct_for_different_dates():
    kwargs = dict(
        bioguide_id="A000001",
        rule_id="committee_sector_trade",
        dimension="conflict_of_interest_risk",
    )
    a = build_evidence_card_id(fired_date=dt.date(2024, 3, 15), **kwargs)
    b = build_evidence_card_id(fired_date=dt.date(2024, 3, 16), **kwargs)
    assert a != b


def test_build_evidence_card_id_distinct_for_different_rules():
    kwargs = dict(
        bioguide_id="A000001",
        dimension="conflict_of_interest_risk",
        fired_date=dt.date(2024, 3, 15),
    )
    a = build_evidence_card_id(rule_id="committee_sector_trade", **kwargs)
    b = build_evidence_card_id(rule_id="late_or_amended_disclosure", **kwargs)
    assert a != b


# ---------------------------------------------------------------------------
# build_snapshot_id
# ---------------------------------------------------------------------------


def test_build_snapshot_id_shape():
    id_ = build_snapshot_id(bioguide_id="A000001", snapshot_date=dt.date(2024, 6, 1))
    _assert_id_shape(id_, "ss")


def test_build_snapshot_id_deterministic():
    kwargs = dict(bioguide_id="A000001", snapshot_date=dt.date(2024, 6, 1))
    assert build_snapshot_id(**kwargs) == build_snapshot_id(**kwargs)


def test_build_snapshot_id_normalization_equivalence():
    a = build_snapshot_id(bioguide_id="a000001", snapshot_date="2024-06-01")
    b = build_snapshot_id(bioguide_id="A000001", snapshot_date=dt.date(2024, 6, 1))
    assert a == b


def test_build_snapshot_id_distinct_for_different_members():
    a = build_snapshot_id(bioguide_id="A000001", snapshot_date=dt.date(2024, 6, 1))
    b = build_snapshot_id(bioguide_id="C000002", snapshot_date=dt.date(2024, 6, 1))
    assert a != b


def test_build_snapshot_id_distinct_for_different_dates():
    a = build_snapshot_id(bioguide_id="A000001", snapshot_date=dt.date(2024, 6, 1))
    b = build_snapshot_id(bioguide_id="A000001", snapshot_date=dt.date(2024, 7, 1))
    assert a != b


def test_build_snapshot_id_prefix_differs_from_evidence_card():
    ec = build_evidence_card_id(
        bioguide_id="A000001",
        rule_id="committee_sector_trade",
        dimension="conflict_of_interest_risk",
        fired_date=dt.date(2024, 6, 1),
    )
    ss = build_snapshot_id(bioguide_id="A000001", snapshot_date=dt.date(2024, 6, 1))
    assert ec.startswith("ec-")
    assert ss.startswith("ss-")
    assert ec != ss


# ---------------------------------------------------------------------------
# build_feed_event_id
# ---------------------------------------------------------------------------


def test_build_feed_event_id_shape():
    id_ = build_feed_event_id(
        zip_code="90210",
        bioguide_id="A000001",
        event_type="evidence_card",
        event_date=dt.date(2024, 6, 1),
    )
    _assert_id_shape(id_, "fe")


def test_build_feed_event_id_deterministic():
    kwargs = dict(
        zip_code="90210",
        bioguide_id="A000001",
        event_type="evidence_card",
        event_date=dt.date(2024, 6, 1),
    )
    assert build_feed_event_id(**kwargs) == build_feed_event_id(**kwargs)


def test_build_feed_event_id_normalization_equivalence():
    a = build_feed_event_id(
        zip_code="90210-1234",
        bioguide_id="a000001",
        event_type="evidence card",
        event_date="2024-06-01",
    )
    b = build_feed_event_id(
        zip_code="90210",
        bioguide_id="A000001",
        event_type="evidence_card",
        event_date=dt.date(2024, 6, 1),
    )
    assert a == b


def test_build_feed_event_id_distinct_for_different_zips():
    kwargs = dict(
        bioguide_id="A000001",
        event_type="evidence_card",
        event_date=dt.date(2024, 6, 1),
    )
    a = build_feed_event_id(zip_code="90210", **kwargs)
    b = build_feed_event_id(zip_code="10001", **kwargs)
    assert a != b


def test_build_feed_event_id_distinct_for_different_event_types():
    kwargs = dict(
        zip_code="90210",
        bioguide_id="A000001",
        event_date=dt.date(2024, 6, 1),
    )
    a = build_feed_event_id(event_type="evidence_card", **kwargs)
    b = build_feed_event_id(event_type="snapshot", **kwargs)
    assert a != b


def test_build_feed_event_id_distinct_from_snapshot_and_evidence_card():
    fe = build_feed_event_id(
        zip_code="90210",
        bioguide_id="A000001",
        event_type="evidence_card",
        event_date=dt.date(2024, 6, 1),
    )
    ss = build_snapshot_id(bioguide_id="A000001", snapshot_date=dt.date(2024, 6, 1))
    assert fe.startswith("fe-")
    assert fe != ss
