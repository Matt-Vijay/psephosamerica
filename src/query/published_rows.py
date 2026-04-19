"""DB-backed row-fetch helpers for the published read side.

Each function returns plain dicts from fetch_all (or a single dict / None).
No payload assembly here — callers do that.
"""

from __future__ import annotations

from typing import Any

from src.db.repositories import ConnectionLike, fetch_all


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


def fetch_member_score_snapshot_rows(
    conn: ConnectionLike, member_id: int
) -> list[dict[str, Any]]:
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


def fetch_member_rule_fire_rows(
    conn: ConnectionLike, member_id: int
) -> list[dict[str, Any]]:
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
          LEFT JOIN evidence_card ec ON ec.rule_fire_id = rf.id
         WHERE rf.subject_member_id = %s
         ORDER BY rf.fired_at DESC
        """,
        (member_id,),
    )


def fetch_member_committee_rows(
    conn: ConnectionLike, member_id: int
) -> list[dict[str, Any]]:
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
               m.full_name  AS member_full_name,
               m.slug       AS member_slug,
               m.state,
               m.chamber,
               m.party
          FROM evidence_card ec
          JOIN member m ON m.id = ec.member_id
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
          LEFT JOIN rule_fire rf ON rf.id = ec.rule_fire_id
         ORDER BY ec.created_at DESC, ec.public_id
        """,
    )


def fetch_homepage_feed_rows(
    conn: ConnectionLike, limit: int = 20
) -> list[dict[str, Any]]:
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
               m.full_name  AS member_full_name,
               m.slug       AS member_slug,
               m.state,
               m.chamber,
               m.party
          FROM evidence_card ec
          JOIN member m ON m.id = ec.member_id
         WHERE ec.rendered_at IS NOT NULL
         ORDER BY ec.rendered_at DESC
         LIMIT %s
        """,
        (limit,),
    )
