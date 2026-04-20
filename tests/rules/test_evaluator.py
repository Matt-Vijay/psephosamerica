"""Tests for src/rules/evaluator.py.

All tests are deterministic and make no network calls.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from src.rules.evaluator import (
    evaluate_condition,
    evaluate_condition_group,
    evaluate_rule,
)
from src.rules.models import (
    Condition,
    ConditionGroup,
    MissingDataPolicy,
    Operator,
    RuleDefinition,
    RuleFire,
    RuleInput,
    Severity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cond(fact: str, op: Operator, value: Any = None, *, value_ref: str | None = None) -> Condition:
    return Condition(fact=fact, operator=op, value=value, value_ref=value_ref)


def _all_of(*items: Condition | ConditionGroup) -> ConditionGroup:
    return ConditionGroup(
        missing_data_policy=MissingDataPolicy.no_fire,
        all_of=list(items),
    )


def _any_of(*items: Condition | ConditionGroup) -> ConditionGroup:
    return ConditionGroup(
        missing_data_policy=MissingDataPolicy.no_fire,
        any_of=list(items),
    )


def _make_rule(
    conditions: ConditionGroup,
    *,
    rule_id: str = "test.rule.v1",
    severity: Severity = Severity.medium,
) -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        dimension="conflict_of_interest_risk",
        version=1,
        inputs=[RuleInput(name="x", required=True)],
        conditions=conditions,
        parameters={"threshold": 5},
        severity=severity,
        source_types_required=["disclosure"],
        explanation_template="Rule fired: x={x}",
    )


MEMBER = "A000001"
RUN = "run-42"


# ---------------------------------------------------------------------------
# evaluate_condition
# ---------------------------------------------------------------------------

class TestEvaluateCondition:
    def test_equals_true(self):
        assert evaluate_condition(_cond("a", Operator.equals, 1), {"a": 1}) is True

    def test_equals_false(self):
        assert evaluate_condition(_cond("a", Operator.equals, 2), {"a": 1}) is False

    def test_not_equals(self):
        assert evaluate_condition(_cond("a", Operator.not_equals, 2), {"a": 1}) is True

    def test_gt_true(self):
        assert evaluate_condition(_cond("a", Operator.gt, 0), {"a": 1}) is True

    def test_gt_false(self):
        assert evaluate_condition(_cond("a", Operator.gt, 1), {"a": 1}) is False

    def test_gte_equal(self):
        assert evaluate_condition(_cond("a", Operator.gte, 1), {"a": 1}) is True

    def test_lt_true(self):
        assert evaluate_condition(_cond("a", Operator.lt, 5), {"a": 3}) is True

    def test_lte_equal(self):
        assert evaluate_condition(_cond("a", Operator.lte, 3), {"a": 3}) is True

    def test_is_null_true(self):
        assert evaluate_condition(_cond("a", Operator.is_null), {"a": None}) is True

    def test_is_null_false(self):
        assert evaluate_condition(_cond("a", Operator.is_null), {"a": 0}) is False

    def test_is_not_null_true(self):
        assert evaluate_condition(_cond("a", Operator.is_not_null), {"a": "v"}) is True

    def test_is_not_null_false(self):
        assert evaluate_condition(_cond("a", Operator.is_not_null), {"a": None}) is False

    def test_in_set_true(self):
        assert evaluate_condition(_cond("a", Operator.in_set, [1, 2, 3]), {"a": 2}) is True

    def test_in_set_false(self):
        assert evaluate_condition(_cond("a", Operator.in_set, [1, 2, 3]), {"a": 9}) is False

    def test_missing_fact_returns_none(self):
        assert evaluate_condition(_cond("missing", Operator.equals, 1), {}) is None

    def test_missing_value_ref_returns_none(self):
        c = _cond("a", Operator.equals, value_ref="b")
        assert evaluate_condition(c, {"a": 1}) is None

    def test_value_ref_resolved(self):
        c = _cond("a", Operator.equals, value_ref="b")
        assert evaluate_condition(c, {"a": 5, "b": 5}) is True

    def test_value_ref_resolved_false(self):
        c = _cond("a", Operator.equals, value_ref="b")
        assert evaluate_condition(c, {"a": 5, "b": 6}) is False

    def test_is_null_ignores_value_ref(self):
        # is_null short-circuits before checking value_ref
        c = Condition(fact="a", operator=Operator.is_null, value_ref="missing_key")
        assert evaluate_condition(c, {"a": None}) is True


# ---------------------------------------------------------------------------
# evaluate_condition_group
# ---------------------------------------------------------------------------

class TestEvaluateConditionGroup:
    def test_all_of_all_true(self):
        g = _all_of(_cond("x", Operator.gte, 1), _cond("y", Operator.equals, "ok"))
        assert evaluate_condition_group(g, {"x": 5, "y": "ok"}) is True

    def test_all_of_one_false(self):
        g = _all_of(_cond("x", Operator.gte, 1), _cond("y", Operator.equals, "ok"))
        assert evaluate_condition_group(g, {"x": 5, "y": "bad"}) is False

    def test_all_of_missing_fact_no_fire(self):
        g = _all_of(_cond("x", Operator.gte, 1), _cond("missing", Operator.equals, 1))
        assert evaluate_condition_group(g, {"x": 5}) is False

    def test_any_of_one_true(self):
        g = _any_of(_cond("x", Operator.equals, 1), _cond("y", Operator.equals, 2))
        assert evaluate_condition_group(g, {"x": 1, "y": 99}) is True

    def test_any_of_all_false(self):
        g = _any_of(_cond("x", Operator.equals, 1), _cond("y", Operator.equals, 2))
        assert evaluate_condition_group(g, {"x": 9, "y": 9}) is False

    def test_any_of_missing_fact_no_fire(self):
        g = _any_of(_cond("missing", Operator.equals, 1))
        assert evaluate_condition_group(g, {}) is False

    def test_any_of_missing_but_another_true(self):
        # If any_of finds a True, missing data doesn't suppress it.
        g = _any_of(_cond("missing", Operator.equals, 1), _cond("x", Operator.equals, 5))
        assert evaluate_condition_group(g, {"x": 5}) is True

    def test_vacuous_group_passes(self):
        g = ConditionGroup(missing_data_policy=MissingDataPolicy.no_fire)
        assert evaluate_condition_group(g, {}) is True

    def test_nested_all_inside_any(self):
        inner = _all_of(_cond("a", Operator.gte, 10), _cond("b", Operator.equals, "yes"))
        outer = _any_of(_cond("x", Operator.equals, 0), inner)
        # x != 0, but inner (a>=10 AND b=="yes") is True
        assert evaluate_condition_group(outer, {"x": 99, "a": 15, "b": "yes"}) is True

    def test_nested_all_inside_any_inner_false(self):
        inner = _all_of(_cond("a", Operator.gte, 10), _cond("b", Operator.equals, "yes"))
        outer = _any_of(_cond("x", Operator.equals, 0), inner)
        # x != 0, inner fails (b != "yes")
        assert evaluate_condition_group(outer, {"x": 99, "a": 15, "b": "no"}) is False

    def test_all_of_short_circuits_on_false_before_missing(self):
        # False before missing: short-circuits to False (no fire), not None.
        g = _all_of(_cond("definite", Operator.equals, 0), _cond("missing", Operator.equals, 1))
        # definite == 5 != 0 → False immediately
        assert evaluate_condition_group(g, {"definite": 5}) is False


# ---------------------------------------------------------------------------
# evaluate_rule
# ---------------------------------------------------------------------------

class TestEvaluateRule:
    def test_fires_when_conditions_met(self):
        rule = _make_rule(_all_of(_cond("x", Operator.gte, 5)))
        fire = evaluate_rule(rule, {"x": 10}, MEMBER, RUN)
        assert isinstance(fire, RuleFire)
        assert fire.rule_id == "test.rule.v1"
        assert fire.member_bioguide_id == MEMBER
        assert fire.recompute_run_id == RUN
        assert fire.severity == Severity.medium
        assert fire.dimension == "conflict_of_interest_risk"
        assert fire.rule_version == 1
        assert fire.parameters_used == {"threshold": 5}
        assert fire.sourced_facts == {"x": 10}
        assert fire.fire_id  # non-empty UUID string

    def test_returns_none_when_conditions_not_met(self):
        rule = _make_rule(_all_of(_cond("x", Operator.gte, 5)))
        assert evaluate_rule(rule, {"x": 1}, MEMBER, RUN) is None

    def test_returns_none_on_missing_data(self):
        rule = _make_rule(_all_of(_cond("x", Operator.gte, 5)))
        assert evaluate_rule(rule, {}, MEMBER, RUN) is None

    def test_severity_inherited_from_rule(self):
        rule = _make_rule(_all_of(_cond("x", Operator.gte, 1)), severity=Severity.high)
        fire = evaluate_rule(rule, {"x": 5}, MEMBER, RUN)
        assert fire is not None
        assert fire.severity == Severity.high

    def test_explanation_rendered_from_template(self):
        rule = _make_rule(_all_of(_cond("x", Operator.gte, 1)))
        fire = evaluate_rule(rule, {"x": 7}, MEMBER, RUN)
        assert fire is not None
        assert fire.explanation == "Rule fired: x=7"

    def test_explanation_with_missing_placeholder_is_safe(self):
        rule = _make_rule(
            _all_of(_cond("x", Operator.gte, 1)),
        )
        # Template references {x} but we pass a different key; should not raise.
        fire = evaluate_rule(rule, {"x": 3, "extra": "ignored"}, MEMBER, RUN)
        assert fire is not None
        assert "3" in fire.explanation

    def test_superseded_filing_id_propagated(self):
        rule = _make_rule(_all_of(_cond("x", Operator.equals, 1)))
        fire = evaluate_rule(rule, {"x": 1}, MEMBER, RUN, superseded_filing_id="fd-99")
        assert fire is not None
        assert fire.superseded_filing_id == "fd-99"

    def test_fire_id_is_unique_across_calls(self):
        rule = _make_rule(_all_of(_cond("x", Operator.gte, 1)))
        fire1 = evaluate_rule(rule, {"x": 5}, MEMBER, RUN)
        fire2 = evaluate_rule(rule, {"x": 5}, MEMBER, RUN)
        assert fire1 is not None and fire2 is not None
        assert fire1.fire_id != fire2.fire_id

    def test_fired_at_override_is_preserved(self):
        rule = _make_rule(_all_of(_cond("x", Operator.gte, 1)))
        fired_at = dt.datetime(2024, 6, 1, tzinfo=dt.UTC)
        fire = evaluate_rule(rule, {"x": 5}, MEMBER, RUN, fired_at=fired_at)
        assert fire is not None
        assert fire.fired_at == fired_at

    def test_sourced_facts_snapshot_is_independent(self):
        facts = {"x": 5}
        rule = _make_rule(_all_of(_cond("x", Operator.gte, 1)))
        fire = evaluate_rule(rule, facts, MEMBER, RUN)
        facts["x"] = 999  # mutate after call
        assert fire is not None
        assert fire.sourced_facts["x"] == 5

    def test_no_fire_for_any_of_all_missing(self):
        rule = _make_rule(_any_of(_cond("missing_a", Operator.equals, 1)))
        assert evaluate_rule(rule, {}, MEMBER, RUN) is None

    def test_in_set_operator_fires(self):
        rule = _make_rule(_all_of(_cond("sector", Operator.in_set, ["finance", "energy"])))
        fire = evaluate_rule(rule, {"sector": "finance"}, MEMBER, RUN)
        assert fire is not None

    def test_in_set_operator_no_fire(self):
        rule = _make_rule(_all_of(_cond("sector", Operator.in_set, ["finance", "energy"])))
        assert evaluate_rule(rule, {"sector": "agriculture"}, MEMBER, RUN) is None
