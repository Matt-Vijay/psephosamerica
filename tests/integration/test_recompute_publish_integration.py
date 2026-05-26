"""Integration: seed canonical rows in Postgres, recompute, publish, verify.

Exercises the real path:
  DB rows → query → recompute_conflicts → plan_snapshot → run_publish → verify

Requires OPENPACT_TEST_POSTGRES_DSN in the environment.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pytest

from src.db.bootstrap import apply_sql, read_schema_sql
from src.db.repositories import execute_one, fetch_all
from src.export.writer import plan_snapshot
from src.pipeline.conflict_recompute import recompute_conflicts
from src.pipeline.publish_pipeline import PublishConfig, run_publish
from src.rules.models import (
    Condition,
    ConditionGroup,
    Operator,
    RuleDefinition,
    RuleFire,
    Severity,
)
from src.scoring.snapshots import build_snapshot_row

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENPACT_TEST_POSTGRES_DSN"),
    reason="OPENPACT_TEST_POSTGRES_DSN not set",
)


# ---------------------------------------------------------------------------
# Seed data helpers
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = dt.date(2026, 1, 15)
_RUN_ID = "integration-run-001"


def _seed_schema(conn) -> None:
    """Apply schema.sql to get all tables."""
    apply_sql(conn, read_schema_sql())


def _seed_member(conn, bioguide_id: str = "T000001") -> int:
    """Insert a member and return its id."""
    execute_one(
        conn,
        """
        INSERT INTO member (bioguide_id, slug, last_name, full_name, party, state, chamber, is_current)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            bioguide_id,
            bioguide_id.lower(),
            "TestLast",
            f"Rep {bioguide_id}",
            "D",
            "CA",
            "house",
            True,
        ),
    )
    rows = fetch_all(conn, "SELECT id FROM member WHERE bioguide_id = %s", (bioguide_id,))
    return rows[0]["id"]


def _seed_committee(conn, code: str = "HSEC", name: str = "Energy Committee") -> int:
    """Insert a committee and return its id."""
    execute_one(
        conn,
        """
        INSERT INTO committee (committee_code, congress, chamber, committee_type, name, review_tier)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (code, 119, "house", "standing", name, "deterministic"),
    )
    rows = fetch_all(
        conn,
        "SELECT id FROM committee WHERE committee_code = %s AND congress = %s",
        (code, 119),
    )
    return rows[0]["id"]


def _seed_committee_membership(
    conn,
    committee_id: int,
    member_id: int,
    start: dt.date = dt.date(2025, 1, 3),
) -> int:
    execute_one(
        conn,
        """
        INSERT INTO committee_membership (committee_id, member_id, role, start_date, is_current)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (committee_id, member_id, "Member", start, True),
    )
    rows = fetch_all(
        conn,
        "SELECT id FROM committee_membership WHERE committee_id = %s AND member_id = %s",
        (committee_id, member_id),
    )
    return rows[0]["id"]


def _seed_disclosure(
    conn,
    member_id: int,
    filing_year: int = 2025,
    period_start: dt.date = dt.date(2025, 1, 1),
    period_end: dt.date = dt.date(2025, 12, 31),
) -> int:
    execute_one(
        conn,
        """
        INSERT INTO financial_disclosure
            (member_id, chamber, filing_year, filing_type,
             filing_period_start, filing_period_end, filed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (member_id, "house", filing_year, "annual", period_start, period_end, dt.date(2025, 5, 15)),
    )
    rows = fetch_all(
        conn,
        "SELECT id FROM financial_disclosure WHERE member_id = %s AND filing_year = %s",
        (member_id, filing_year),
    )
    return rows[0]["id"]


def _seed_holding(
    conn,
    disclosure_id: int,
    line: int = 1,
    issuer: str = "Exxon Mobil",
    ticker: str = "XOM",
    value_min: float = 15001.0,
    value_max: float = 50000.0,
) -> int:
    execute_one(
        conn,
        """
        INSERT INTO holding
            (financial_disclosure_id, line_number, owner_type, issuer_name,
             issuer_ticker, value_min, value_max)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (disclosure_id, line, "self", issuer, ticker, value_min, value_max),
    )
    rows = fetch_all(
        conn,
        "SELECT id FROM holding WHERE financial_disclosure_id = %s AND line_number = %s",
        (disclosure_id, line),
    )
    return rows[0]["id"]


# ---------------------------------------------------------------------------
# Build the pre-joined rows that recompute_conflicts expects
# ---------------------------------------------------------------------------


def _build_committee_sector_trade_row(
    bioguide: str,
    fd_id: int,
    cm_id: int,
    committee_name: str = "Energy Committee",
) -> dict[str, Any]:
    """Minimal row for the committee_sector_trade family."""
    return {
        "member_bioguide_id": bioguide,
        "committee_name": committee_name,
        "committee_sector": "energy",
        "holding_sector": "energy",
        "sector_name": "Energy",
        "committee_start_date": dt.date(2025, 1, 3),
        "committee_end_date": None,
        "disclosure_period_start": dt.date(2025, 1, 1),
        "disclosure_period_end": dt.date(2025, 12, 31),
        "financial_disclosure_id": str(fd_id),
        "committee_membership_id": str(cm_id),
    }


# ---------------------------------------------------------------------------
# Minimal rule definition (same shape as the YAML rules)
# ---------------------------------------------------------------------------


def _make_cst_rule() -> RuleDefinition:
    return RuleDefinition(
        rule_id="conflict_of_interest_risk.committee_sector_trade.v1",
        dimension="conflict_of_interest_risk",
        version=1,
        inputs=[],
        conditions=ConditionGroup(
            all_of=[
                Condition(fact="committee_sector", operator=Operator.is_not_null),
                Condition(fact="holding_sector", operator=Operator.is_not_null),
                Condition(
                    fact="committee_service_overlap_days",
                    operator=Operator.gte,
                    value_ref="parameters.minimum_overlap_days",
                ),
                Condition(
                    fact="holding_overlap_days",
                    operator=Operator.gte,
                    value_ref="parameters.minimum_overlap_days",
                ),
            ]
        ),
        parameters={"minimum_overlap_days": 1},
        severity=Severity.medium,
        source_types_required=["committee_membership", "financial_disclosure"],
        explanation_template=(
            "Member served on {committee_name} while disclosing a holding "
            "in {sector_name}; overlap was {committee_service_overlap_days} day(s)."
        ),
    )


def _seq_id_gen(prefix: str = "ec-integ") -> Any:
    counter = {"n": 0}

    def gen(fire: RuleFire) -> str:
        counter["n"] += 1
        return f"{prefix}-{counter['n']}"

    return gen


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRecomputeFromDBRows:
    """Seed real Postgres rows, build pre-joined dicts, run recompute."""

    def test_single_member_recompute_produces_fire_and_card(self, pg_conn_clean):
        conn = pg_conn_clean
        _seed_schema(conn)

        member_id = _seed_member(conn, "T000001")
        committee_id = _seed_committee(conn)
        cm_id = _seed_committee_membership(conn, committee_id, member_id)
        fd_id = _seed_disclosure(conn, member_id)
        _seed_holding(conn, fd_id)

        # Verify rows landed
        members = fetch_all(conn, "SELECT * FROM member WHERE bioguide_id = %s", ("T000001",))
        assert len(members) == 1

        holdings = fetch_all(
            conn,
            "SELECT h.* FROM holding h JOIN financial_disclosure fd ON h.financial_disclosure_id = fd.id WHERE fd.member_id = %s",
            (member_id,),
        )
        assert len(holdings) >= 1

        # Build the pre-joined row for recompute
        cst_row = _build_committee_sector_trade_row("T000001", fd_id, cm_id)
        member_dict = {
            "bioguide_id": "T000001",
            "full_name": "Rep T000001",
            "slug": "t000001",
        }

        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [cst_row]},
            members_by_bioguide={"T000001": member_dict},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_cst_rule()],
            id_generator=_seq_id_gen(),
        )

        assert len(result.rule_fires) == 1
        assert len(result.evidence_cards) == 1

        fire = result.rule_fires[0]
        assert fire.member_bioguide_id == "T000001"
        assert fire.rule_id == "conflict_of_interest_risk.committee_sector_trade.v1"
        assert fire.severity == Severity.medium

        card = result.evidence_cards[0]
        assert card.member_bioguide_id == "T000001"
        assert card.score_delta == -2.0
        assert card.dimension == "conflict_of_interest_risk"

    def test_two_members_recompute(self, pg_conn_clean):
        conn = pg_conn_clean
        _seed_schema(conn)

        m1_id = _seed_member(conn, "A000001")
        m2_id = _seed_member(conn, "B000002")
        c_id = _seed_committee(conn)
        cm1 = _seed_committee_membership(conn, c_id, m1_id)
        cm2 = _seed_committee_membership(conn, c_id, m2_id, start=dt.date(2025, 1, 4))
        fd1 = _seed_disclosure(conn, m1_id)
        fd2 = _seed_disclosure(conn, m2_id)
        _seed_holding(conn, fd1)
        _seed_holding(conn, fd2)

        rows = [
            _build_committee_sector_trade_row("A000001", fd1, cm1),
            _build_committee_sector_trade_row("B000002", fd2, cm2),
        ]
        members = {
            "A000001": {"bioguide_id": "A000001", "full_name": "Rep A000001", "slug": "a000001"},
            "B000002": {"bioguide_id": "B000002", "full_name": "Rep B000002", "slug": "b000002"},
        }

        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": rows},
            members_by_bioguide=members,
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_cst_rule()],
            id_generator=_seq_id_gen(),
        )

        assert len(result.rule_fires) == 2
        assert "A000001" in result.by_member
        assert "B000002" in result.by_member


class TestRecomputeThenPublish:
    """Recompute from DB-seeded rows, then publish to a temp directory."""

    def test_publish_produces_verified_files(self, pg_conn_clean, tmp_path: Path):
        conn = pg_conn_clean
        _seed_schema(conn)

        member_id = _seed_member(conn, "P000001")
        committee_id = _seed_committee(conn, "HFIN", "Finance Committee")
        cm_id = _seed_committee_membership(conn, committee_id, member_id)
        fd_id = _seed_disclosure(conn, member_id)
        _seed_holding(conn, fd_id, issuer="JPMorgan Chase", ticker="JPM")

        cst_row = _build_committee_sector_trade_row(
            "P000001", fd_id, cm_id, committee_name="Finance Committee"
        )
        # Override sector to finance for this test
        cst_row["committee_sector"] = "finance"
        cst_row["holding_sector"] = "finance"
        cst_row["sector_name"] = "Finance"

        member_dict = {
            "bioguide_id": "P000001",
            "full_name": "Rep P000001",
            "slug": "p000001",
        }

        recompute_result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [cst_row]},
            members_by_bioguide={"P000001": member_dict},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_cst_rule()],
            id_generator=_seq_id_gen("ec-pub"),
        )

        assert len(recompute_result.evidence_cards) == 1
        card = recompute_result.evidence_cards[0]

        # Build a planner from the recompute output
        def planner():
            return plan_snapshot(
                snapshot_id="2026-01-15",
                member_profiles=[],
                zip_feeds=[],
                evidence_cards=recompute_result.evidence_cards,
            )

        config = PublishConfig(snapshot_id="2026-01-15", target_dir=tmp_path)
        publish_result = run_publish(config, planner)

        assert publish_result.succeeded
        assert publish_result.planned_count >= 2  # evidence card + manifest
        assert publish_result.written_count == publish_result.planned_count
        assert publish_result.verification_failures == []

        # Verify the evidence card file exists and contains the right payload
        card_path = tmp_path / f"evidence/{card.evidence_card_id}.json"
        assert card_path.exists()

        written = json.loads(card_path.read_bytes())
        assert written["member_bioguide_id"] == "P000001"
        assert written["dimension"] == "conflict_of_interest_risk"
        assert written["score_delta"] == -2.0

    def test_scoring_snapshot_from_recompute(self, pg_conn_clean):
        """Verify that recompute fires feed correctly into build_snapshot_row."""
        conn = pg_conn_clean
        _seed_schema(conn)

        member_id = _seed_member(conn, "S000001")
        committee_id = _seed_committee(conn, "HAGR", "Agriculture Committee")
        cm_id = _seed_committee_membership(conn, committee_id, member_id)
        fd_id = _seed_disclosure(conn, member_id)
        _seed_holding(conn, fd_id)

        # Create an ingestion_run for the recompute FK
        execute_one(
            conn,
            "INSERT INTO ingestion_run (run_type, status) VALUES (%s, %s)",
            ("recompute", "running"),
        )
        run_rows = fetch_all(conn, "SELECT id FROM ingestion_run ORDER BY id DESC LIMIT 1")
        run_id = run_rows[0]["id"]

        cst_row = _build_committee_sector_trade_row("S000001", fd_id, cm_id)
        member_dict = {
            "bioguide_id": "S000001",
            "full_name": "Rep S000001",
            "slug": "s000001",
        }

        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [cst_row]},
            members_by_bioguide={"S000001": member_dict},
            recompute_run_id=str(run_id),
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_cst_rule()],
            id_generator=_seq_id_gen("ec-score"),
        )

        # Build score deltas from the evidence cards
        delta_rows = [
            {"dimension": card.dimension, "score_delta": card.score_delta}
            for card in result.evidence_cards
        ]

        snapshot_row = build_snapshot_row(
            member={"id": member_id},
            delta_rows=delta_rows,
            snapshot_at=_SNAPSHOT_DATE,
            recompute_run_id=run_id,
        )

        assert snapshot_row["member_id"] == member_id
        assert snapshot_row["snapshot_at"] == _SNAPSHOT_DATE
        assert "conflict_of_interest_risk" in snapshot_row["dimension_scores"]
        assert snapshot_row["dimension_scores"]["conflict_of_interest_risk"] == 98.0
        assert snapshot_row["score_total"] == 98.0
