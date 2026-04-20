"""Tests for src/pipeline/conflict_recompute.py.

All tests are pure: no DB, no filesystem reads (rules are passed explicitly),
no network calls.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from src.export.contracts import ConfidenceLabel, EvidenceCardPayload
from src.pipeline.conflict_recompute import (
    _default_id_generator,
    group_by_member,
    recompute_conflicts,
)
from src.rules.models import (
    Condition,
    ConditionGroup,
    Operator,
    RuleDefinition,
    RuleFire,
    Severity,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_SNAPSHOT_DATE = dt.date(2024, 6, 1)
_RUN_ID = "run-test-001"


def _make_rule(
    rule_id: str = "conflict_of_interest_risk.committee_sector_trade.v1",
    dimension: str = "conflict_of_interest_risk",
    version: int = 1,
    severity: Severity = Severity.medium,
    conditions: ConditionGroup | None = None,
    parameters: dict | None = None,
) -> RuleDefinition:
    """Build a minimal RuleDefinition for testing."""
    if conditions is None:
        # Fires when committee_sector and holding_sector are present and
        # overlap days meet the threshold.  Uses value_ref so the orchestrator's
        # parameter-injection path is exercised in tests.
        conditions = ConditionGroup(
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
        )
    if parameters is None:
        parameters = {"minimum_overlap_days": 1}  # injected as parameters.minimum_overlap_days

    return RuleDefinition(
        rule_id=rule_id,
        dimension=dimension,
        version=version,
        inputs=[],
        conditions=conditions,
        parameters=parameters,
        severity=severity,
        source_types_required=["committee_membership", "financial_disclosure"],
        explanation_template=(
            "Member served on {committee_name} while disclosing a holding "
            "in {sector_name}; overlap was {committee_service_overlap_days} day(s)."
        ),
    )


def _cst_row(
    bioguide: str = "A000001",
    committee_name: str = "Energy Committee",
    committee_sector: str = "energy",
    holding_sector: str = "energy",
    sector_name: str = "Energy",
    service_overlap_days: int = 30,
    holding_overlap_days: int = 30,
    financial_disclosure_id: str = "fd-001",
    committee_membership_id: str = "cm-001",
    committee_start_date: dt.date | None = None,
    committee_end_date: dt.date | None = None,
    disclosure_period_start: dt.date | None = None,
    disclosure_period_end: dt.date | None = None,
) -> dict[str, Any]:
    """Build a minimal committee_sector_trade row that will fire the rule."""
    return {
        "member_bioguide_id": bioguide,
        "committee_name": committee_name,
        "committee_sector": committee_sector,
        "holding_sector": holding_sector,
        "sector_name": sector_name,
        # These two facts are evaluated directly by the rule:
        "committee_service_overlap_days": service_overlap_days,
        "holding_overlap_days": holding_overlap_days,
        # Also included so the context builder doesn't error on missing keys:
        "committee_start_date": committee_start_date or dt.date(2023, 1, 1),
        "committee_end_date": committee_end_date,
        "disclosure_period_start": disclosure_period_start or dt.date(2023, 1, 1),
        "disclosure_period_end": disclosure_period_end or dt.date(2023, 12, 31),
        "financial_disclosure_id": financial_disclosure_id,
        "committee_membership_id": committee_membership_id,
    }


def _member(bioguide: str = "A000001") -> dict[str, Any]:
    return {
        "bioguide_id": bioguide,
        "full_name": f"Rep {bioguide}",
        "slug": bioguide.lower(),
    }


def _seq_id_gen(prefix: str = "ev") -> Any:
    """Return an injectable ID generator that emits sequential IDs."""
    counter = {"n": 0}

    def gen(fire: RuleFire) -> str:  # noqa: D401
        counter["n"] += 1
        return f"{prefix}-{counter['n']}"

    return gen


# ---------------------------------------------------------------------------
# recompute_conflicts — basic cases
# ---------------------------------------------------------------------------


class TestRecomputeConflictsBasic:
    def test_empty_inputs_returns_empty_result(self) -> None:
        result = recompute_conflicts(
            rows_by_family={},
            members_by_bioguide={},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
        )
        assert result.rule_fires == []
        assert result.evidence_cards == []
        assert result.by_member == {}

    def test_unknown_family_produces_no_output(self) -> None:
        result = recompute_conflicts(
            rows_by_family={"nonexistent_family": [{"member_bioguide_id": "X"}]},
            members_by_bioguide={},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
        )
        assert result.rule_fires == []
        assert result.evidence_cards == []

    def test_single_firing_row_produces_one_fire_and_one_card(self) -> None:
        row = _cst_row()
        rule = _make_rule()
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[rule],
            id_generator=_seq_id_gen(),
        )
        assert len(result.rule_fires) == 1
        assert len(result.evidence_cards) == 1
        assert result.rule_fires[0].member_bioguide_id == "A000001"
        assert result.evidence_cards[0].member_bioguide_id == "A000001"

    def test_non_firing_row_produces_no_output(self) -> None:
        """A row missing committee_sector should not fire the rule."""
        row = _cst_row()
        row["committee_sector"] = None  # condition: is_not_null → False
        rule = _make_rule()
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[rule],
        )
        assert result.rule_fires == []
        assert result.evidence_cards == []


# ---------------------------------------------------------------------------
# recompute_conflicts — fire and card attributes
# ---------------------------------------------------------------------------


class TestRuleFireAttributes:
    def test_fire_rule_id_matches_rule(self) -> None:
        row = _cst_row()
        rule = _make_rule(rule_id="conflict_of_interest_risk.committee_sector_trade.v1")
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[rule],
            id_generator=_seq_id_gen(),
        )
        assert result.rule_fires[0].rule_id == rule.rule_id

    def test_fire_recompute_run_id_propagated(self) -> None:
        row = _cst_row()
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id="custom-run-42",
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
            id_generator=_seq_id_gen(),
        )
        assert result.rule_fires[0].recompute_run_id == "custom-run-42"

    def test_evidence_card_snapshot_date_set(self) -> None:
        row = _cst_row()
        snap = dt.date(2024, 12, 31)
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=snap,
            rules=[_make_rule()],
            id_generator=_seq_id_gen(),
        )
        assert result.evidence_cards[0].snapshot_date == snap
        assert result.rule_fires[0].fired_at == dt.datetime(2024, 12, 31, tzinfo=dt.UTC)
        assert result.evidence_cards[0].created_at == dt.datetime(2024, 12, 31, tzinfo=dt.UTC)

    def test_default_evidence_card_id_is_stable_for_same_snapshot_date(self) -> None:
        row = _cst_row()
        first = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
        )
        second = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id="run-test-002",
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
        )
        assert first.evidence_cards[0].evidence_card_id == second.evidence_cards[0].evidence_card_id

    def test_evidence_card_id_uses_injected_generator(self) -> None:
        row = _cst_row()
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
            id_generator=lambda _fire: "fixed-card-id",
        )
        assert result.evidence_cards[0].evidence_card_id == "fixed-card-id"

    def test_score_delta_reflects_severity_medium(self) -> None:
        row = _cst_row()
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule(severity=Severity.medium)],
            id_generator=_seq_id_gen(),
        )
        assert result.evidence_cards[0].score_delta == -2.0

    def test_score_delta_reflects_severity_high(self) -> None:
        row = _cst_row()
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule(severity=Severity.high)],
            id_generator=_seq_id_gen(),
        )
        assert result.evidence_cards[0].score_delta == -4.0

    def test_evidence_card_confidence_is_high(self) -> None:
        row = _cst_row()
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
            id_generator=_seq_id_gen(),
        )
        assert result.evidence_cards[0].confidence == ConfidenceLabel.HIGH

    def test_source_anchors_forwarded_to_evidence_card(self) -> None:
        row = _cst_row(financial_disclosure_id="fd-999", committee_membership_id="cm-999")
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
            id_generator=_seq_id_gen(),
        )
        anchor_ids = {a.source_id for a in result.evidence_cards[0].source_anchors}
        assert "fd-999" in anchor_ids
        assert "cm-999" in anchor_ids

    def test_open_ended_rows_produce_stable_score_outputs_across_wall_clock_dates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import src.evidence.builder as evidence_builder
        import src.rules.evaluator as rule_evaluator
        import src.rules.models as rule_models

        row = _cst_row()
        row["committee_end_date"] = None
        row["disclosure_period_end"] = None

        def freeze_now(target: dt.datetime) -> None:
            class FrozenDateTime(dt.datetime):
                @classmethod
                def now(cls, tz=None):
                    if tz is None:
                        return target.replace(tzinfo=None)
                    return target.astimezone(tz)

            monkeypatch.setattr(rule_models.dt, "datetime", FrozenDateTime)
            monkeypatch.setattr(evidence_builder.dt, "datetime", FrozenDateTime)

        monkeypatch.setattr(
            rule_evaluator.uuid,
            "uuid4",
            lambda: "00000000-0000-0000-0000-000000000001",
        )

        def project(result) -> dict[str, Any]:
            fire = result.rule_fires[0]
            card = result.evidence_cards[0]
            return {
                "overlap_days": fire.sourced_facts["committee_service_overlap_days"],
                "holding_overlap_days": fire.sourced_facts["holding_overlap_days"],
                "overlap_end": fire.sourced_facts["overlap_end"],
                "explanation": fire.explanation,
                "score_delta": card.score_delta,
                "short_explanation": card.short_explanation,
                "snapshot_date": card.snapshot_date,
                "evidence_card_id": card.evidence_card_id,
            }

        freeze_now(dt.datetime(2026, 1, 15, tzinfo=dt.timezone.utc))
        first = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
            id_generator=lambda _fire: "stable-card-id",
        )

        freeze_now(dt.datetime(2031, 9, 10, tzinfo=dt.timezone.utc))
        second = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
            id_generator=lambda _fire: "stable-card-id",
        )

        expected = {
            "overlap_days": 518,
            "holding_overlap_days": 518,
            "overlap_end": _SNAPSHOT_DATE,
            "explanation": (
                "Member served on Energy Committee while disclosing a holding "
                "in Energy; overlap was 518 day(s)."
            ),
            "score_delta": -2.0,
            "short_explanation": (
                "Member served on Energy Committee while disclosing a holding "
                "in Energy; overlap was 518 day(s)."
            ),
            "snapshot_date": _SNAPSHOT_DATE,
            "evidence_card_id": "stable-card-id",
        }

        assert project(first) == expected
        assert project(second) == expected


# ---------------------------------------------------------------------------
# recompute_conflicts — per-member grouping
# ---------------------------------------------------------------------------


class TestPerMemberGrouping:
    def test_two_members_produce_separate_buckets(self) -> None:
        rows = [_cst_row("A000001"), _cst_row("B000002")]
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": rows},
            members_by_bioguide={
                "A000001": _member("A000001"),
                "B000002": _member("B000002"),
            },
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
            id_generator=_seq_id_gen(),
        )
        assert "A000001" in result.by_member
        assert "B000002" in result.by_member
        assert len(result.by_member["A000001"].rule_fires) == 1
        assert len(result.by_member["B000002"].rule_fires) == 1

    def test_two_firing_rows_same_member_accumulate(self) -> None:
        rows = [
            _cst_row("A000001", financial_disclosure_id="fd-1", committee_membership_id="cm-1"),
            _cst_row("A000001", financial_disclosure_id="fd-2", committee_membership_id="cm-2"),
        ]
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": rows},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
            id_generator=_seq_id_gen(),
        )
        assert len(result.by_member["A000001"].rule_fires) == 2
        assert len(result.by_member["A000001"].evidence_cards) == 2

    def test_by_member_fires_match_flat_list(self) -> None:
        rows = [_cst_row("A000001"), _cst_row("B000002")]
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": rows},
            members_by_bioguide={
                "A000001": _member("A000001"),
                "B000002": _member("B000002"),
            },
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
            id_generator=_seq_id_gen(),
        )
        flat_fire_ids = {f.fire_id for f in result.rule_fires}
        member_fire_ids = {
            f.fire_id
            for bucket in result.by_member.values()
            for f in bucket.rule_fires
        }
        assert flat_fire_ids == member_fire_ids

    def test_member_not_in_dict_still_produces_results(self) -> None:
        """Missing member metadata must not abort processing."""
        row = _cst_row("Z999999")
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": [row]},
            members_by_bioguide={},  # Z999999 absent
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
            id_generator=_seq_id_gen(),
        )
        assert len(result.rule_fires) == 1
        assert result.rule_fires[0].member_bioguide_id == "Z999999"


# ---------------------------------------------------------------------------
# recompute_conflicts — multiple families
# ---------------------------------------------------------------------------


class TestMultipleFamilies:
    def _sector_holdings_rule(self) -> RuleDefinition:
        """Minimal sector_holdings_overlap rule that always fires if sectors present."""
        conditions = ConditionGroup(
            all_of=[
                Condition(fact="committee_sector", operator=Operator.is_not_null),
                Condition(fact="holding_sector", operator=Operator.is_not_null),
                Condition(
                    fact="holding_value_usd",
                    operator=Operator.gte,
                    value_ref="parameters.minimum_holding_value_usd",
                ),
                Condition(
                    fact="overlap_days",
                    operator=Operator.gte,
                    value_ref="parameters.minimum_overlap_days",
                ),
            ]
        )
        return RuleDefinition(
            rule_id="conflict_of_interest_risk.sector_holdings_overlap.v1",
            dimension="conflict_of_interest_risk",
            version=1,
            inputs=[],
            conditions=conditions,
            parameters={"minimum_holding_value_usd": 1000.0, "minimum_overlap_days": 1},
            severity=Severity.medium,
            source_types_required=["committee_membership", "financial_disclosure"],
            explanation_template=(
                "Member held a disclosed position in {sector_name} while serving on "
                "{committee_name}; active overlap lasted {overlap_days} day(s)."
            ),
        )

    def _sho_row(self, bioguide: str = "A000001") -> dict[str, Any]:
        return {
            "member_bioguide_id": bioguide,
            "committee_name": "Finance Committee",
            "committee_sector": "finance",
            "holding_sector": "finance",
            "sector_name": "Finance",
            "holding_value_min": 15000.0,
            "holding_value_max": 50000.0,
            "committee_start_date": dt.date(2023, 1, 1),
            "committee_end_date": None,
            "disclosure_period_start": dt.date(2023, 1, 1),
            "disclosure_period_end": dt.date(2023, 12, 31),
            "financial_disclosure_id": "fd-sho-001",
            "committee_membership_id": "cm-sho-001",
        }

    def test_fires_from_both_families_aggregated(self) -> None:
        cst_rule = _make_rule()
        sho_rule = self._sector_holdings_rule()

        result = recompute_conflicts(
            rows_by_family={
                "committee_sector_trade": [_cst_row()],
                "sector_holdings_overlap": [self._sho_row()],
            },
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[cst_rule, sho_rule],
            id_generator=_seq_id_gen(),
        )
        # One fire per family for the same member.
        assert len(result.rule_fires) == 2
        rule_ids = {f.rule_id for f in result.rule_fires}
        assert "conflict_of_interest_risk.committee_sector_trade.v1" in rule_ids
        assert "conflict_of_interest_risk.sector_holdings_overlap.v1" in rule_ids

    def test_per_member_accumulates_across_families(self) -> None:
        cst_rule = _make_rule()
        sho_rule = self._sector_holdings_rule()

        result = recompute_conflicts(
            rows_by_family={
                "committee_sector_trade": [_cst_row()],
                "sector_holdings_overlap": [self._sho_row()],
            },
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[cst_rule, sho_rule],
            id_generator=_seq_id_gen(),
        )
        bucket = result.by_member["A000001"]
        assert len(bucket.rule_fires) == 2
        assert len(bucket.evidence_cards) == 2


# ---------------------------------------------------------------------------
# recompute_conflicts — injectable ID generator
# ---------------------------------------------------------------------------


class TestInjectableIdGenerator:
    def test_custom_generator_called_per_fire(self) -> None:
        calls: list[RuleFire] = []

        def capturing_gen(fire: RuleFire) -> str:
            calls.append(fire)
            return f"card-{len(calls)}"

        rows = [
            _cst_row("A000001", financial_disclosure_id="fd-1", committee_membership_id="cm-1"),
            _cst_row("A000001", financial_disclosure_id="fd-2", committee_membership_id="cm-2"),
        ]
        result = recompute_conflicts(
            rows_by_family={"committee_sector_trade": rows},
            members_by_bioguide={"A000001": _member()},
            recompute_run_id=_RUN_ID,
            snapshot_date=_SNAPSHOT_DATE,
            rules=[_make_rule()],
            id_generator=capturing_gen,
        )
        assert len(calls) == 2
        card_ids = {c.evidence_card_id for c in result.evidence_cards}
        assert card_ids == {"card-1", "card-2"}

    def test_default_id_generator_produces_ec_prefixed_ids(self) -> None:
        from src.rules.models import Severity as S

        # Build a minimal fire to test the default generator directly.
        fire = RuleFire(
            fire_id="test-fire-id",
            rule_id="x.y.v1",
            rule_version=1,
            member_bioguide_id="A000001",
            dimension="conflict_of_interest_risk",
            severity=S.low,
            sourced_facts={},
            parameters_used={},
            recompute_run_id="run-1",
            explanation="",
        )
        card_id = _default_id_generator(fire)
        assert card_id.startswith("ec-")

    def test_default_id_generator_is_deterministic(self) -> None:
        from src.rules.models import Severity as S

        fire = RuleFire(
            fire_id="stable-fire-id",
            rule_id="x.y.v1",
            rule_version=1,
            member_bioguide_id="A000001",
            dimension="conflict_of_interest_risk",
            severity=S.low,
            sourced_facts={},
            parameters_used={},
            recompute_run_id="run-1",
            explanation="",
        )
        assert _default_id_generator(fire) == _default_id_generator(fire)


# ---------------------------------------------------------------------------
# group_by_member utility
# ---------------------------------------------------------------------------


class TestGroupByMember:
    def _make_fire(self, bioguide: str, fire_id: str = "f1") -> RuleFire:
        return RuleFire(
            fire_id=fire_id,
            rule_id="x.y.v1",
            rule_version=1,
            member_bioguide_id=bioguide,
            dimension="conflict_of_interest_risk",
            severity=Severity.low,
            sourced_facts={},
            parameters_used={},
            recompute_run_id="run-1",
            explanation="",
        )

    def _make_card(self, bioguide: str, card_id: str = "c1") -> EvidenceCardPayload:
        return EvidenceCardPayload(
            evidence_card_id=card_id,
            member_bioguide_id=bioguide,
            member_name="Test",
            member_slug=bioguide.lower(),
            dimension="conflict_of_interest_risk",
            rule_id="x.y.v1",
            rule_version=1,
            score_delta=1.0,
            short_explanation="test",
            blocks=[],
            source_anchors=[],
            confidence=ConfidenceLabel.HIGH,
            snapshot_date=_SNAPSHOT_DATE,
            created_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        )

    def test_empty_lists_return_empty_dict(self) -> None:
        assert group_by_member([], []) == {}

    def test_fires_grouped_by_member(self) -> None:
        fires = [self._make_fire("A", "f1"), self._make_fire("B", "f2")]
        result = group_by_member(fires, [])
        assert set(result.keys()) == {"A", "B"}
        assert len(result["A"].rule_fires) == 1
        assert len(result["B"].rule_fires) == 1

    def test_cards_grouped_by_member(self) -> None:
        cards = [self._make_card("A", "c1"), self._make_card("A", "c2")]
        result = group_by_member([], cards)
        assert len(result["A"].evidence_cards) == 2

    def test_mixed_fires_and_cards(self) -> None:
        fires = [self._make_fire("A", "f1")]
        cards = [self._make_card("A", "c1"), self._make_card("B", "c2")]
        result = group_by_member(fires, cards)
        assert len(result["A"].rule_fires) == 1
        assert len(result["A"].evidence_cards) == 1
        assert len(result["B"].evidence_cards) == 1
