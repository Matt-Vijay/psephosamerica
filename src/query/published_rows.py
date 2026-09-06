"""DB-backed row-fetch helpers for the published read side.

Each function returns plain dicts from fetch_all (or a single dict / None).
No payload assembly here — callers do that.
"""

from __future__ import annotations

from typing import Any

from src.db.repositories import ConnectionLike, fetch_all
from src.query.member_terms import member_active_on_sql

_ONTOLOGY_EDGE_AVAILABILITY_DATE_KEYS = (
    "transaction_date",
    "contribution_date",
    "start_date",
    "committee_start_date",
    "filing_date",
    "filed_at",
    "report_date",
    "statement_date",
    "effective_date",
)


def _ontology_edge_availability_cutoff_sql(alias: str = "oe") -> str:
    return "\n".join(
        f"           AND NOT ({alias}.attributes ? '{key}' "
        f"AND ({alias}.attributes->>'{key}')::date > %s)"
        for key in _ONTOLOGY_EDGE_AVAILABILITY_DATE_KEYS
    )


def _ontology_edge_availability_cutoff_params(feature_cutoff: Any) -> tuple[Any, ...]:
    return tuple(feature_cutoff for _ in _ONTOLOGY_EDGE_AVAILABILITY_DATE_KEYS)


def fetch_current_member_slugs(conn: ConnectionLike) -> list[str]:
    rows = fetch_all(
        conn,
        """
        SELECT slug
          FROM member
         WHERE is_current = true
         ORDER BY chamber, state, last_name, first_name, slug
        """,
    )
    return [row["slug"] for row in rows]


def fetch_member_row_by_slug(conn: ConnectionLike, slug: str) -> dict[str, Any] | None:
    rows = fetch_all(
        conn,
        """
        SELECT id, bioguide_id, slug, full_name, first_name, last_name,
               party, state, chamber, current_term_start, current_term_end,
               is_current
          FROM member
         WHERE slug = %s
         LIMIT 1
        """,
        (slug,),
    )
    return rows[0] if rows else None


def fetch_member_score_snapshot_rows(conn: ConnectionLike, member_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT id, member_id, snapshot_at, score_total, dimension_scores,
               published_at
          FROM score_snapshot
         WHERE member_id = %s
         ORDER BY snapshot_at DESC
        """,
        (member_id,),
    )


def fetch_member_rule_fire_rows(conn: ConnectionLike, member_id: int) -> list[dict[str, Any]]:
    """Rule fires joined to their evidence card for member-profile surfaces.

    Columns returned:
      rule_id, dimension, severity, explanation, fired_at,
      evidence_card_id (public_id), short_explanation, score_delta,
      snapshot_date (rendered_at date, proxies snapshot context).
    """
    return fetch_all(
        conn,
        """
        SELECT rf.id,
               rf.rule_id,
               rf.dimension,
               rf.severity,
               rf.explanation,
               rf.fired_at,
               ec.public_id   AS evidence_card_id,
               ec.short_explanation,
               ec.score_delta,
               ec.rendered_at::date AS snapshot_date
          FROM rule_fire rf
          JOIN evidence_card ec ON ec.rule_fire_id = rf.id
         WHERE rf.subject_member_id = %s
         ORDER BY rf.fired_at DESC
        """,
        (member_id,),
    )


def fetch_member_committee_rows(conn: ConnectionLike, member_id: int) -> list[dict[str, Any]]:
    """Current and historical committee memberships with committee metadata."""
    return fetch_all(
        conn,
        """
        SELECT cm.id,
               cm.role,
               cm.start_date,
               cm.end_date,
               cm.is_current,
               c.name  AS committee_name,
               c.chamber,
               c.committee_type,
               c.review_tier
          FROM committee_membership cm
          JOIN committee c ON c.id = cm.committee_id
         WHERE cm.member_id = %s
         ORDER BY cm.is_current DESC, cm.start_date DESC
        """,
        (member_id,),
    )


def fetch_evidence_card_row(conn: ConnectionLike, public_id: str) -> dict[str, Any] | None:
    rows = fetch_all(
        conn,
        """
        SELECT ec.id,
               ec.public_id,
               ec.member_id,
               ec.dimension,
               ec.score_delta,
               ec.short_explanation,
               ec.source_anchors,
               ec.facts,
               ec.inferences,
               ec.normative_judgments,
               ec.confidence_label,
               ec.member_page_slug,
               ec.rendered_at,
               ec.created_at,
               m.bioguide_id AS member_bioguide_id,
               m.full_name  AS member_full_name,
               m.slug       AS member_slug,
               m.state,
               m.chamber,
               m.party,
               rf.rule_id,
               rf.rule_version
          FROM evidence_card ec
          JOIN member m ON m.id = ec.member_id
          JOIN rule_fire rf ON rf.id = ec.rule_fire_id
         WHERE ec.public_id = %s
         LIMIT 1
        """,
        (public_id,),
    )
    return rows[0] if rows else None


def fetch_all_evidence_card_rows(conn: ConnectionLike) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT ec.id,
               ec.public_id,
               ec.dimension,
               ec.score_delta,
               ec.short_explanation,
               ec.source_anchors,
               ec.facts,
               ec.inferences,
               ec.normative_judgments,
               ec.confidence_label,
               ec.rendered_at,
               ec.created_at,
               m.bioguide_id AS member_bioguide_id,
               m.full_name   AS member_full_name,
               m.slug        AS member_slug,
               rf.rule_id,
               rf.rule_version
          FROM evidence_card ec
          JOIN member m ON m.id = ec.member_id
          JOIN rule_fire rf ON rf.id = ec.rule_fire_id
         ORDER BY ec.created_at DESC, ec.public_id
        """,
    )


def fetch_all_ontology_edge_rows(conn: ConnectionLike) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT edge_id,
               edge_type,
               subject_node_type,
               subject_node_id,
               subject_node_label,
               object_node_type,
               object_node_id,
               object_node_label,
               source_anchors,
               confidence,
               attributes
          FROM ontology_edge
         ORDER BY edge_type, subject_node_type, subject_node_id, object_node_type, object_node_id, edge_id
        """,
    )


def fetch_vote_prediction_readiness_rows(conn: ConnectionLike) -> list[dict[str, Any]]:
    """Per-current-member vote history counts for prediction readiness."""
    return fetch_all(
        conn,
        f"""
        WITH vote_event_total AS (
            SELECT COUNT(*) AS vote_event_count
              FROM vote_event
        )
        SELECT 'us_congress' AS jurisdiction_id,
               ('us_congress_' || m.chamber) AS legislative_body_id,
               (array_agg(
                   ('congress_' || ve.congress || '_session_' || ve.session_number)
                   ORDER BY ve.vote_date DESC, ve.id DESC
               ) FILTER (WHERE ve.id IS NOT NULL))[1] AS legislative_session_id,
               m.chamber,
               m.bioguide_id,
               COUNT(ve.id) AS vote_count,
               COUNT(ve.id) FILTER (WHERE vc.vote_option = 'yea') AS yea_count,
               COUNT(ve.id) FILTER (WHERE vc.vote_option = 'nay') AS nay_count,
               COUNT(ve.id) FILTER (WHERE vc.vote_option = 'present') AS present_count,
               COUNT(ve.id) FILTER (WHERE vc.vote_option = 'not_voting') AS not_voting_count,
               MAX(ve.vote_date) AS latest_vote_date,
               vet.vote_event_count AS vote_event_count
          FROM member m
         CROSS JOIN vote_event_total vet
          LEFT JOIN vote_cast vc
            ON vc.member_id = m.id
          LEFT JOIN vote_event ve
            ON ve.id = vc.vote_event_id
{member_active_on_sql("m", "ve.vote_date", "mt_readiness")}
         WHERE m.is_current = true
         GROUP BY m.bioguide_id, m.chamber, vet.vote_event_count
         ORDER BY m.bioguide_id
        """,  # nosec B608
    )


def fetch_vote_prediction_backtest_feature_rows(
    conn: ConnectionLike,
    feature_cutoff: Any,
) -> list[dict[str, Any]]:
    """Per-member vote counts using only votes at or before ``feature_cutoff``."""
    return fetch_all(
        conn,
        f"""
        WITH feature_vote_cast AS (
            SELECT vc.id,
                   vc.member_id,
                   vc.vote_option,
                   ve.vote_date,
                   COALESCE(vc.source_record_id, ve.source_record_id) AS source_record_id,
                   ('congress_' || ve.congress || '_session_' || ve.session_number)
                       AS legislative_session_id,
                   (ve.chamber || '-' || ve.congress || '-' || ve.session_number || '-' ||
                        ve.roll_call_number
                   ) AS vote_event_key
              FROM vote_cast vc
              JOIN vote_event ve ON ve.id = vc.vote_event_id
             WHERE ve.vote_date <= %s
        ),
        vote_event_total AS (
            SELECT COUNT(*) AS vote_event_count
              FROM vote_event
             WHERE vote_date <= %s
        )
        SELECT 'us_congress' AS jurisdiction_id,
               ('us_congress_' || m.chamber) AS legislative_body_id,
               m.bioguide_id,
               m.party,
               m.chamber,
               COUNT(fvc.id) AS vote_count,
               COUNT(fvc.id) FILTER (WHERE fvc.vote_option = 'yea') AS yea_count,
               COUNT(fvc.id) FILTER (WHERE fvc.vote_option = 'nay') AS nay_count,
               COUNT(fvc.id) FILTER (WHERE fvc.vote_option = 'present') AS present_count,
               COUNT(fvc.id) FILTER (WHERE fvc.vote_option = 'not_voting') AS not_voting_count,
               MAX(fvc.vote_date) AS latest_vote_date,
               COUNT(DISTINCT fvc.source_record_id)
                   FILTER (WHERE fvc.source_record_id IS NOT NULL)
                   AS vote_source_url_count,
               (array_agg(fvc.source_record_id ORDER BY fvc.vote_date DESC, fvc.id DESC)
                   FILTER (WHERE fvc.source_record_id IS NOT NULL))[1]
                   AS latest_vote_source_url,
               (array_agg(fvc.legislative_session_id ORDER BY fvc.vote_date DESC, fvc.id DESC)
                    FILTER (WHERE fvc.legislative_session_id IS NOT NULL))[1]
                   AS latest_legislative_session_id,
               (array_agg(fvc.vote_event_key ORDER BY fvc.vote_date DESC, fvc.id DESC)
                    FILTER (WHERE fvc.vote_event_key IS NOT NULL))[1]
                   AS latest_vote_event_key,
               vet.vote_event_count AS vote_event_count
         FROM member m
         CROSS JOIN vote_event_total vet
          LEFT JOIN feature_vote_cast fvc
            ON fvc.member_id = m.id
{member_active_on_sql("m", "fvc.vote_date", "mt")}
         WHERE true
{member_active_on_sql("m", "%s", "mt_cutoff")}
         GROUP BY m.bioguide_id, m.party, m.chamber, vet.vote_event_count
         ORDER BY m.bioguide_id
        """,  # nosec B608
        (
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
        ),
    )


def fetch_vote_prediction_backtest_label_rows(
    conn: ConnectionLike,
    label_start: Any,
    label_end: Any,
) -> list[dict[str, Any]]:
    """Vote-cast labels in the evaluation window for backtesting."""
    return fetch_all(
        conn,
        f"""
        SELECT 'us_congress' AS jurisdiction_id,
               ('us_congress_' || ve.chamber) AS legislative_body_id,
               ('congress_' || ve.congress || '_session_' || ve.session_number)
                   AS legislative_session_id,
               ve.id AS vote_event_id,
               ve.chamber,
               ve.congress,
               ve.session_number,
               ve.roll_call_number,
               ve.vote_date,
               ve.question,
               ve.result,
               COALESCE(vc.source_record_id, ve.source_record_id) AS source_url,
               m.bioguide_id,
               m.slug AS member_slug,
               m.full_name AS member_name,
               m.party,
               m.state,
               vc.vote_option
          FROM vote_cast vc
          JOIN vote_event ve ON ve.id = vc.vote_event_id
         JOIN member m ON m.id = vc.member_id
         WHERE ve.vote_date >= %s
           AND ve.vote_date <= %s
{member_active_on_sql("m", "ve.vote_date", "mt_label")}
         ORDER BY ve.vote_date,
                  ve.chamber,
                  ve.roll_call_number,
                  m.bioguide_id
        """,  # nosec B608
        (label_start, label_end),
    )


def fetch_vote_prediction_backtest_bill_signal_rows(
    conn: ConnectionLike,
    feature_cutoff: Any,
) -> list[dict[str, Any]]:
    """Bill sponsor/cosponsor context used by ontology-aware prediction backtests."""
    return fetch_all(
        conn,
        f"""
        WITH loaded_bill_keys AS (
            SELECT b.congress,
                   lower(b.bill_type) AS bill_type,
                   b.bill_number
              FROM bill b
             WHERE COALESCE(b.introduced_date, b.latest_action_date) <= %s
               AND COALESCE(b.introduced_date, b.latest_action_date) IS NOT NULL
        ),
        vote_referenced_bills AS (
            SELECT DISTINCT
                   'us_congress' AS jurisdiction_id,
                   ('us_congress_' || ve.chamber) AS legislative_body_id,
                   ('congress_' || ve.congress || '_session_' || ve.session_number)
                       AS legislative_session_id,
                   ve.congress,
                   regexp_replace(lower(ref.match[1]), '[^a-z]', '', 'g') AS bill_type,
                   ref.match[2]::int AS bill_number,
                   ve.question AS title,
                   NULL::text AS short_title,
                   ve.vote_date AS introduced_date,
                   ve.vote_date AS latest_action_date,
                   ve.result AS current_status,
                   ('https://api.congress.gov/v3/bill/' || ve.congress || '/' ||
                        regexp_replace(lower(ref.match[1]), '[^a-z]', '', 'g') || '/' ||
                        ref.match[2] || '?format=json'
                   ) AS bill_source_url,
                   NULL::text AS sponsor_bioguide_id,
                   NULL::text AS sponsor_party,
                   NULL::text AS sponsor_role,
                   NULL::boolean AS is_primary,
                   NULL::date AS sponsor_date,
                   NULL::text AS sponsor_source_url
              FROM vote_event ve
             CROSS JOIN LATERAL regexp_matches(
                   COALESCE(ve.question, '') || ' ' || COALESCE(ve.result, ''),
                   '\\m(H\\.?\\s*R\\.?|S\\.?|H\\.?\\s*J\\.?\\s*RES\\.?|S\\.?\\s*J\\.?\\s*RES\\.?|H\\.?\\s*CON\\.?\\s*RES\\.?|S\\.?\\s*CON\\.?\\s*RES\\.?|H\\.?\\s*RES\\.?|S\\.?\\s*RES\\.?)\\s*([0-9]+)\\M',
                   'i'
             ) AS ref(match)
             WHERE ve.vote_date <= %s
               AND ve.source_record_id IS NOT NULL
               AND NOT EXISTS (
                    SELECT 1
                      FROM loaded_bill_keys lb
                     WHERE lb.congress = ve.congress
                       AND lb.bill_type = regexp_replace(
                           lower(ref.match[1]),
                           '[^a-z]',
                           '',
                           'g'
                       )
                       AND lb.bill_number = ref.match[2]::int
               )
        )
        SELECT 'us_congress' AS jurisdiction_id,
               CASE
                   WHEN lower(b.bill_type) IN ('hr', 'hres', 'hjres', 'hconres')
                       THEN 'us_congress_house'
                   WHEN lower(b.bill_type) IN ('s', 'sres', 'sjres', 'sconres')
                       THEN 'us_congress_senate'
                   ELSE 'us_congress'
               END AS legislative_body_id,
               ('congress_' || b.congress || '_session_' ||
                    CASE
                        WHEN COALESCE(b.introduced_date, b.latest_action_date) IS NULL THEN 1
                        WHEN EXTRACT(
                            YEAR FROM COALESCE(b.introduced_date, b.latest_action_date)
                        )::int %% 2 = 0 THEN 2
                        ELSE 1
                    END
               ) AS legislative_session_id,
               b.congress,
               b.bill_type,
               b.bill_number,
               b.title,
               b.short_title,
               b.introduced_date,
               CASE WHEN b.latest_action_date <= %s THEN b.latest_action_date ELSE NULL END
                   AS latest_action_date,
               CASE WHEN b.latest_action_date <= %s THEN b.current_status ELSE NULL END
                   AS current_status,
               b.source_record_id AS bill_source_url,
               m.bioguide_id AS sponsor_bioguide_id,
               m.party AS sponsor_party,
               bs.sponsor_role,
               bs.is_primary,
               bs.sponsor_date,
               bs.source_record_id AS sponsor_source_url
         FROM bill b
         LEFT JOIN bill_sponsor bs
            ON bs.bill_id = b.id
           AND bs.sponsor_date <= %s
          LEFT JOIN member m
            ON m.id = bs.member_id
{member_active_on_sql("m", "bs.sponsor_date", "mt_sponsor")}
         WHERE (b.introduced_date IS NULL OR b.introduced_date <= %s)
           AND COALESCE(b.introduced_date, b.latest_action_date) <= %s
           AND COALESCE(b.introduced_date, b.latest_action_date) IS NOT NULL
        UNION ALL
        SELECT jurisdiction_id,
               legislative_body_id,
               legislative_session_id,
               congress,
               bill_type,
               bill_number,
               title,
               short_title,
               introduced_date,
               latest_action_date,
               current_status,
               bill_source_url,
               sponsor_bioguide_id,
               sponsor_party,
               sponsor_role,
               is_primary,
               sponsor_date,
               sponsor_source_url
          FROM vote_referenced_bills
         ORDER BY congress,
                  bill_type,
                  bill_number,
                  is_primary DESC,
                  sponsor_role,
                  sponsor_bioguide_id
        """,  # nosec B608
        (
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
        ),
    )


def fetch_vote_prediction_contribution_signal_rows(
    conn: ConnectionLike,
    feature_cutoff: Any,
) -> list[dict[str, Any]]:
    """Member-sector contribution alignment signals from cutoff-safe ontology edges."""
    return fetch_all(
        conn,
        f"""
        SELECT 'us_congress' AS jurisdiction_id,
               ('us_congress_' || m.chamber) AS legislative_body_id,
               m.bioguide_id AS bioguide_id,
               oe.object_node_id AS sector,
               MAX(
                   COALESCE(
                       NULLIF(oe.attributes->>'alignment_score', '')::numeric,
                       1.0
                   )
               )::float AS alignment_score,
               MAX((oe.attributes->>'contribution_date')::date) AS contribution_date,
               COALESCE(
                   jsonb_agg(DISTINCT anchor.source_anchor),
                   '[]'::jsonb
               ) AS source_anchors
          FROM ontology_edge oe
          JOIN member m
            ON m.bioguide_id = oe.subject_node_id
{member_active_on_sql("m", "(oe.attributes->>'contribution_date')::date", "mt_contribution")}
          CROSS JOIN LATERAL jsonb_array_elements(oe.source_anchors) AS anchor(source_anchor)
         WHERE oe.edge_type = 'member_sector_contribution_exposure'
           AND oe.subject_node_type = 'member'
           AND oe.object_node_type = 'sector'
           AND jsonb_array_length(oe.source_anchors) > 0
           AND anchor.source_anchor ? 'url'
           AND btrim(anchor.source_anchor->>'url') <> ''
           AND oe.attributes ? 'contribution_date'
           AND (oe.attributes->>'contribution_date')::date <= %s
{_ontology_edge_availability_cutoff_sql()}
         GROUP BY m.bioguide_id, m.chamber, oe.object_node_id
         ORDER BY m.bioguide_id, oe.object_node_id
        """,  # nosec B608
        (feature_cutoff, *_ontology_edge_availability_cutoff_params(feature_cutoff)),
    )


def fetch_vote_prediction_statement_signal_rows(
    conn: ConnectionLike,
    feature_cutoff: Any,
) -> list[dict[str, Any]]:
    """Member-sector public-statement alignment signals from cutoff-safe ontology edges."""
    return fetch_all(
        conn,
        f"""
        SELECT 'us_congress' AS jurisdiction_id,
               ('us_congress_' || m.chamber) AS legislative_body_id,
               m.bioguide_id AS bioguide_id,
               oe.object_node_id AS sector,
               MAX(
                   COALESCE(
                       NULLIF(oe.attributes->>'alignment_score', '')::numeric,
                       1.0
                   )
               )::float AS alignment_score,
               MAX((oe.attributes->>'statement_date')::date) AS statement_date,
               COALESCE(
                   jsonb_agg(DISTINCT anchor.source_anchor),
                   '[]'::jsonb
               ) AS source_anchors
          FROM ontology_edge oe
          JOIN member m
            ON m.bioguide_id = oe.subject_node_id
{member_active_on_sql("m", "(oe.attributes->>'statement_date')::date", "mt_statement")}
          CROSS JOIN LATERAL jsonb_array_elements(oe.source_anchors) AS anchor(source_anchor)
         WHERE oe.edge_type IN (
                   'member_sector_statement_alignment',
                   'member_sector_public_statement_alignment'
               )
           AND oe.subject_node_type = 'member'
           AND oe.object_node_type = 'sector'
           AND jsonb_array_length(oe.source_anchors) > 0
           AND anchor.source_anchor ? 'url'
           AND btrim(anchor.source_anchor->>'url') <> ''
           AND oe.attributes ? 'statement_date'
           AND (oe.attributes->>'statement_date')::date <= %s
{_ontology_edge_availability_cutoff_sql()}
         GROUP BY m.bioguide_id, m.chamber, oe.object_node_id
         ORDER BY m.bioguide_id, oe.object_node_id
        """,  # nosec B608
        (feature_cutoff, *_ontology_edge_availability_cutoff_params(feature_cutoff)),
    )


def fetch_bill_semantic_input_rows(
    conn: ConnectionLike,
    *,
    feature_cutoff: Any | None = None,
) -> list[dict[str, Any]]:
    """Loaded bills to enrich with LLM semantic extraction."""
    cutoff_filter = ""
    loaded_bill_key_filter = ""
    vote_fallback_cutoff_filter = ""
    latest_action_date_sql = "b.latest_action_date"
    current_status_sql = "b.current_status"
    params: tuple[Any, ...] = ()
    if feature_cutoff is not None:
        cutoff_filter = """
         WHERE COALESCE(b.introduced_date, b.latest_action_date) <= %s
           AND COALESCE(b.introduced_date, b.latest_action_date) IS NOT NULL
        """
        latest_action_date_sql = (
            "CASE WHEN b.latest_action_date <= %s THEN b.latest_action_date ELSE NULL END"
        )
        current_status_sql = (
            "CASE WHEN b.latest_action_date <= %s THEN b.current_status ELSE NULL END"
        )
        loaded_bill_key_filter = """
             WHERE COALESCE(b.introduced_date, b.latest_action_date) <= %s
               AND COALESCE(b.introduced_date, b.latest_action_date) IS NOT NULL
        """
        vote_fallback_cutoff_filter = "AND ve.vote_date <= %s"
        params = (
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
            feature_cutoff,
        )
    return fetch_all(
        conn,
        f"""
        WITH loaded_bill_keys AS (
            SELECT b.congress,
                   lower(b.bill_type) AS bill_type,
                   b.bill_number
              FROM bill b
        {loaded_bill_key_filter}
        ),
        vote_referenced_bills AS (
            SELECT DISTINCT
                   'us_congress' AS jurisdiction_id,
                   ('us_congress_' || ve.chamber) AS legislative_body_id,
                   ('congress_' || ve.congress || '_session_' || ve.session_number)
                       AS legislative_session_id,
                   ve.congress,
                   regexp_replace(lower(ref.match[1]), '[^a-z]', '', 'g') AS bill_type,
                   ref.match[2]::int AS bill_number,
                   ve.question AS title,
                   NULL::text AS short_title,
                   ve.vote_date AS introduced_date,
                   ve.vote_date AS latest_action_date,
                   ve.result AS current_status,
                   ('https://api.congress.gov/v3/bill/' || ve.congress || '/' ||
                        regexp_replace(lower(ref.match[1]), '[^a-z]', '', 'g') || '/' ||
                        ref.match[2] || '?format=json'
                   ) AS bill_source_url
              FROM vote_event ve
             CROSS JOIN LATERAL regexp_matches(
                   COALESCE(ve.question, '') || ' ' || COALESCE(ve.result, ''),
                   '\\m(H\\.?\\s*R\\.?|S\\.?|H\\.?\\s*J\\.?\\s*RES\\.?|S\\.?\\s*J\\.?\\s*RES\\.?|H\\.?\\s*CON\\.?\\s*RES\\.?|S\\.?\\s*CON\\.?\\s*RES\\.?|H\\.?\\s*RES\\.?|S\\.?\\s*RES\\.?)\\s*([0-9]+)\\M',
                   'i'
             ) AS ref(match)
             WHERE ve.source_record_id IS NOT NULL
               {vote_fallback_cutoff_filter}
               AND NOT EXISTS (
                    SELECT 1
                      FROM loaded_bill_keys lb
                     WHERE lb.congress = ve.congress
                       AND lb.bill_type = regexp_replace(
                           lower(ref.match[1]),
                           '[^a-z]',
                           '',
                           'g'
                       )
                       AND lb.bill_number = ref.match[2]::int
               )
        )
        SELECT 'us_congress' AS jurisdiction_id,
               CASE
                   WHEN lower(b.bill_type) IN ('hr', 'hres', 'hjres', 'hconres')
                       THEN 'us_congress_house'
                   WHEN lower(b.bill_type) IN ('s', 'sres', 'sjres', 'sconres')
                       THEN 'us_congress_senate'
                   ELSE 'us_congress'
               END AS legislative_body_id,
               ('congress_' || b.congress || '_session_' ||
                    CASE
                        WHEN COALESCE(b.introduced_date, b.latest_action_date) IS NULL THEN 1
                        WHEN EXTRACT(
                            YEAR FROM COALESCE(b.introduced_date, b.latest_action_date)
                        )::int %% 2 = 0 THEN 2
                        ELSE 1
                    END
               ) AS legislative_session_id,
               b.congress,
               b.bill_type,
               b.bill_number,
               b.title,
               b.short_title,
               b.introduced_date,
               {latest_action_date_sql} AS latest_action_date,
               {current_status_sql} AS current_status,
               b.source_record_id AS bill_source_url
          FROM bill b
        {cutoff_filter}
        UNION ALL
        SELECT jurisdiction_id,
               legislative_body_id,
               legislative_session_id,
               congress,
               bill_type,
               bill_number,
               title,
               short_title,
               introduced_date,
               latest_action_date,
               current_status,
               bill_source_url
          FROM vote_referenced_bills
         ORDER BY congress, bill_type, bill_number
        """,  # nosec B608
        params,
    )


def fetch_homepage_feed_rows(conn: ConnectionLike, limit: int = 20) -> list[dict[str, Any]]:
    """Most-recently-rendered evidence cards for the homepage activity feed."""
    return fetch_all(
        conn,
        """
        SELECT ec.public_id,
               ec.dimension,
               ec.score_delta,
               ec.short_explanation,
               ec.confidence_label,
               ec.rendered_at,
               m.bioguide_id AS member_bioguide_id,
               m.full_name  AS member_full_name,
               m.slug       AS member_slug,
               m.state,
               m.chamber,
               m.party
         FROM evidence_card ec
          JOIN member m ON m.id = ec.member_id
         WHERE ec.rendered_at IS NOT NULL
         ORDER BY ec.rendered_at DESC, ec.public_id
         LIMIT %s
        """,
        (limit,),
    )
