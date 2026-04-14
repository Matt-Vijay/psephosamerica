"""Typed models for rule definitions and rule fires.

These models define the contract between YAML rule definitions on disk
and the rule execution engine (implemented elsewhere).
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Operator(str, Enum):
    equals = "equals"
    not_equals = "not_equals"
    gt = "gt"
    gte = "gte"
    lt = "lt"
    lte = "lte"
    is_null = "is_null"
    is_not_null = "is_not_null"
    in_set = "in_set"


class MissingDataPolicy(str, Enum):
    """What to do when a required input has missing data."""
    no_fire = "no_fire"


# ---------------------------------------------------------------------------
# Rule definition sub-models
# ---------------------------------------------------------------------------

class RuleInput(BaseModel):
    """A single named input that a rule requires."""
    name: str
    required: bool = True
    description: str = ""


class Condition(BaseModel):
    """A single fact-level predicate."""
    fact: str
    operator: Operator
    value: Any = None
    value_ref: str | None = None


class ConditionGroup(BaseModel):
    """A group of conditions joined by all_of / any_of logic.

    Supports one level of nesting: a top-level group may contain
    nested groups (e.g. any_of containing all_of blocks).
    """
    missing_data_policy: MissingDataPolicy = MissingDataPolicy.no_fire
    all_of: list[Condition | ConditionGroup] | None = None
    any_of: list[Condition | ConditionGroup] | None = None


# ---------------------------------------------------------------------------
# Rule definition (maps 1:1 to a YAML file on disk)
# ---------------------------------------------------------------------------

class RuleDefinition(BaseModel):
    """A complete, versioned, declarative rule definition.

    Every field required by ENGINEERING_SPEC_V1.md section 9 is present.
    """
    rule_id: str = Field(
        ..., description="Fully qualified rule id: dimension.family.vN"
    )
    dimension: str = Field(
        ..., description="Score dimension this rule belongs to"
    )
    version: int = Field(..., ge=1)
    inputs: list[RuleInput]
    conditions: ConditionGroup
    parameters: dict[str, Any] = Field(default_factory=dict)
    severity: Severity
    source_types_required: list[str]
    explanation_template: str = Field(
        ..., description="Human-readable template with {placeholder} refs"
    )


# ---------------------------------------------------------------------------
# Rule fire (output of rule execution)
# ---------------------------------------------------------------------------

class RuleFire(BaseModel):
    """An immutable record of a single rule firing against a member.

    Captures every piece of context required to reproduce and audit the
    fire: sourced facts, derived values, parameters, and run metadata.

    Per the spec:
    - amended disclosures produce a *new* immutable fire linked to the
      superseded filing, never a mutation of a prior fire
    - severity is rule-authored, never changed editorially after the fact
    """
    fire_id: str = Field(
        ..., description="Unique identifier for this fire instance"
    )
    rule_id: str = Field(
        ..., description="Fully qualified rule id that produced this fire"
    )
    rule_version: int = Field(..., ge=1)
    member_bioguide_id: str
    dimension: str
    severity: Severity

    # Evidence payload
    sourced_facts: dict[str, Any] = Field(
        ..., description="Exact sourced facts used at fire time"
    )
    derived_values: dict[str, Any] = Field(
        default_factory=dict,
        description="Values computed from sourced facts",
    )
    parameters_used: dict[str, Any] = Field(
        ..., description="Rule parameters in effect at fire time"
    )

    # Provenance
    recompute_run_id: str
    fired_at: dt.datetime = Field(
        default_factory=lambda: dt.datetime.now(dt.UTC),
        description="Timestamp of rule evaluation",
    )
    superseded_filing_id: str | None = Field(
        default=None,
        description="If this fire was caused by an amendment, the ID of the superseded filing",
    )

    # Explanation
    explanation: str = Field(
        default="",
        description="Rendered explanation from the rule template",
    )
