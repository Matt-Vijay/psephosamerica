"""Deterministic rule evaluator.

Evaluates RuleDefinition.conditions against a flat fact context and
returns either None (no fire) or a RuleFire.

Only supports the operators defined in Operator.  Missing-data handling
follows MissingDataPolicy.no_fire: any missing fact causes the whole
rule evaluation to return None.

No cross-record joins or pipeline engine logic lives here.
"""

from __future__ import annotations

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

# Sentinel returned by internal helpers to signal a missing fact.
_MISSING = object()


# ---------------------------------------------------------------------------
# Low-level: evaluate one condition
# ---------------------------------------------------------------------------

def evaluate_condition(
    condition: Condition,
    facts: dict[str, Any],
) -> bool | None:
    """Evaluate a single Condition against *facts*.

    Returns:
        True  – condition satisfied
        False – condition not satisfied
        None  – a required fact (or value_ref) was absent (missing data)
    """
    if condition.fact not in facts:
        return None

    fact_value = facts[condition.fact]

    op = condition.operator

    # Nullity checks need no comparison value.
    if op is Operator.is_null:
        return fact_value is None
    if op is Operator.is_not_null:
        return fact_value is not None

    # Resolve comparison value.
    if condition.value_ref is not None:
        if condition.value_ref not in facts:
            return None
        cmp_value = facts[condition.value_ref]
    else:
        cmp_value = condition.value

    if op is Operator.equals:
        return fact_value == cmp_value
    if op is Operator.not_equals:
        return fact_value != cmp_value
    if op is Operator.gt:
        return fact_value > cmp_value
    if op is Operator.gte:
        return fact_value >= cmp_value
    if op is Operator.lt:
        return fact_value < cmp_value
    if op is Operator.lte:
        return fact_value <= cmp_value
    if op is Operator.in_set:
        return fact_value in cmp_value

    # Unreachable if Operator enum is exhaustive, but be explicit.
    raise ValueError(f"Unhandled operator: {op}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Internal recursive helper
# ---------------------------------------------------------------------------

def _eval_item(
    item: Condition | ConditionGroup,
    facts: dict[str, Any],
) -> bool | None:
    """Recursively evaluate a condition or group.

    Returns True/False/None (None == missing data).
    """
    if isinstance(item, Condition):
        return evaluate_condition(item, facts)
    return _eval_group(item, facts)


def _eval_group(
    group: ConditionGroup,
    facts: dict[str, Any],
) -> bool | None:
    """Core logic for a ConditionGroup.

    Returns True/False/None (None == missing data).
    """
    # Vacuously pass if neither clause is specified.
    if group.all_of is None and group.any_of is None:
        return True

    has_missing = False

    if group.all_of is not None:
        for item in group.all_of:
            result = _eval_item(item, facts)
            if result is None:
                has_missing = True
                # Under no_fire, missing data means we cannot confirm True;
                # keep scanning so we can short-circuit on a hard False.
            elif result is False:
                return False  # Short-circuit: definitely no fire.
        # All items were True or some were None.
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


# ---------------------------------------------------------------------------
# Mid-level: evaluate a condition group (public surface)
# ---------------------------------------------------------------------------

def evaluate_condition_group(
    group: ConditionGroup,
    facts: dict[str, Any],
) -> bool:
    """Evaluate a ConditionGroup against *facts*.

    Respects ``missing_data_policy``:
    - ``no_fire``: any missing fact causes the group to return False.

    Returns True if conditions are satisfied, False otherwise.
    """
    result = _eval_group(group, facts)

    if result is None:
        # missing_data_policy is always no_fire in v1.
        return False

    return bool(result)


# ---------------------------------------------------------------------------
# Top-level: evaluate one rule
# ---------------------------------------------------------------------------

def evaluate_rule(
    rule: RuleDefinition,
    facts: dict[str, Any],
    member_bioguide_id: str,
    recompute_run_id: str,
    *,
    superseded_filing_id: str | None = None,
) -> RuleFire | None:
    """Evaluate *rule* against the flat *facts* context.

    Args:
        rule: A validated RuleDefinition.
        facts: Flat dict mapping fact name → value.
        member_bioguide_id: Canonical member identifier.
        recompute_run_id: ID of the current recompute run (provenance).
        superseded_filing_id: Populated when the trigger is an amendment.

    Returns:
        A RuleFire if the rule fires, or None if it does not.
    """
    if not evaluate_condition_group(rule.conditions, facts):
        return None

    # Render explanation template safely: unknown keys pass through verbatim.
    _safe = defaultdict(lambda: "?")
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
        superseded_filing_id=superseded_filing_id,
        explanation=explanation,
    )
