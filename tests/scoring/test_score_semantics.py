from __future__ import annotations

import datetime as dt

import pytest

from src.feed.changes import (
    events_from_evidence_cards,
    events_from_rule_fires,
    events_from_score_deltas,
)
from src.homepage.builders import build_top_changes
from src.load.recompute import plan_score_snapshots
from src.pipeline.conflict_recompute import recompute_conflicts
from src.rules.contexts import (
    build_committee_sector_trade_context,
    build_repeated_committee_linked_trading_context,
)
from src.rules.models import Condition, ConditionGroup, Operator, RuleDefinition, Severity
from src.scoring.deltas import diff_one_member
from src.scoring.snapshots import build_snapshot_row

DIM = "conflict_of_interest_risk"


def _member() -> dict:
    return {"id": 1, "bioguide_id": "A000001", "full_name": "Test Member"}


def _member_meta() -> dict[str, dict]:
    return {
        "A000001": {
            "bioguide_id": "A000001",
            "full_name": "Test Member",
            "slug": "test-member",
            "chamber": "house",
            "party": "I",
            "state": "CA",
        }
    }


def _delta(value: float) -> dict:
    return {"dimension": DIM, "delta": value}


def _make_rule(severity: Severity = Severity.medium) -> RuleDefinition:
    return RuleDefinition(
        rule_id="conflict_of_interest_risk.committee_sector_trade.v1",
        dimension=DIM,
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
        severity=severity,
        source_types_required=["committee_membership", "financial_disclosure"],
        explanation_template="Committee and holding overlap.",
    )


def _committee_sector_trade_row() -> dict:
    return {
        "member_bioguide_id": "A000001",
        "committee_name": "Finance Committee",
        "committee_sector": "finance",
        "holding_sector": "finance",
        "sector_name": "Finance",
        "committee_start_date": dt.date(2025, 1, 3),
        "committee_end_date": None,
        "disclosure_period_start": dt.date(2025, 1, 3),
        "disclosure_period_end": dt.date(2025, 12, 31),
        "financial_disclosure_id": "fd-1",
        "source_url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/fd-1.pdf",
        "committee_membership_id": "cm-1",
        "committee_membership_source_url": "https://api.congress.gov/v3/committee/house/HSBA?format=json",
    }


class TestScoreSemantics:
    def test_reset_to_baseline_emits_positive_recovery_delta(self):
        previous = build_snapshot_row(
            _member(),
            [_delta(-20.0)],
            dt.date(2025, 1, 15),
            7,
        )
        current = build_snapshot_row(
            _member(),
            [],
            dt.date(2025, 2, 15),
            7,
        )
        previous["bioguide_id"] = "A000001"
        current["bioguide_id"] = "A000001"

        deltas = diff_one_member(current, previous)

        assert len(deltas) == 1
        assert deltas[0]["dimension"] == DIM
        assert deltas[0]["delta"] == pytest.approx(20.0)
        assert deltas[0]["snapshot_date"] == dt.date(2025, 2, 15)

    def test_legacy_empty_baseline_row_still_produces_visible_recovery_everywhere(self):
        previous = build_snapshot_row(
            _member(),
            [_delta(-20.0)],
            dt.date(2025, 1, 15),
            7,
        )
        previous["bioguide_id"] = "A000001"
        current = {
            "member_id": 1,
            "bioguide_id": "A000001",
            "snapshot_at": dt.date(2025, 2, 15),
            "score_total": 100.0,
            "dimension_scores": {},
        }

        deltas = diff_one_member(current, previous)
        assert len(deltas) == 1
        assert deltas[0]["delta"] == pytest.approx(20.0)

        events = events_from_score_deltas(deltas, _member_meta())
        assert len(events) == 1
        assert events[0].score_delta == pytest.approx(20.0)
        assert events[0].abs_delta == pytest.approx(20.0)

        summary = build_top_changes(events, _member_meta(), n=1)[0]
        assert summary.score_delta == pytest.approx(20.0)
        assert summary.abs_delta == pytest.approx(20.0)

    def test_medium_penalty_has_same_signed_meaning_across_pipeline_feed_homepage_and_load(self):
        snapshot_date = dt.date(2025, 2, 15)
        member_meta = _member_meta()
        member = member_meta["A000001"]

        recompute = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [_committee_sector_trade_row()]},
            members_by_bioguide={"A000001": member},
            recompute_run_id="run-1",
            snapshot_date=snapshot_date,
            rules=[_make_rule(Severity.medium)],
            id_generator=lambda _fire: "card-1",
        )
        card = recompute.evidence_cards[0]
        assert card.score_delta == pytest.approx(-2.0)

        card_payload = card.model_dump()
        card_payload["public_id"] = card_payload.pop("evidence_card_id")
        card_event = events_from_evidence_cards([card_payload], member_meta)[0]
        assert card_event.score_delta == pytest.approx(-2.0)

        fire_event = events_from_rule_fires(
            [
                {
                    "fire_id": recompute.rule_fires[0].fire_id,
                    "rule_id": recompute.rule_fires[0].rule_id,
                    "member_bioguide_id": "A000001",
                    "dimension": DIM,
                    "severity": "medium",
                    "explanation": recompute.rule_fires[0].explanation,
                    "snapshot_date": snapshot_date,
                }
            ],
            member_meta,
        )[0]
        assert fire_event.score_delta == pytest.approx(card.score_delta)

        homepage_summary = build_top_changes([card_event], member_meta, n=1)[0]
        assert homepage_summary.score_delta == pytest.approx(card.score_delta)
        assert homepage_summary.abs_delta == pytest.approx(abs(card.score_delta))

        load_row = plan_score_snapshots(
            members=[{"id": 1, "bioguide_id": "A000001"}],
            delta_rows_by_member_id={1: [{"dimension": DIM, "score_delta": card.score_delta}]},
            snapshot_at=snapshot_date,
            recompute_run_id=9,
        )["rows"][0]
        assert load_row["dimension_scores"] == {DIM: 98.0}
        assert load_row["score_total"] == pytest.approx(98.0)

    def test_committee_sector_trade_context_uses_snapshot_date_for_open_ranges(self):
        snapshot_date = dt.date(2024, 6, 30)
        row = {
            "committee_name": "Finance Committee",
            "committee_sector": "finance",
            "committee_start_date": dt.date(2024, 1, 1),
            "committee_end_date": None,
            "holding_sector": "finance",
            "sector_name": "Finance",
            "disclosure_period_start": dt.date(2024, 3, 1),
            "disclosure_period_end": None,
            "snapshot_date": snapshot_date,
        }

        ctx = build_committee_sector_trade_context(row)

        assert ctx["overlap_start"] == dt.date(2024, 3, 1)
        assert ctx["overlap_end"] == snapshot_date
        assert ctx["committee_service_overlap_days"] == 122
        assert ctx["holding_overlap_days"] == 122

    def test_repeated_trading_context_uses_snapshot_date_for_open_service(self):
        snapshot_date = dt.date(2024, 6, 30)
        row = {
            "committee_name": "Energy Committee",
            "committee_sector": "energy",
            "committee_start_date": dt.date(2024, 1, 1),
            "committee_end_date": None,
            "transactions": [
                {"transaction_date": dt.date(2024, 5, 1), "sector": "energy"},
                {"transaction_date": dt.date(2024, 7, 1), "sector": "energy"},
            ],
            "snapshot_date": snapshot_date,
        }

        ctx = build_repeated_committee_linked_trading_context(row)

        assert ctx["service_overlap_days"] == 182
        assert ctx["matching_transaction_count"] == 1
        assert ctx["distinct_trade_days"] == 1
