"""Deterministic rule evaluator: conditions against a flat fact dict → RuleFire | None.

Missing-data policy is always no_fire: any absent fact returns None at the condition
level and propagates up to prevent the rule from firing.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from typing import Any

from src.rules.models import (
    Condition,
    ConditionGroup,
    Operator,
    RuleDefinition,
    RuleFire,
)


def evaluate_condition(
    condition: Condition,
    facts: dict[str, Any],
) -> bool | None:
    """Return True/False, or None if a required fact is absent."""
    if condition.fact not in facts:
        return None

    fact_value = facts[condition.fact]
    op = condition.operator

    if op is Operator.is_null:
        return fact_value is None
    if op is Operator.is_not_null:
        return fact_value is not None

    if condition.value_ref is not None:
        if condition.value_ref not in facts:
            return None
        cmp_value = facts[condition.value_ref]
    else:
        cmp_value = condition.value

    if op is Operator.equals:
        return bool(fact_value == cmp_value)
    if op is Operator.not_equals:
        return bool(fact_value != cmp_value)
    if op is Operator.gt:
        return bool(fact_value > cmp_value)
    if op is Operator.gte:
        return bool(fact_value >= cmp_value)
    if op is Operator.lt:
        return bool(fact_value < cmp_value)
    if op is Operator.lte:
        return bool(fact_value <= cmp_value)
    if op is Operator.in_set:
        return bool(fact_value in cmp_value)

    raise ValueError(f"Unhandled operator: {op}")  # pragma: no cover


def _eval_item(
    item: Condition | ConditionGroup,
    facts: dict[str, Any],
) -> bool | None:
    if isinstance(item, Condition):
        return evaluate_condition(item, facts)
    return _eval_group(item, facts)


def _eval_group(
    group: ConditionGroup,
    facts: dict[str, Any],
) -> bool | None:
    # Vacuously pass if neither clause is specified.
    if group.all_of is None and group.any_of is None:
        return True

    has_missing = False

    if group.all_of is not None:
        for item in group.all_of:
            result = _eval_item(item, facts)
            if result is None:
                has_missing = True
                # Keep scanning to allow short-circuit on a hard False.
            elif result is False:
                return False
        if has_missing:
            return None

    if group.any_of is not None:
        any_true = False
        any_missing = False
        for item in group.any_of:
            result = _eval_item(item, facts)
            if result is True:
                any_true = True
                break
            if result is None:
                any_missing = True
        if not any_true:
            if any_missing:
                return None
            return False

    return True


def evaluate_condition_group(
    group: ConditionGroup,
    facts: dict[str, Any],
) -> bool:
    """Evaluate a ConditionGroup; missing facts → False (no_fire policy)."""
    result = _eval_group(group, facts)
    return False if result is None else bool(result)


def evaluate_rule(
    rule: RuleDefinition,
    facts: dict[str, Any],
    member_bioguide_id: str,
    recompute_run_id: str,
    *,
    fired_at: dt.datetime | None = None,
    superseded_filing_id: str | None = None,
) -> RuleFire | None:
    """Return a RuleFire if *rule* fires against *facts*, else None.

    *superseded_filing_id* is forwarded onto the fire when the context
    originates from an amendment filing.
    """
    if not evaluate_condition_group(rule.conditions, facts):
        return None

    # Render explanation template; unknown keys pass through as "?".
    _safe: dict[str, Any] = defaultdict(lambda: "?")
    _safe.update(facts)
    try:
        explanation = rule.explanation_template.format_map(_safe)
    except (ValueError, KeyError):
        explanation = rule.explanation_template

    return RuleFire(
        fire_id=str(uuid.uuid4()),
        rule_id=rule.rule_id,
        rule_version=rule.version,
        member_bioguide_id=member_bioguide_id,
        dimension=rule.dimension,
        severity=rule.severity,
        sourced_facts=dict(facts),
        derived_values={},
        parameters_used=dict(rule.parameters),
        recompute_run_id=recompute_run_id,
        fired_at=fired_at or dt.datetime.now(dt.UTC),
        superseded_filing_id=superseded_filing_id,
        explanation=explanation,
    )
