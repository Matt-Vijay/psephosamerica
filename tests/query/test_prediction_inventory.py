from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

from src.query.prediction_inventory import fetch_prediction_fec_inventory


def test_fetch_prediction_fec_inventory_joins_fec_linkage_to_members() -> None:
    conn = MagicMock()
    with (
        patch(
            "src.query.prediction_inventory.fetch_all",
            side_effect=[
                [{"exists": True}],
                [
                    {
                        "fec_contribution_count": 10,
                        "member_attributed_fec_contribution_count": 5,
                        "members_with_fec_candidate_id_count": 2,
                        "public_statement_signal_count": 4,
                        "members_with_public_statement_signal_count": 3,
                    }
                ],
            ],
        ) as fetch,
        patch("src.db.repositories.fetch_all", fetch),
    ):
        result = fetch_prediction_fec_inventory(conn, feature_cutoff=dt.date(2024, 12, 31))

    _, sql, params = fetch.call_args_list[1].args
    assert "FROM contribution c" in sql
    assert "WHERE c.contribution_date <= %s" in sql
    assert "JOIN fec_candidate_committee_linkage fcl" in sql
    assert "JOIN member m" in sql
    assert "m.fec_candidate_id = fcl.fec_candidate_id" in sql
    assert "fec_candidate_id IS NOT NULL" in sql
    assert "member_sector_public_statement_alignment" in sql
    assert "jsonb_array_length(oe.source_anchors) > 0" in sql
    assert "oe.attributes ? 'statement_date'" in sql
    assert "(oe.attributes->>'statement_date')::date <= %s" in sql
    assert "COUNT(DISTINCT oe.subject_node_id)" in sql
    assert params == (
        dt.date(2024, 12, 31),
        dt.date(2024, 12, 31),
        dt.date(2024, 12, 31),
        dt.date(2024, 12, 31),
    )
    assert result["fec_contribution_count"] == 10
    assert result["public_statement_signal_count"] == 4


def test_fetch_prediction_fec_inventory_counts_only_term_active_attributed_contributions() -> None:
    conn = MagicMock()
    with (
        patch(
            "src.query.prediction_inventory.fetch_all",
            side_effect=[
                [{"exists": True}],
                [
                    {
                        "fec_contribution_count": 10,
                        "member_attributed_fec_contribution_count": 5,
                        "members_with_fec_candidate_id_count": 2,
                        "public_statement_signal_count": 4,
                        "members_with_public_statement_signal_count": 3,
                    }
                ],
            ],
        ) as fetch,
        patch("src.db.repositories.fetch_all", fetch),
    ):
        fetch_prediction_fec_inventory(conn, feature_cutoff=dt.date(2024, 12, 31))

    _, sql, _ = fetch.call_args_list[1].args
    normalized_sql = " ".join(sql.split())
    assert "FROM member_term mt" in sql
    assert "mt.member_id = m.id" in sql
    assert "mt.start_date <= c.contribution_date" in sql
    assert "mt.end_date IS NULL OR mt.end_date >= c.contribution_date" in normalized_sql
    assert (
        "NOT EXISTS ( SELECT 1 FROM member_term mt_any WHERE mt_any.member_id = m.id )"
        in normalized_sql
    )
    assert (
        "m.current_term_start IS NULL OR m.current_term_start <= c.contribution_date"
        in normalized_sql
    )
    assert (
        "m.current_term_end IS NULL OR m.current_term_end >= c.contribution_date" in normalized_sql
    )


def test_fetch_prediction_fec_inventory_counts_only_term_active_public_statement_edges() -> None:
    conn = MagicMock()
    with (
        patch(
            "src.query.prediction_inventory.fetch_all",
            side_effect=[
                [{"exists": True}],
                [
                    {
                        "fec_contribution_count": 10,
                        "member_attributed_fec_contribution_count": 5,
                        "members_with_fec_candidate_id_count": 2,
                        "public_statement_signal_count": 4,
                        "members_with_public_statement_signal_count": 3,
                    }
                ],
            ],
        ) as fetch,
        patch("src.db.repositories.fetch_all", fetch),
    ):
        fetch_prediction_fec_inventory(conn, feature_cutoff=dt.date(2024, 12, 31))

    _, sql, _ = fetch.call_args_list[1].args
    normalized_sql = " ".join(sql.split())
    assert "JOIN member statement_member" in sql
    assert "statement_member.bioguide_id = oe.subject_node_id" in sql
    assert "oe.subject_node_type = 'member'" in sql
    assert "FROM member_term statement_member_term" in sql
    assert "statement_member_term.member_id = statement_member.id" in sql
    assert "statement_member_term.start_date <= (oe.attributes->>'statement_date')::date" in sql
    assert (
        "statement_member_term.end_date IS NULL OR "
        "statement_member_term.end_date >= (oe.attributes->>'statement_date')::date"
    ) in normalized_sql
    assert (
        "NOT EXISTS (SELECT 1 FROM member_term statement_member_term_any "
        "WHERE statement_member_term_any.member_id = statement_member.id)"
    ).replace("(SELECT", "( SELECT").replace("id)", "id )") in normalized_sql
    assert "statement_member.current_term_start <= (oe.attributes->>'statement_date')::date" in sql
    assert "statement_member.current_term_end IS NULL" in sql
    assert "statement_member.current_term_end >= (oe.attributes->>'statement_date')::date" in sql


def test_fetch_prediction_fec_inventory_survives_missing_fec_linkage_table() -> None:
    conn = MagicMock()
    with (
        patch(
            "src.query.prediction_inventory.fetch_all",
            side_effect=[
                [{"exists": False}],
                [
                    {
                        "fec_contribution_count": 10,
                        "member_attributed_fec_contribution_count": 0,
                        "members_with_fec_candidate_id_count": 2,
                        "public_statement_signal_count": 4,
                        "members_with_public_statement_signal_count": 3,
                    }
                ],
            ],
        ) as fetch,
        patch("src.db.repositories.fetch_all", fetch),
    ):
        result = fetch_prediction_fec_inventory(conn, feature_cutoff=dt.date(2024, 12, 31))

    _, sql, params = fetch.call_args_list[1].args
    assert "JOIN fec_candidate_committee_linkage" not in sql
    assert "WHERE contribution_date <= %s" in sql
    assert "jsonb_array_length(oe.source_anchors) > 0" in sql
    assert "(oe.attributes->>'statement_date')::date <= %s" in sql
    assert params == (
        dt.date(2024, 12, 31),
        dt.date(2024, 12, 31),
        dt.date(2024, 12, 31),
    )
    assert result["fec_contribution_count"] == 10
    assert result["member_attributed_fec_contribution_count"] == 0


def test_fetch_prediction_fec_inventory_without_linkage_counts_only_term_active_public_statement_edges() -> (
    None
):
    conn = MagicMock()
    with (
        patch(
            "src.query.prediction_inventory.fetch_all",
            side_effect=[
                [{"exists": False}],
                [
                    {
                        "fec_contribution_count": 10,
                        "member_attributed_fec_contribution_count": 0,
                        "members_with_fec_candidate_id_count": 2,
                        "public_statement_signal_count": 4,
                        "members_with_public_statement_signal_count": 3,
                    }
                ],
            ],
        ) as fetch,
        patch("src.db.repositories.fetch_all", fetch),
    ):
        fetch_prediction_fec_inventory(conn, feature_cutoff=dt.date(2024, 12, 31))

    _, sql, _ = fetch.call_args_list[1].args
    assert "JOIN fec_candidate_committee_linkage" not in sql
    assert "JOIN member statement_member" in sql
    assert "statement_member.bioguide_id = oe.subject_node_id" in sql
    assert "oe.subject_node_type = 'member'" in sql
    assert "statement_member.current_term_start IS NULL" in sql
    assert "statement_member.current_term_start <= (oe.attributes->>'statement_date')::date" in sql
    assert "statement_member.current_term_end IS NULL" in sql
    assert "statement_member.current_term_end >= (oe.attributes->>'statement_date')::date" in sql


# --- inventory query selection + relation existence helpers ---


def test_statement_signal_inventory_query_picks_sql_and_params_by_linkage_and_cutoff() -> None:
    from src.query.prediction_inventory import (
        _FEC_INVENTORY_CUTOFF_SQL,
        _FEC_INVENTORY_SQL,
        _FEC_INVENTORY_WITHOUT_LINKAGE_CUTOFF_SQL,
        _FEC_INVENTORY_WITHOUT_LINKAGE_SQL,
        _statement_signal_inventory_query,
    )

    assert _statement_signal_inventory_query(has_linkage=True, feature_cutoff=None) == (
        _FEC_INVENTORY_SQL,
        (),
    )
    sql, params = _statement_signal_inventory_query(
        has_linkage=True, feature_cutoff=dt.date(2024, 1, 1)
    )
    assert sql == _FEC_INVENTORY_CUTOFF_SQL
    assert params == (dt.date(2024, 1, 1),) * 4

    assert _statement_signal_inventory_query(has_linkage=False, feature_cutoff=None) == (
        _FEC_INVENTORY_WITHOUT_LINKAGE_SQL,
        (),
    )
    sql2, params2 = _statement_signal_inventory_query(
        has_linkage=False, feature_cutoff=dt.date(2024, 1, 1)
    )
    assert sql2 == _FEC_INVENTORY_WITHOUT_LINKAGE_CUTOFF_SQL
    assert params2 == (dt.date(2024, 1, 1),) * 3


def test_relation_exists_handles_empty_rows_and_flag() -> None:
    from src.db.repositories import relation_exists

    with patch("src.db.repositories.fetch_all", return_value=[]):
        assert relation_exists(MagicMock(), "missing") is False
    with patch("src.db.repositories.fetch_all", return_value=[{"exists": True}]):
        assert relation_exists(MagicMock(), "present") is True
    with patch("src.db.repositories.fetch_all", return_value=[{"exists": False}]):
        assert relation_exists(MagicMock(), "present") is False
