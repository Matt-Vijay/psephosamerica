"""Shared SQL predicates for member service-window filtering."""

from __future__ import annotations


def member_active_on_sql(member_alias: str, date_expr: str, term_alias: str) -> str:
    """Prefer historical member_term rows; fall back to member current-term bounds."""
    term_any_alias = f"{term_alias}_any"
    return f"""
           AND (
                EXISTS (
                    SELECT 1
                      FROM member_term {term_alias}
                     WHERE {term_alias}.member_id = {member_alias}.id
                       AND {term_alias}.start_date <= {date_expr}
                       AND ({term_alias}.end_date IS NULL OR {term_alias}.end_date >= {date_expr})
                )
                OR (
                    NOT EXISTS (
                        SELECT 1
                          FROM member_term {term_any_alias}
                         WHERE {term_any_alias}.member_id = {member_alias}.id
                    )
                    AND ({member_alias}.current_term_start IS NULL OR {member_alias}.current_term_start <= {date_expr})
                    AND ({member_alias}.current_term_end IS NULL OR {member_alias}.current_term_end >= {date_expr})
                )
           )
    """  # nosec B608
