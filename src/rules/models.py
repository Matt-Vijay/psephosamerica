from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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
    no_fire = "no_fire"


class RuleInput(BaseModel):
    name: str
    required: bool = True
    description: str = ""


class Condition(BaseModel):
    fact: str
    operator: Operator
    value: Any = None
    value_ref: str | None = None


class ConditionGroup(BaseModel):
    """One level of nesting is supported: a top-level group may contain nested groups."""
    missing_data_policy: MissingDataPolicy = MissingDataPolicy.no_fire
    all_of: list[Condition | ConditionGroup] | None = None
    any_of: list[Condition | ConditionGroup] | None = None


class RuleDefinition(BaseModel):
    rule_id: str = Field(..., description="Fully qualified rule id: dimension.family.vN")
    dimension: str
    version: int = Field(..., ge=1)
    inputs: list[RuleInput]
    conditions: ConditionGroup
    parameters: dict[str, Any] = Field(default_factory=dict)
    severity: Severity
    source_types_required: list[str]
    explanation_template: str


class RuleFire(BaseModel):
    """Immutable record of a single rule firing.

    - Amended disclosures produce a new fire linked to the superseded filing,
      never a mutation of a prior fire.
    - Severity is rule-authored, never changed editorially after the fact.
    """
    fire_id: str
    rule_id: str
    rule_version: int = Field(..., ge=1)
    member_bioguide_id: str
    dimension: str
    severity: Severity

    sourced_facts: dict[str, Any]
    derived_values: dict[str, Any] = Field(default_factory=dict)
    parameters_used: dict[str, Any]

    recompute_run_id: str
    fired_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    superseded_filing_id: str | None = Field(
        default=None,
        description="Set when this fire was triggered by an amendment; links to the replaced filing.",
    )

    explanation: str = ""
