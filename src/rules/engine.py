"""Batch rule runner over existing rule YAMLs and fact contexts.

Composes loader.py and evaluator.py into higher-level batch helpers.
No joins, no DB, no orchestration beyond rule execution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.rules.evaluator import evaluate_rule
from src.rules.loader import load_rules_from_directory
from src.rules.models import RuleDefinition, RuleFire

# Canonical rule directory, expressed relative to this module's location.
# Resolves to  src/rules/conflict_of_interest/
_CANONICAL_RULE_DIR: Path = Path(__file__).parent / "conflict_of_interest"


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------

def load_canonical_rules() -> list[RuleDefinition]:
    """Load all rules from the canonical conflict_of_interest rule directory.

    Returns a list of validated RuleDefinition objects sorted by rule_id.
    Raises RuleLoadError on the first file that fails.
    """
    return load_rules_from_directory(_CANONICAL_RULE_DIR)


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------

def filter_rules(
    rules: list[RuleDefinition],
    *,
    family: str | None = None,
    rule_id: str | None = None,
) -> list[RuleDefinition]:
    """Return a subset of *rules* matching the given criteria.

    Args:
        rules:   Full list to filter.
        family:  Keep rules whose rule_id contains this token as the second
                 dot-separated segment (e.g. ``"committee_sector_trade"``).
        rule_id: Keep only rules with this exact rule_id.

    Both filters may be combined; a rule must satisfy every supplied filter.
    If no filter is supplied the original list is returned unchanged.
    """
    result = rules
    if rule_id is not None:
        result = [r for r in result if r.rule_id == rule_id]
    if family is not None:
        result = [r for r in result if _rule_family(r) == family]
    return result


def _rule_family(rule: RuleDefinition) -> str:
    """Extract the family segment from a rule_id like ``dim.family.vN``."""
    parts = rule.rule_id.split(".")
    # Conventional layout: dimension.family.version
    # If the id doesn't match, fall back to the full id so filtering is safe.
    return parts[1] if len(parts) >= 2 else rule.rule_id


# ---------------------------------------------------------------------------
# Single-member batch evaluation
# ---------------------------------------------------------------------------

def run_member_batch(
    rules: list[RuleDefinition],
    member_bioguide_id: str,
    contexts: list[dict[str, Any]],
    recompute_run_id: str,
    *,
    superseded_filing_id: str | None = None,
) -> list[RuleFire]:
    """Evaluate every rule against every context for a single member.

    Args:
        rules:                Validated rules to evaluate.
        member_bioguide_id:   Canonical member identifier.
        contexts:             List of flat fact dicts (one per disclosure /
                              holding / transaction context to evaluate).
        recompute_run_id:     Provenance ID for the current recompute run.
        superseded_filing_id: Populated when a context originates from an
                              amendment filing.

    Returns:
        All RuleFire objects produced; may be empty.
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


# ---------------------------------------------------------------------------
# Multi-member batch evaluation
# ---------------------------------------------------------------------------

def run_batch(
    rules: list[RuleDefinition],
    member_contexts: dict[str, list[dict[str, Any]]],
    recompute_run_id: str,
) -> list[RuleFire]:
    """Evaluate rules across many members.

    Args:
        rules:           Validated rules to evaluate.
        member_contexts: Mapping of member_bioguide_id → list of fact dicts.
        recompute_run_id: Provenance ID for the current recompute run.

    Returns:
        All RuleFire objects produced across all members; may be empty.
    """
    fires: list[RuleFire] = []
    for member_bioguide_id, contexts in member_contexts.items():
        fires.extend(
            run_member_batch(rules, member_bioguide_id, contexts, recompute_run_id)
        )
    return fires
