"""Batch rule runner: composes loader and evaluator into higher-level helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.rules.evaluator import evaluate_rule
from src.rules.loader import load_rules_from_directory
from src.rules.models import RuleDefinition, RuleFire

# Resolves to src/rules/conflict_of_interest/
_CANONICAL_RULE_DIR: Path = Path(__file__).parent / "conflict_of_interest"


def load_canonical_rules() -> list[RuleDefinition]:
    return load_rules_from_directory(_CANONICAL_RULE_DIR)


def filter_rules(
    rules: list[RuleDefinition],
    *,
    family: str | None = None,
    rule_id: str | None = None,
) -> list[RuleDefinition]:
    """Return rules matching *family* and/or *rule_id*. Both filters stack.

    *family* matches the second dot-segment of rule_id (e.g. ``"committee_sector_trade"``).
    """
    result = rules
    if rule_id is not None:
        result = [r for r in result if r.rule_id == rule_id]
    if family is not None:
        result = [r for r in result if _rule_family(r) == family]
    return result


def _rule_family(rule: RuleDefinition) -> str:
    """Extract the family segment from ``dimension.family.vN``."""
    parts = rule.rule_id.split(".")
    return parts[1] if len(parts) >= 2 else rule.rule_id


def run_member_batch(
    rules: list[RuleDefinition],
    member_bioguide_id: str,
    contexts: list[dict[str, Any]],
    recompute_run_id: str,
    *,
    superseded_filing_id: str | None = None,
) -> list[RuleFire]:
    """Evaluate every rule against every context for one member.

    *superseded_filing_id* is forwarded to each fire when the contexts
    originate from an amendment filing.
    """
    fires: list[RuleFire] = []
    for facts in contexts:
        for rule in rules:
            fire = evaluate_rule(
                rule,
                facts,
                member_bioguide_id,
                recompute_run_id,
                superseded_filing_id=superseded_filing_id,
            )
            if fire is not None:
                fires.append(fire)
    return fires


def run_batch(
    rules: list[RuleDefinition],
    member_contexts: dict[str, list[dict[str, Any]]],
    recompute_run_id: str,
) -> list[RuleFire]:
    fires: list[RuleFire] = []
    for member_bioguide_id, contexts in member_contexts.items():
        fires.extend(run_member_batch(rules, member_bioguide_id, contexts, recompute_run_id))
    return fires
