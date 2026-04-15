"""Tests for published_rows query helpers.

No live DB — fetch_all is patched at the call site in each test.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from src.query.published_rows import (
    fetch_evidence_card_row,
    fetch_homepage_feed_rows,
    fetch_member_committee_rows,
    fetch_member_row_by_slug,
    fetch_member_rule_fire_rows,
    fetch_member_score_snapshot_rows,
)

CONN = MagicMock()
MODULE = "src.query.published_rows.fetch_all"

_MEMBER_ROW: dict[str, Any] = {
    "id": 1,
    "bioguide_id": "A000001",
    "slug": "jane-doe",
    "full_name": "Jane Doe",
    "first_name": "Jane",
    "last_name": "Doe",
    "party": "D",
    "state": "CA",
    "chamber": "house",
    "current_term_start": date(2023, 1, 3),
    "current_term_end": None,
    "is_current": True,
}


class TestFetchMemberRowBySlug:
    def test_returns_row_when_found(self):
        with patch(MODULE, return_value=[_MEMBER_ROW]) as mock_fa:
            result = fetch_member_row_by_slug(CONN, "jane-doe")
        assert result == _MEMBER_ROW
        sql, params = mock_fa.call_args[0][1], mock_fa.call_args[0][2]
        assert "member" in sql
        assert params == ("jane-doe",)

    def test_returns_none_when_not_found(self):
        with patch(MODULE, return_value=[]):
            result = fetch_member_row_by_slug(CONN, "no-one")
        assert result is None

    def test_passes_conn_through(self):
        with patch(MODULE, return_value=[_MEMBER_ROW]) as mock_fa:
            fetch_member_row_by_slug(CONN, "jane-doe")
        assert mock_fa.call_args[0][0] is CONN


class TestFetchMemberScoreSnapshotRows:
    _SNAPSHOT = {
        "id": 10,
        "member_id": 1,
        "snapshot_at": date(2024, 3, 1),
        "score_total": 42.5,
        "dimension_scores": {"conflict_of_interest_risk": 42.5},
        "published_at": datetime(2024, 3, 2, 0, 0),
    }

    def test_returns_list(self):
        with patch(MODULE, return_value=[self._SNAPSHOT]) as mock_fa:
            result = fetch_member_score_snapshot_rows(CONN, 1)
        assert result == [self._SNAPSHOT]
        sql = mock_fa.call_args[0][1]
        assert "score_snapshot" in sql
        assert mock_fa.call_args[0][2] == (1,)

    def test_returns_empty_list_when_none(self):
        with patch(MODULE, return_value=[]):
            result = fetch_member_score_snapshot_rows(CONN, 999)
        assert result == []


class TestFetchMemberRuleFireRows:
    _FIRE = {
        "id": 5,
        "rule_id": "committee_sector_trade_v1",
        "dimension": "conflict_of_interest_risk",
        "severity": "high",
        "explanation": "Traded in sector matching committee.",
        "fired_at": datetime(2024, 2, 14, 12, 0),
        "evidence_card_id": "ec_abc123",
        "short_explanation": "Sold tech stock while on tech committee.",
        "score_delta": -10.0,
        "snapshot_date": date(2024, 2, 14),
    }

    def test_returns_fire_rows(self):
        with patch(MODULE, return_value=[self._FIRE]) as mock_fa:
            result = fetch_member_rule_fire_rows(CONN, 1)
        assert result == [self._FIRE]
        sql = mock_fa.call_args[0][1]
        assert "rule_fire" in sql
        assert "evidence_card" in sql

    def test_joins_evidence_card(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_member_rule_fire_rows(CONN, 1)
        sql = mock_fa.call_args[0][1]
        assert "LEFT JOIN evidence_card" in sql or "left join evidence_card" in sql.lower()

    def test_filters_by_member_id(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_member_rule_fire_rows(CONN, 42)
        assert mock_fa.call_args[0][2] == (42,)


class TestFetchMemberCommitteeRows:
    _COMMITTEE_ROW = {
        "id": 3,
        "role": "member",
        "start_date": date(2023, 1, 3),
        "end_date": None,
        "is_current": True,
        "committee_name": "House Committee on Science",
        "chamber": "house",
        "committee_type": "standing",
        "review_tier": "deterministic",
    }

    def test_returns_committee_rows(self):
        with patch(MODULE, return_value=[self._COMMITTEE_ROW]) as mock_fa:
            result = fetch_member_committee_rows(CONN, 1)
        assert result == [self._COMMITTEE_ROW]
        sql = mock_fa.call_args[0][1]
        assert "committee_membership" in sql
        assert "committee" in sql

    def test_includes_committee_name(self):
        with patch(MODULE, return_value=[self._COMMITTEE_ROW]):
            result = fetch_member_committee_rows(CONN, 1)
        assert result[0]["committee_name"] == "House Committee on Science"

    def test_filters_by_member_id(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_member_committee_rows(CONN, 7)
        assert mock_fa.call_args[0][2] == (7,)


class TestFetchEvidenceCardRow:
    _CARD = {
        "id": 2,
        "public_id": "ec_abc123",
        "member_id": 1,
        "dimension": "conflict_of_interest_risk",
        "score_delta": -10.0,
        "short_explanation": "Sold tech stock while on tech committee.",
        "source_anchors": [],
        "facts": {},
        "inferences": {},
        "normative_judgments": {},
        "confidence_label": "HIGH",
        "member_page_slug": "jane-doe",
        "rendered_at": datetime(2024, 2, 14, 15, 0),
        "member_full_name": "Jane Doe",
        "member_slug": "jane-doe",
        "state": "CA",
        "chamber": "house",
        "party": "D",
    }

    def test_returns_card_when_found(self):
        with patch(MODULE, return_value=[self._CARD]) as mock_fa:
            result = fetch_evidence_card_row(CONN, "ec_abc123")
        assert result == self._CARD
        sql = mock_fa.call_args[0][1]
        assert "evidence_card" in sql
        assert mock_fa.call_args[0][2] == ("ec_abc123",)

    def test_joins_member(self):
        with patch(MODULE, return_value=[self._CARD]) as mock_fa:
            fetch_evidence_card_row(CONN, "ec_abc123")
        sql = mock_fa.call_args[0][1]
        assert "member" in sql

    def test_returns_none_when_not_found(self):
        with patch(MODULE, return_value=[]):
            result = fetch_evidence_card_row(CONN, "ec_unknown")
        assert result is None

    def test_includes_member_fields(self):
        with patch(MODULE, return_value=[self._CARD]):
            result = fetch_evidence_card_row(CONN, "ec_abc123")
        assert result["member_full_name"] == "Jane Doe"
        assert result["member_slug"] == "jane-doe"


class TestFetchHomepageFeedRows:
    _FEED_ROW = {
        "public_id": "ec_abc123",
        "dimension": "conflict_of_interest_risk",
        "score_delta": -10.0,
        "short_explanation": "Sold tech stock while on tech committee.",
        "confidence_label": "HIGH",
        "rendered_at": datetime(2024, 2, 14, 15, 0),
        "member_full_name": "Jane Doe",
        "member_slug": "jane-doe",
        "state": "CA",
        "chamber": "house",
        "party": "D",
    }

    def test_returns_feed_rows(self):
        with patch(MODULE, return_value=[self._FEED_ROW]):
            result = fetch_homepage_feed_rows(CONN)
        assert result == [self._FEED_ROW]

    def test_default_limit_is_20(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_homepage_feed_rows(CONN)
        assert mock_fa.call_args[0][2] == (20,)

    def test_custom_limit(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_homepage_feed_rows(CONN, limit=5)
        assert mock_fa.call_args[0][2] == (5,)

    def test_filters_rendered_at_not_null(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_homepage_feed_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "rendered_at IS NOT NULL" in sql

    def test_orders_by_rendered_at_desc(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_homepage_feed_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "rendered_at DESC" in sql

    def test_joins_member(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_homepage_feed_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "member" in sql
