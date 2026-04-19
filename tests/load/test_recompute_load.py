"""Tests for src/load/recompute.py — pure, no network, no DB."""

from __future__ import annotations

import datetime as dt

import pytest

from src.rules.models import RuleFire, Severity
from src.export.contracts import (
    ConfidenceLabel,
    EvidenceBlock,
    EvidenceCardPayload,
    EvidenceSection,
    SourceAnchor,
)
from src.load.recompute import (
    plan_evidence_cards,
    plan_rule_fires,
    plan_score_snapshots,
    recompute_load_plan,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FIRED_AT = dt.datetime(2024, 3, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
_SNAPSHOT_DATE = dt.date(2024, 3, 1)


@pytest.fixture()
def rule_fire() -> RuleFire:
    return RuleFire(
        fire_id="fire-abc-001",
        rule_id="conflict_of_interest_risk.committee_sector_trade.v1",
        rule_version=1,
        member_bioguide_id="P000197",
        dimension="conflict_of_interest_risk",
        severity=Severity.high,
        sourced_facts={"trade_date": "2023-06-01", "amount": 50000},
        derived_values={"days_after_vote": 3},
        parameters_used={"threshold_days": 30},
        recompute_run_id="42",
        fired_at=_FIRED_AT,
        explanation="Trade within 30 days of committee vote on related sector.",
    )


@pytest.fixture()
def rule_fire_amended(rule_fire: RuleFire) -> RuleFire:
    return rule_fire.model_copy(
        update={
            "fire_id": "fire-abc-002",
            "superseded_filing_id": "fd-source-001",
        }
    )


@pytest.fixture()
def evidence_card() -> EvidenceCardPayload:
    return EvidenceCardPayload(
        evidence_card_id="card-xyz-001",
        member_bioguide_id="P000197",
        member_name="Nancy Pelosi",
        member_slug="nancy-pelosi",
        dimension="conflict_of_interest_risk",
        rule_id="conflict_of_interest_risk.committee_sector_trade.v1",
        rule_version=1,
        score_delta=-10.0,
        short_explanation="Trade in committee-linked sector shortly after vote.",
        blocks=[
            EvidenceBlock(section=EvidenceSection.FACT, text="Trade on 2023-06-01."),
            EvidenceBlock(section=EvidenceSection.INFERENCE, text="Sector overlap identified."),
        ],
        source_anchors=[
            SourceAnchor(
                source_type="financial_disclosure",
                source_id="fd-source-001",
                url=None,
                label="2023 Annual Disclosure",
            )
        ],
        confidence=ConfidenceLabel.HIGH,
        snapshot_date=_SNAPSHOT_DATE,
        created_at=_FIRED_AT,
    )


@pytest.fixture()
def member() -> dict:
    return {"id": 1, "bioguide_id": "P000197"}


@pytest.fixture()
def delta_rows() -> list[dict]:
    return [{"dimension": "conflict_of_interest_risk", "delta": -10.0}]


# ---------------------------------------------------------------------------
# plan_rule_fires
# ---------------------------------------------------------------------------


def test_plan_rule_fires_structure(rule_fire: RuleFire) -> None:
    op = plan_rule_fires([rule_fire])
    assert op["table"] == "rule_fire"
    assert op["mode"] == "upsert"
    assert op["conflict_columns"] == ["source_record_id"]
    assert len(op["rows"]) == 1


def test_plan_rule_fires_row_fields(rule_fire: RuleFire) -> None:
    row = plan_rule_fires([rule_fire])["rows"][0]
    assert row["_bioguide_id"] == "P000197"
    assert row["rule_id"] == rule_fire.rule_id
    assert row["dimension"] == "conflict_of_interest_risk"
    assert row["rule_version"] == "1"
    assert row["severity"] == "high"
    assert row["source_record_id"] == "fire-abc-001"
    assert row["recompute_run_id"] == 42
    assert row["fired_at"] == _FIRED_AT
    assert row["source_facts"] == {"trade_date": "2023-06-01", "amount": 50000}
    assert row["derived_values"] == {"days_after_vote": 3}
    assert row["parameters"] == {"threshold_days": 30}
    assert row["explanation"] == rule_fire.explanation


def test_plan_rule_fires_no_superseded_filing(rule_fire: RuleFire) -> None:
    row = plan_rule_fires([rule_fire])["rows"][0]
    assert row["_superseded_filing_source_id"] is None


def test_plan_rule_fires_amended_has_superseded_ref(rule_fire_amended: RuleFire) -> None:
    row = plan_rule_fires([rule_fire_amended])["rows"][0]
    assert row["_superseded_filing_source_id"] == "fd-source-001"


def test_plan_rule_fires_empty() -> None:
    op = plan_rule_fires([])
    assert op["rows"] == []


def test_plan_rule_fires_multiple(rule_fire: RuleFire, rule_fire_amended: RuleFire) -> None:
    op = plan_rule_fires([rule_fire, rule_fire_amended])
    assert len(op["rows"]) == 2
    assert op["rows"][0]["source_record_id"] == "fire-abc-001"
    assert op["rows"][1]["source_record_id"] == "fire-abc-002"


# ---------------------------------------------------------------------------
# plan_evidence_cards
# ---------------------------------------------------------------------------


def test_plan_evidence_cards_structure(evidence_card: EvidenceCardPayload) -> None:
    op = plan_evidence_cards([evidence_card])
    assert op["table"] == "evidence_card"
    assert op["mode"] == "upsert"
    assert op["conflict_columns"] == ["public_id"]
    assert len(op["rows"]) == 1


def test_plan_evidence_cards_row_fields(evidence_card: EvidenceCardPayload) -> None:
    row = plan_evidence_cards([evidence_card])["rows"][0]
    assert row["_bioguide_id"] == "P000197"
    assert row["public_id"] == "card-xyz-001"
    assert row["dimension"] == "conflict_of_interest_risk"
    assert row["score_delta"] == -10.0
    assert row["short_explanation"] == evidence_card.short_explanation
    assert row["confidence_label"] == "HIGH"
    assert row["member_page_slug"] == "nancy-pelosi"
    assert row["source_record_id"] == "card-xyz-001"


def test_plan_evidence_cards_source_anchors(evidence_card: EvidenceCardPayload) -> None:
    row = plan_evidence_cards([evidence_card])["rows"][0]
    assert isinstance(row["source_anchors"], list)
    assert len(row["source_anchors"]) == 1
    anchor = row["source_anchors"][0]
    assert anchor["source_type"] == "financial_disclosure"
    assert anchor["source_id"] == "fd-source-001"


def test_plan_evidence_cards_blocks_split(evidence_card: EvidenceCardPayload) -> None:
    row = plan_evidence_cards([evidence_card])["rows"][0]
    assert "fact" in row["facts"]
    assert "inference" in row["inferences"]
    assert row["normative_judgments"] == {}


def test_plan_evidence_cards_empty() -> None:
    op = plan_evidence_cards([])
    assert op["rows"] == []


# ---------------------------------------------------------------------------
# plan_score_snapshots
# ---------------------------------------------------------------------------


def test_plan_score_snapshots_structure(
    member: dict, delta_rows: list[dict]
) -> None:
    op = plan_score_snapshots(
        members=[member],
        delta_rows_by_member_id={1: delta_rows},
        snapshot_at=_SNAPSHOT_DATE,
        recompute_run_id=42,
    )
    assert op["table"] == "score_snapshot"
    assert op["mode"] == "upsert"
    assert op["conflict_columns"] == ["member_id", "snapshot_at"]
    assert len(op["rows"]) == 1


def test_plan_score_snapshots_row_fields(
    member: dict, delta_rows: list[dict]
) -> None:
    row = plan_score_snapshots(
        members=[member],
        delta_rows_by_member_id={1: delta_rows},
        snapshot_at=_SNAPSHOT_DATE,
        recompute_run_id=42,
    )["rows"][0]
    assert row["_bioguide_id"] == "P000197"
    assert row["snapshot_at"] == _SNAPSHOT_DATE
    assert row["recompute_run_id"] == 42
    assert row["score_total"] == 90.0
    assert row["dimension_scores"] == {"conflict_of_interest_risk": 90.0}


def test_plan_score_snapshots_baseline_member(member: dict) -> None:
    """Member with no delta rows gets an explicit baseline dimension score."""
    row = plan_score_snapshots(
        members=[member],
        delta_rows_by_member_id={},
        snapshot_at=_SNAPSHOT_DATE,
        recompute_run_id=42,
    )["rows"][0]
    assert row["score_total"] == 100.0
    assert row["dimension_scores"] == {"conflict_of_interest_risk": 100.0}


def test_plan_score_snapshots_multiple_members() -> None:
    members = [
        {"id": 1, "bioguide_id": "P000197"},
        {"id": 2, "bioguide_id": "S000148"},
    ]
    deltas = {1: [{"dimension": "conflict_of_interest_risk", "delta": -5.0}]}
    op = plan_score_snapshots(
        members=members,
        delta_rows_by_member_id=deltas,
        snapshot_at=_SNAPSHOT_DATE,
        recompute_run_id=7,
    )
    rows = op["rows"]
    assert len(rows) == 2
    by_bioguide = {r["_bioguide_id"]: r for r in rows}
    assert by_bioguide["P000197"]["score_total"] == 95.0
    assert by_bioguide["S000148"]["score_total"] == 100.0


def test_plan_score_snapshots_clamps_below_zero() -> None:
    member = {"id": 1, "bioguide_id": "X000001"}
    deltas = {1: [{"dimension": "conflict_of_interest_risk", "delta": -200.0}]}
    row = plan_score_snapshots(
        members=[member],
        delta_rows_by_member_id=deltas,
        snapshot_at=_SNAPSHOT_DATE,
        recompute_run_id=1,
    )["rows"][0]
    assert row["score_total"] == 0.0


# ---------------------------------------------------------------------------
# recompute_load_plan (integration)
# ---------------------------------------------------------------------------


def test_recompute_load_plan_order(
    rule_fire: RuleFire,
    evidence_card: EvidenceCardPayload,
    member: dict,
    delta_rows: list[dict],
) -> None:
    plan = recompute_load_plan(
        fires=[rule_fire],
        cards=[evidence_card],
        members=[member],
        delta_rows_by_member_id={1: delta_rows},
        snapshot_at=_SNAPSHOT_DATE,
        recompute_run_id=42,
    )
    assert len(plan) == 3
    assert plan[0]["table"] == "rule_fire"
    assert plan[1]["table"] == "evidence_card"
    assert plan[2]["table"] == "score_snapshot"


def test_recompute_load_plan_all_ops_have_required_keys(
    rule_fire: RuleFire,
    evidence_card: EvidenceCardPayload,
    member: dict,
    delta_rows: list[dict],
) -> None:
    plan = recompute_load_plan(
        fires=[rule_fire],
        cards=[evidence_card],
        members=[member],
        delta_rows_by_member_id={1: delta_rows},
        snapshot_at=_SNAPSHOT_DATE,
        recompute_run_id=42,
    )
    for op in plan:
        assert "table" in op
        assert "rows" in op
        assert "conflict_columns" in op
        assert "mode" in op


def test_recompute_load_plan_empty_fires_and_cards(member: dict) -> None:
    plan = recompute_load_plan(
        fires=[],
        cards=[],
        members=[member],
        delta_rows_by_member_id={},
        snapshot_at=_SNAPSHOT_DATE,
        recompute_run_id=1,
    )
    assert plan[0]["rows"] == []
    assert plan[1]["rows"] == []
    assert len(plan[2]["rows"]) == 1
