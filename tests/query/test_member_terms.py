from __future__ import annotations

from src.query.member_terms import member_active_on_sql


def test_member_active_on_sql_prefers_historical_terms_with_current_term_fallback() -> None:
    sql = member_active_on_sql("m", "vote_date", "mt")
    normalized_sql = " ".join(sql.split())

    assert "EXISTS ( SELECT 1 FROM member_term mt" in normalized_sql
    assert "mt.member_id = m.id" in normalized_sql
    assert "mt.start_date <= vote_date" in normalized_sql
    assert "mt.end_date IS NULL OR mt.end_date >= vote_date" in normalized_sql
    assert "NOT EXISTS ( SELECT 1 FROM member_term mt_any" in normalized_sql
    assert "mt_any.member_id = m.id" in normalized_sql
    assert "m.current_term_start IS NULL OR m.current_term_start <= vote_date" in normalized_sql
    assert "m.current_term_end IS NULL OR m.current_term_end >= vote_date" in normalized_sql


def test_member_active_on_sql_supports_custom_aliases_and_date_expressions() -> None:
    sql = member_active_on_sql(
        "statement_member",
        "(oe.attributes->>'statement_date')::date",
        "statement_member_term",
    )
    normalized_sql = " ".join(sql.split())

    assert "statement_member_term.member_id = statement_member.id" in normalized_sql
    assert "statement_member_term_any.member_id = statement_member.id" in normalized_sql
    assert (
        "statement_member.current_term_start IS NULL OR "
        "statement_member.current_term_start <= (oe.attributes->>'statement_date')::date"
    ) in normalized_sql
