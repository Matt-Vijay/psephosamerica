"""Tests for published_rows query helpers.

No live DB — fetch_all is patched at the call site in each test.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from src.query.evidence_card import assemble_evidence_card
from src.query.published_rows import (
    fetch_all_ontology_edge_rows,
    fetch_evidence_card_row,
    fetch_homepage_feed_rows,
    fetch_member_committee_rows,
    fetch_member_row_by_slug,
    fetch_member_rule_fire_rows,
    fetch_member_score_snapshot_rows,
    fetch_bill_semantic_input_rows,
    fetch_vote_prediction_backtest_bill_signal_rows,
    fetch_vote_prediction_contribution_signal_rows,
    fetch_vote_prediction_backtest_feature_rows,
    fetch_vote_prediction_backtest_label_rows,
    fetch_vote_prediction_readiness_rows,
    fetch_vote_prediction_statement_signal_rows,
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
        assert "JOIN evidence_card" in sql
        assert "LEFT JOIN evidence_card" not in sql

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
        "source_anchors": [
            {
                "source_type": "financial_disclosure",
                "source_id": "fd-001",
                "url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/001.pdf",
                "label": "Financial disclosure",
            }
        ],
        "facts": {"fact": "Trade was disclosed."},
        "inferences": {},
        "normative_judgments": {},
        "confidence_label": "HIGH",
        "member_page_slug": "jane-doe",
        "rendered_at": datetime(2024, 2, 14, 15, 0),
        "member_full_name": "Jane Doe",
        "member_bioguide_id": "A000001",
        "member_slug": "jane-doe",
        "state": "CA",
        "chamber": "house",
        "party": "D",
        "rule_id": "committee_sector_trade.v1",
        "rule_version": "1",
        "created_at": datetime(2024, 2, 14, 14, 0),
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
        assert result["member_bioguide_id"] == "A000001"
        assert result["member_slug"] == "jane-doe"

    def test_selects_assembly_ready_rule_fire_fields(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_evidence_card_row(CONN, "ec_abc123")
        sql = mock_fa.call_args[0][1]
        assert "m.bioguide_id AS member_bioguide_id" in sql
        assert "rf.rule_id" in sql
        assert "rf.rule_version" in sql
        assert "ec.created_at" in sql
        assert "JOIN rule_fire" in sql
        assert "LEFT JOIN rule_fire" not in sql

    def test_returned_row_can_be_assembled(self):
        with patch(MODULE, return_value=[self._CARD]):
            result = fetch_evidence_card_row(CONN, "ec_abc123")
        card = assemble_evidence_card(result)
        assert card.evidence_card_id == "ec_abc123"
        assert card.member_bioguide_id == "A000001"
        assert card.rule_id == "committee_sector_trade.v1"


class TestFetchHomepageFeedRows:
    _FEED_ROW = {
        "public_id": "ec_abc123",
        "dimension": "conflict_of_interest_risk",
        "score_delta": -10.0,
        "short_explanation": "Sold tech stock while on tech committee.",
        "confidence_label": "HIGH",
        "rendered_at": datetime(2024, 2, 14, 15, 0),
        "member_bioguide_id": "A000001",
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


class TestFetchAllOntologyEdgeRows:
    _EDGE = {
        "edge_id": "ont-edge-001",
        "edge_type": "member_committee_assignment",
        "subject_node_type": "member",
        "subject_node_id": "A000001",
        "subject_node_label": "Jane Doe",
        "object_node_type": "committee",
        "object_node_id": "HSEN",
        "object_node_label": "Energy",
        "source_anchors": [
            {
                "source_type": "committee_membership",
                "source_id": "cm-1",
                "url": "https://api.congress.gov/v3/committee/house/HSEN?format=json",
                "label": "Committee membership",
            }
        ],
        "confidence": "high",
        "attributes": {"role": "member"},
    }

    def test_returns_edges(self):
        with patch(MODULE, return_value=[self._EDGE]) as mock_fa:
            result = fetch_all_ontology_edge_rows(CONN)
        assert result == [self._EDGE]
        sql = mock_fa.call_args[0][1]
        assert "ontology_edge" in sql
        assert "ORDER BY edge_type" in sql

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

    def test_orders_by_rendered_at_desc_then_public_id(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_homepage_feed_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "ORDER BY ec.rendered_at DESC, ec.public_id" in sql

    def test_joins_member(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_homepage_feed_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "JOIN member" in sql


class TestFetchVotePredictionReadinessRows:
    _ROW = {
        "bioguide_id": "A000001",
        "vote_count": 3,
        "yea_count": 2,
        "nay_count": 1,
        "present_count": 0,
        "not_voting_count": 0,
        "latest_vote_date": date(2026, 4, 25),
        "vote_event_count": 12,
    }

    def test_returns_rows(self):
        with patch(MODULE, return_value=[self._ROW]) as mock_fa:
            result = fetch_vote_prediction_readiness_rows(CONN)
        assert result == [self._ROW]
        sql = mock_fa.call_args[0][1]
        assert "vote_cast" in sql
        assert "vote_event" in sql

    def test_left_joins_vote_history_from_current_members(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_readiness_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "FROM member m" in sql
        assert "LEFT JOIN vote_cast" in sql
        assert "LEFT JOIN vote_event" in sql
        assert "m.is_current = true" in sql

    def test_readiness_vote_history_is_scoped_to_member_active_term(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_readiness_rows(CONN)
        sql = mock_fa.call_args[0][1]
        normalized_sql = " ".join(sql.split())
        assert "FROM member_term mt_readiness" in sql
        assert "mt_readiness.member_id = m.id" in sql
        assert "mt_readiness.start_date <= ve.vote_date" in sql
        assert "mt_readiness.end_date IS NULL OR mt_readiness.end_date >= ve.vote_date" in sql
        assert (
            "NOT EXISTS ( SELECT 1 FROM member_term mt_readiness_any "
            "WHERE mt_readiness_any.member_id = m.id )" in normalized_sql
        )
        assert "m.current_term_start IS NULL OR m.current_term_start <= ve.vote_date" in sql
        assert "m.current_term_end IS NULL OR m.current_term_end >= ve.vote_date" in sql

    def test_selects_explicit_congress_jurisdiction_fields(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_readiness_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "'us_congress' AS jurisdiction_id" in sql
        assert "('us_congress_' || m.chamber) AS legislative_body_id" in sql
        assert "('congress_' || ve.congress || '_session_' || ve.session_number)" in sql
        assert "AS legislative_session_id" in sql
        assert "m.chamber" in sql

    def test_readiness_session_id_comes_from_latest_member_vote(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_readiness_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "array_agg(" in sql
        assert "ORDER BY ve.vote_date DESC, ve.id DESC" in sql
        assert "FILTER (WHERE ve.id IS NOT NULL)" in sql
        assert "MAX(\n                   ('congress_'" not in sql

    def test_counts_vote_options_and_total_events(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_readiness_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "COUNT(ve.id)" in sql
        assert "FILTER (WHERE vc.vote_option = 'yea')" in sql
        assert "WITH vote_event_total" in sql
        assert "CROSS JOIN vote_event_total" in sql
        assert "member" in sql

    def test_selects_member_bioguide_id(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_homepage_feed_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "m.bioguide_id AS member_bioguide_id" in sql


class TestFetchVotePredictionBacktestFeatureRows:
    def test_filters_feature_votes_at_or_before_cutoff(self):
        cutoff = date(2024, 12, 31)
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_feature_rows(CONN, cutoff)
        sql, params = mock_fa.call_args[0][1], mock_fa.call_args[0][2]
        normalized_sql = " ".join(sql.split())
        assert "WHERE ve.vote_date <= %s" in sql
        assert "WHERE vote_date <= %s" in sql
        assert "EXISTS (" in sql
        assert "FROM member_term mt" in sql
        assert "mt.member_id = m.id" in sql
        assert "mt_cutoff.start_date <= %s" in sql
        assert "mt_cutoff.end_date IS NULL OR mt_cutoff.end_date >= %s" in normalized_sql
        assert (
            "NOT EXISTS ( SELECT 1 FROM member_term mt_any WHERE mt_any.member_id = m.id )"
            in normalized_sql
        )
        assert "m.current_term_start IS NULL OR m.current_term_start <= %s" in sql
        assert "m.current_term_end IS NULL OR m.current_term_end >= %s" in sql
        assert params == (cutoff, cutoff, cutoff, cutoff, cutoff, cutoff)

    def test_feature_vote_history_is_scoped_to_member_active_term(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_feature_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "LEFT JOIN feature_vote_cast fvc" in sql
        assert "mt.start_date <= fvc.vote_date" in sql
        assert "mt.end_date IS NULL OR mt.end_date >= fvc.vote_date" in sql
        assert "m.current_term_start IS NULL OR m.current_term_start <= fvc.vote_date" in sql
        assert "m.current_term_end IS NULL OR m.current_term_end >= fvc.vote_date" in sql

    def test_backtest_feature_rows_do_not_filter_to_current_members(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_feature_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "feature_vote_cast" in sql
        assert "m.is_current = true" not in sql
        assert "LEFT JOIN feature_vote_cast fvc" in sql

    def test_returns_vote_history_source_coverage_fields(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_feature_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "source_record_id" in sql
        assert "COALESCE(vc.source_record_id, ve.source_record_id) AS source_record_id" in sql
        assert "vote_source_url_count" in sql
        assert "latest_vote_source_url" in sql
        assert "array_agg(fvc.source_record_id ORDER BY fvc.vote_date DESC, fvc.id DESC)" in sql
        assert "latest_vote_event_key" in sql
        assert "latest_legislative_session_id" in sql

    def test_returns_member_party_and_chamber_for_party_baselines(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_feature_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "m.party" in sql
        assert "m.chamber" in sql

    def test_returns_jurisdiction_neutral_member_context(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_feature_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "'us_congress' AS jurisdiction_id" in sql
        assert "('us_congress_' || m.chamber) AS legislative_body_id" in sql


class TestFetchVotePredictionBacktestLabelRows:
    def test_filters_labels_to_closed_future_window(self):
        label_start = date(2025, 1, 1)
        label_end = date(2026, 4, 25)
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_label_rows(CONN, label_start, label_end)
        sql, params = mock_fa.call_args[0][1], mock_fa.call_args[0][2]
        assert "ve.vote_date >= %s" in sql
        assert "ve.vote_date <= %s" in sql
        assert params == (label_start, label_end)

    def test_returns_vote_event_and_member_label_fields(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_label_rows(
                CONN,
                date(2025, 1, 1),
                date(2026, 4, 25),
            )
        sql = mock_fa.call_args[0][1]
        assert "ve.id AS vote_event_id" in sql
        assert "m.bioguide_id" in sql
        assert "vc.vote_option" in sql
        assert "COALESCE(vc.source_record_id, ve.source_record_id) AS source_url" in sql
        assert "ORDER BY ve.vote_date" in sql

    def test_backtest_label_rows_do_not_filter_to_current_members(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_label_rows(
                CONN,
                date(2025, 1, 1),
                date(2026, 4, 25),
            )
        sql = mock_fa.call_args[0][1]
        assert "m.is_current = true" not in sql

    def test_filters_labels_to_member_terms_active_on_vote_date(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_label_rows(
                CONN,
                date(2025, 1, 1),
                date(2026, 4, 25),
            )
        sql = mock_fa.call_args[0][1]
        assert "(m.current_term_start IS NULL OR m.current_term_start <= ve.vote_date)" in sql
        assert "(m.current_term_end IS NULL OR m.current_term_end >= ve.vote_date)" in sql

    def test_returns_jurisdiction_neutral_vote_event_context(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_label_rows(
                CONN,
                date(2025, 1, 1),
                date(2026, 4, 25),
            )
        sql = mock_fa.call_args[0][1]
        assert "'us_congress' AS jurisdiction_id" in sql
        assert "('us_congress_' || ve.chamber) AS legislative_body_id" in sql
        assert "('congress_' || ve.congress || '_session_' || ve.session_number)" in sql


class TestFetchVotePredictionBacktestBillSignalRows:
    def test_returns_sponsor_and_cosponsor_rows_for_loaded_bills(self):
        cutoff = date(2024, 12, 31)
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_bill_signal_rows(CONN, cutoff)
        sql, params = mock_fa.call_args[0][1], mock_fa.call_args[0][2]
        assert "FROM bill b" in sql
        assert "vote_referenced_bills AS" in sql
        assert "FROM vote_event ve" in sql
        assert "regexp_matches(" in sql
        assert "'https://api.congress.gov/v3/bill/' || ve.congress" in sql
        assert "AS bill_source_url" in sql
        assert "ve.source_record_id AS bill_source_url" not in sql
        assert "NOT EXISTS" in sql
        assert "UNION ALL" in sql
        assert "LEFT JOIN bill_sponsor bs" in sql
        assert "LEFT JOIN member m" in sql
        assert "b.introduced_date" in sql
        assert "b.latest_action_date" in sql
        assert "b.introduced_date <= %s" in sql
        assert "COALESCE(b.introduced_date, b.latest_action_date) <= %s" in sql
        assert "b.latest_action_date <= %s" in sql
        assert "bs.sponsor_date <= %s" in sql
        assert params == (cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff)
        assert "bs.sponsor_role" in sql
        assert "m.bioguide_id AS sponsor_bioguide_id" in sql
        assert "bs.source_record_id AS sponsor_source_url" in sql

    def test_excludes_undated_sponsors_from_cutoff_safe_bill_signals(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_bill_signal_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "AND bs.sponsor_date <= %s" in sql
        assert "bs.sponsor_date IS NULL OR bs.sponsor_date <= %s" not in sql
        assert "bs.sponsor_date IS NULL" not in sql

    def test_sponsor_member_join_requires_term_active_on_sponsor_date(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_bill_signal_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "LEFT JOIN member m" in sql
        assert "FROM member_term mt_sponsor" in sql
        assert "mt_sponsor.member_id = m.id" in sql
        assert "mt_sponsor.start_date <= bs.sponsor_date" in sql
        assert "mt_sponsor.end_date IS NULL OR mt_sponsor.end_date >= bs.sponsor_date" in sql
        assert (
            "NOT EXISTS ( SELECT 1 FROM member_term mt_sponsor_any "
            "WHERE mt_sponsor_any.member_id = m.id )" in " ".join(sql.split())
        )

    def test_excludes_bills_with_only_future_availability_date(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_bill_signal_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "COALESCE(b.introduced_date, b.latest_action_date) <= %s" in sql
        assert "COALESCE(b.introduced_date, b.latest_action_date) IS NOT NULL" in sql

    def test_keeps_pre_cutoff_bills_while_masking_future_latest_action(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_bill_signal_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "AND (b.latest_action_date IS NULL OR b.latest_action_date <= %s)" not in sql
        assert "CASE WHEN b.latest_action_date <= %s THEN b.latest_action_date ELSE NULL END" in sql
        assert "AS latest_action_date" in sql
        assert "CASE WHEN b.latest_action_date <= %s THEN b.current_status ELSE NULL END" in sql
        assert "AS current_status" in sql

    def test_returns_jurisdiction_neutral_bill_context(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_bill_signal_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "'us_congress' AS jurisdiction_id" in sql
        assert "WHEN lower(b.bill_type) IN ('hr', 'hres', 'hjres', 'hconres')" in sql
        assert "THEN 'us_congress_house'" in sql
        assert "WHEN lower(b.bill_type) IN ('s', 'sres', 'sjres', 'sconres')" in sql
        assert "THEN 'us_congress_senate'" in sql
        assert "ELSE 'us_congress'" in sql
        assert "AS legislative_body_id" in sql
        assert "('congress_' || b.congress || '_session_'" in sql
        assert "EXTRACT(" in sql
        assert "YEAR FROM COALESCE(b.introduced_date, b.latest_action_date)" in sql

    def test_bill_signal_session_uses_latest_action_when_introduced_date_missing(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_backtest_bill_signal_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "WHEN COALESCE(b.introduced_date, b.latest_action_date) IS NULL THEN 1" in sql
        assert "YEAR FROM COALESCE(b.introduced_date, b.latest_action_date)" in sql


class TestFetchVotePredictionContributionSignalRows:
    def test_reads_source_backed_cutoff_safe_contribution_edges(self):
        cutoff = date(2024, 12, 31)
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_contribution_signal_rows(CONN, cutoff)
        sql, params = mock_fa.call_args[0][1], mock_fa.call_args[0][2]
        assert "FROM ontology_edge oe" in sql
        assert "member_sector_contribution_exposure" in sql
        assert "source_anchors" in sql
        assert "jsonb_array_elements(oe.source_anchors)" in sql
        assert "AS source_anchors" in sql
        assert "anchor.source_anchor ? 'url'" in sql
        assert "btrim(anchor.source_anchor->>'url') <> ''" in sql
        assert "contribution_date" in sql
        assert "AS contribution_date" in sql
        assert "alignment_score" in sql
        assert "m.bioguide_id AS bioguide_id" in sql
        assert "oe.object_node_id AS sector" in sql
        assert params == (cutoff, *([cutoff] * 9))

    def test_scopes_contribution_edges_to_congress_member_nodes(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_contribution_signal_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "'us_congress' AS jurisdiction_id" in sql
        assert "('us_congress_' || m.chamber) AS legislative_body_id" in sql
        assert "m.bioguide_id AS bioguide_id" in sql
        assert "JOIN member m" in sql
        assert "m.bioguide_id = oe.subject_node_id" in sql
        assert "GROUP BY m.bioguide_id, m.chamber, oe.object_node_id" in sql
        assert "ORDER BY m.bioguide_id, oe.object_node_id" in sql

    def test_contribution_edges_require_member_term_active_on_contribution_date(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_contribution_signal_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "m.current_term_start IS NULL" in sql
        assert "m.current_term_start <= (oe.attributes->>'contribution_date')::date" in sql
        assert "m.current_term_end IS NULL" in sql
        assert "m.current_term_end >= (oe.attributes->>'contribution_date')::date" in sql

    def test_rejects_edges_with_any_future_availability_date(self):
        cutoff = date(2024, 12, 31)
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_contribution_signal_rows(CONN, cutoff)
        sql, params = mock_fa.call_args[0][1], mock_fa.call_args[0][2]
        assert "oe.attributes ? 'filing_date'" in sql
        assert "(oe.attributes->>'filing_date')::date > %s" in sql
        assert "oe.attributes ? 'effective_date'" in sql
        assert "(oe.attributes->>'effective_date')::date > %s" in sql
        assert params.count(cutoff) > 1


class TestFetchVotePredictionStatementSignalRows:
    def test_reads_source_backed_cutoff_safe_statement_edges(self):
        cutoff = date(2024, 12, 31)
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_statement_signal_rows(CONN, cutoff)
        sql, params = mock_fa.call_args[0][1], mock_fa.call_args[0][2]
        assert "FROM ontology_edge oe" in sql
        assert "member_sector_statement_alignment" in sql
        assert "member_sector_public_statement_alignment" in sql
        assert "source_anchors" in sql
        assert "jsonb_array_elements(oe.source_anchors)" in sql
        assert "AS source_anchors" in sql
        assert "anchor.source_anchor ? 'url'" in sql
        assert "btrim(anchor.source_anchor->>'url') <> ''" in sql
        assert "statement_date" in sql
        assert "AS statement_date" in sql
        assert "alignment_score" in sql
        assert "m.bioguide_id AS bioguide_id" in sql
        assert "oe.object_node_id AS sector" in sql
        assert params == (cutoff, *([cutoff] * 9))

    def test_scopes_statement_edges_to_congress_member_nodes(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_statement_signal_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "'us_congress' AS jurisdiction_id" in sql
        assert "('us_congress_' || m.chamber) AS legislative_body_id" in sql
        assert "m.bioguide_id AS bioguide_id" in sql
        assert "JOIN member m" in sql
        assert "m.bioguide_id = oe.subject_node_id" in sql
        assert "GROUP BY m.bioguide_id, m.chamber, oe.object_node_id" in sql
        assert "ORDER BY m.bioguide_id, oe.object_node_id" in sql

    def test_statement_edges_require_member_term_active_on_statement_date(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_statement_signal_rows(CONN, date(2024, 12, 31))
        sql = mock_fa.call_args[0][1]
        assert "m.current_term_start IS NULL" in sql
        assert "m.current_term_start <= (oe.attributes->>'statement_date')::date" in sql
        assert "m.current_term_end IS NULL" in sql
        assert "m.current_term_end >= (oe.attributes->>'statement_date')::date" in sql

    def test_rejects_edges_with_any_future_availability_date(self):
        cutoff = date(2024, 12, 31)
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_vote_prediction_statement_signal_rows(CONN, cutoff)
        sql, params = mock_fa.call_args[0][1], mock_fa.call_args[0][2]
        assert "oe.attributes ? 'filing_date'" in sql
        assert "(oe.attributes->>'filing_date')::date > %s" in sql
        assert "oe.attributes ? 'effective_date'" in sql
        assert "(oe.attributes->>'effective_date')::date > %s" in sql
        assert params.count(cutoff) > 1


class TestFetchBillSemanticInputRows:
    def test_returns_loaded_bills_for_llm_semantic_materialization(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_bill_semantic_input_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "FROM bill b" in sql
        assert "vote_referenced_bills AS" in sql
        assert "FROM vote_event ve" in sql
        assert "regexp_matches(" in sql
        assert "'https://api.congress.gov/v3/bill/' || ve.congress" in sql
        assert "AS bill_source_url" in sql
        assert "ve.source_record_id AS bill_source_url" not in sql
        assert "NOT EXISTS" in sql
        assert "UNION ALL" in sql
        assert "b.congress" in sql
        assert "b.bill_type" in sql
        assert "b.bill_number" in sql
        assert "b.title" in sql
        assert "b.introduced_date" in sql
        assert "b.latest_action_date" in sql
        assert "b.source_record_id AS bill_source_url" in sql
        assert mock_fa.call_args[0][2] == ()

    def test_can_filter_to_cutoff_available_bills_for_inventory(self):
        cutoff = date(2024, 12, 31)
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_bill_semantic_input_rows(CONN, feature_cutoff=cutoff)
        sql, params = mock_fa.call_args[0][1], mock_fa.call_args[0][2]
        assert "WHERE COALESCE(b.introduced_date, b.latest_action_date) <= %s" in sql
        assert "AND COALESCE(b.introduced_date, b.latest_action_date) IS NOT NULL" in sql
        assert "AND ve.vote_date <= %s" in sql
        assert params == (cutoff, cutoff, cutoff, cutoff, cutoff)

    def test_masks_future_bill_action_fields_for_cutoff_semantics(self):
        cutoff = date(2024, 12, 31)
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_bill_semantic_input_rows(CONN, feature_cutoff=cutoff)
        sql = mock_fa.call_args[0][1]
        assert "CASE WHEN b.latest_action_date <= %s THEN b.latest_action_date ELSE NULL END" in sql
        assert "AS latest_action_date" in sql
        assert "CASE WHEN b.latest_action_date <= %s THEN b.current_status ELSE NULL END" in sql
        assert "AS current_status" in sql

    def test_returns_jurisdiction_neutral_bill_context(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_bill_semantic_input_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "'us_congress' AS jurisdiction_id" in sql
        assert "WHEN lower(b.bill_type) IN ('hr', 'hres', 'hjres', 'hconres')" in sql
        assert "THEN 'us_congress_house'" in sql
        assert "WHEN lower(b.bill_type) IN ('s', 'sres', 'sjres', 'sconres')" in sql
        assert "THEN 'us_congress_senate'" in sql
        assert "ELSE 'us_congress'" in sql
        assert "AS legislative_body_id" in sql
        assert "('congress_' || b.congress || '_session_'" in sql
        assert "EXTRACT(" in sql
        assert "YEAR FROM COALESCE(b.introduced_date, b.latest_action_date)" in sql

    def test_bill_semantic_session_uses_latest_action_when_introduced_date_missing(self):
        with patch(MODULE, return_value=[]) as mock_fa:
            fetch_bill_semantic_input_rows(CONN)
        sql = mock_fa.call_args[0][1]
        assert "WHEN COALESCE(b.introduced_date, b.latest_action_date) IS NULL THEN 1" in sql
        assert "YEAR FROM COALESCE(b.introduced_date, b.latest_action_date)" in sql
