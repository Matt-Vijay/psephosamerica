from __future__ import annotations

from typing import Any

from src.db.repositories import ConnectionLike, fetch_all
from src.query.member_terms import member_active_on_sql

_FEC_INVENTORY_SQL = f"""
        WITH attributed AS (
            SELECT COUNT(c.id) AS member_attributed_fec_contribution_count
              FROM contribution c
              JOIN fec_committee fc ON fc.id = c.recipient_fec_committee_id
              JOIN fec_candidate_committee_linkage fcl
                ON fcl.fec_committee_id = fc.fec_committee_id
              JOIN member m
                ON m.fec_candidate_id = fcl.fec_candidate_id
{member_active_on_sql("m", "c.contribution_date", "mt")}
        )
        SELECT
            (SELECT COUNT(*) FROM contribution) AS fec_contribution_count,
            (SELECT member_attributed_fec_contribution_count FROM attributed)
                AS member_attributed_fec_contribution_count,
            (SELECT COUNT(*) FROM member WHERE fec_candidate_id IS NOT NULL)
                AS members_with_fec_candidate_id_count,
            (
                SELECT COUNT(*)
                  FROM ontology_edge oe
                  JOIN member statement_member
                    ON statement_member.bioguide_id = oe.subject_node_id
{member_active_on_sql("statement_member", "(oe.attributes->>'statement_date')::date", "statement_member_term")}
                 WHERE oe.edge_type IN (
                    'member_sector_statement_alignment',
                    'member_sector_public_statement_alignment'
                 )
                   AND oe.subject_node_type = 'member'
                   AND jsonb_array_length(oe.source_anchors) > 0
                   AND oe.attributes ? 'statement_date'
            ) AS public_statement_signal_count,
            (
                SELECT COUNT(DISTINCT oe.subject_node_id)
                  FROM ontology_edge oe
                  JOIN member statement_member
                    ON statement_member.bioguide_id = oe.subject_node_id
{member_active_on_sql("statement_member", "(oe.attributes->>'statement_date')::date", "statement_member_term")}
                 WHERE oe.edge_type IN (
                    'member_sector_statement_alignment',
                    'member_sector_public_statement_alignment'
                 )
                   AND oe.subject_node_type = 'member'
                   AND jsonb_array_length(oe.source_anchors) > 0
                   AND oe.attributes ? 'statement_date'
            ) AS members_with_public_statement_signal_count
"""  # nosec B608

_FEC_INVENTORY_CUTOFF_SQL = f"""
        WITH attributed AS (
            SELECT COUNT(c.id) AS member_attributed_fec_contribution_count
              FROM contribution c
              JOIN fec_committee fc ON fc.id = c.recipient_fec_committee_id
              JOIN fec_candidate_committee_linkage fcl
                ON fcl.fec_committee_id = fc.fec_committee_id
              JOIN member m
                ON m.fec_candidate_id = fcl.fec_candidate_id
{member_active_on_sql("m", "c.contribution_date", "mt")}
             WHERE c.contribution_date <= %s
        )
        SELECT
            (SELECT COUNT(*) FROM contribution WHERE contribution_date <= %s)
                AS fec_contribution_count,
            (SELECT member_attributed_fec_contribution_count FROM attributed)
                AS member_attributed_fec_contribution_count,
            (SELECT COUNT(*) FROM member WHERE fec_candidate_id IS NOT NULL)
                AS members_with_fec_candidate_id_count,
            (
                SELECT COUNT(*)
                  FROM ontology_edge oe
                  JOIN member statement_member
                    ON statement_member.bioguide_id = oe.subject_node_id
{member_active_on_sql("statement_member", "(oe.attributes->>'statement_date')::date", "statement_member_term")}
                 WHERE oe.edge_type IN (
                    'member_sector_statement_alignment',
                    'member_sector_public_statement_alignment'
                 )
                   AND oe.subject_node_type = 'member'
                   AND jsonb_array_length(oe.source_anchors) > 0
                   AND oe.attributes ? 'statement_date'
                   AND (oe.attributes->>'statement_date')::date <= %s
            ) AS public_statement_signal_count,
            (
                SELECT COUNT(DISTINCT oe.subject_node_id)
                  FROM ontology_edge oe
                  JOIN member statement_member
                    ON statement_member.bioguide_id = oe.subject_node_id
{member_active_on_sql("statement_member", "(oe.attributes->>'statement_date')::date", "statement_member_term")}
                 WHERE oe.edge_type IN (
                    'member_sector_statement_alignment',
                    'member_sector_public_statement_alignment'
                 )
                   AND oe.subject_node_type = 'member'
                   AND jsonb_array_length(oe.source_anchors) > 0
                   AND oe.attributes ? 'statement_date'
                   AND (oe.attributes->>'statement_date')::date <= %s
            ) AS members_with_public_statement_signal_count
"""  # nosec B608

_FEC_INVENTORY_WITHOUT_LINKAGE_SQL = f"""
        SELECT
            (SELECT COUNT(*) FROM contribution) AS fec_contribution_count,
            0 AS member_attributed_fec_contribution_count,
            (SELECT COUNT(*) FROM member WHERE fec_candidate_id IS NOT NULL)
                AS members_with_fec_candidate_id_count,
            (
                SELECT COUNT(*)
                  FROM ontology_edge oe
                  JOIN member statement_member
                    ON statement_member.bioguide_id = oe.subject_node_id
{member_active_on_sql("statement_member", "(oe.attributes->>'statement_date')::date", "statement_member_term")}
                 WHERE oe.edge_type IN (
                    'member_sector_statement_alignment',
                    'member_sector_public_statement_alignment'
                 )
                   AND oe.subject_node_type = 'member'
                   AND jsonb_array_length(oe.source_anchors) > 0
                   AND oe.attributes ? 'statement_date'
            ) AS public_statement_signal_count,
            (
                SELECT COUNT(DISTINCT oe.subject_node_id)
                  FROM ontology_edge oe
                  JOIN member statement_member
                    ON statement_member.bioguide_id = oe.subject_node_id
{member_active_on_sql("statement_member", "(oe.attributes->>'statement_date')::date", "statement_member_term")}
                 WHERE oe.edge_type IN (
                    'member_sector_statement_alignment',
                    'member_sector_public_statement_alignment'
                 )
                   AND oe.subject_node_type = 'member'
                   AND jsonb_array_length(oe.source_anchors) > 0
                   AND oe.attributes ? 'statement_date'
            ) AS members_with_public_statement_signal_count
"""  # nosec B608

_FEC_INVENTORY_WITHOUT_LINKAGE_CUTOFF_SQL = f"""
        SELECT
            (SELECT COUNT(*) FROM contribution WHERE contribution_date <= %s)
                AS fec_contribution_count,
            0 AS member_attributed_fec_contribution_count,
            (SELECT COUNT(*) FROM member WHERE fec_candidate_id IS NOT NULL)
                AS members_with_fec_candidate_id_count,
            (
                SELECT COUNT(*)
                  FROM ontology_edge oe
                  JOIN member statement_member
                    ON statement_member.bioguide_id = oe.subject_node_id
{member_active_on_sql("statement_member", "(oe.attributes->>'statement_date')::date", "statement_member_term")}
                 WHERE oe.edge_type IN (
                    'member_sector_statement_alignment',
                    'member_sector_public_statement_alignment'
                 )
                   AND oe.subject_node_type = 'member'
                   AND jsonb_array_length(oe.source_anchors) > 0
                   AND oe.attributes ? 'statement_date'
                   AND (oe.attributes->>'statement_date')::date <= %s
            ) AS public_statement_signal_count,
            (
                SELECT COUNT(DISTINCT oe.subject_node_id)
                  FROM ontology_edge oe
                  JOIN member statement_member
                    ON statement_member.bioguide_id = oe.subject_node_id
{member_active_on_sql("statement_member", "(oe.attributes->>'statement_date')::date", "statement_member_term")}
                 WHERE oe.edge_type IN (
                    'member_sector_statement_alignment',
                    'member_sector_public_statement_alignment'
                 )
                   AND oe.subject_node_type = 'member'
                   AND jsonb_array_length(oe.source_anchors) > 0
                   AND oe.attributes ? 'statement_date'
                   AND (oe.attributes->>'statement_date')::date <= %s
            ) AS members_with_public_statement_signal_count
"""  # nosec B608


def fetch_prediction_fec_inventory(
    conn: ConnectionLike,
    *,
    feature_cutoff: Any | None = None,
) -> dict[str, Any]:
    if not _relation_exists(conn, "fec_candidate_committee_linkage"):
        return _fetch_prediction_fec_inventory_without_fec_linkage(
            conn,
            feature_cutoff=feature_cutoff,
        )
    sql, statement_params = _statement_signal_inventory_query(
        has_linkage=True,
        feature_cutoff=feature_cutoff,
    )
    rows = fetch_all(
        conn,
        sql,
        statement_params,
    )
    return rows[0] if rows else {}


def _relation_exists(conn: ConnectionLike, relation_name: str) -> bool:
    rows = fetch_all(
        conn,
        "SELECT to_regclass(%s) IS NOT NULL AS exists",
        (relation_name,),
    )
    if not rows:
        return False
    return bool(rows[0].get("exists"))


def _fetch_prediction_fec_inventory_without_fec_linkage(
    conn: ConnectionLike,
    *,
    feature_cutoff: Any | None = None,
) -> dict[str, Any]:
    sql, statement_params = _statement_signal_inventory_query(
        has_linkage=False,
        feature_cutoff=feature_cutoff,
    )
    rows = fetch_all(
        conn,
        sql,
        statement_params,
    )
    return rows[0] if rows else {}


def _statement_signal_inventory_query(
    *,
    has_linkage: bool,
    feature_cutoff: Any | None,
) -> tuple[str, tuple[Any, ...]]:
    if has_linkage and feature_cutoff is None:
        return _FEC_INVENTORY_SQL, ()
    if has_linkage:
        return _FEC_INVENTORY_CUTOFF_SQL, (
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
        )
    if feature_cutoff is None:
        return _FEC_INVENTORY_WITHOUT_LINKAGE_SQL, ()
    return _FEC_INVENTORY_WITHOUT_LINKAGE_CUTOFF_SQL, (
        feature_cutoff,
        feature_cutoff,
        feature_cutoff,
    )
