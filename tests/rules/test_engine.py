"""Tests for src/rules/engine.py.

All tests are deterministic and make no network calls.
Real YAML files on disk are used for load/filter smoke tests only.
"""

from __future__ import annotations

from typing import Any

from src.rules.engine import (
    filter_rules,
    load_canonical_rules,
    run_batch,
    run_member_batch,
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
# Shared helpers
# ---------------------------------------------------------------------------

MEMBER_A = "A000001"
MEMBER_B = "B000002"
RUN = "run-batch-test"


def _cond(fact: str, op: Operator, value: Any = None) -> Condition:
    return Condition(fact=fact, operator=op, value=value)


def _all_of(*items: Condition | ConditionGroup) -> ConditionGroup:
    return ConditionGroup(missing_data_policy=MissingDataPolicy.no_fire, all_of=list(items))


def _make_rule(
    conditions: ConditionGroup,
    *,
    rule_id: str = "conflict_of_interest_risk.test_family.v1",
    dimension: str = "conflict_of_interest_risk",
    severity: Severity = Severity.medium,
    parameters: dict[str, Any] | None = None,
) -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        dimension=dimension,
        version=1,
        inputs=[RuleInput(name="fact_a", required=True)],
        conditions=conditions,
        parameters=parameters or {},
        severity=severity,
        source_types_required=["financial_disclosure"],
        explanation_template="fired for {fact_a}",
    )


# A simple always-fire rule (vacuous conditions).
_VACUOUS_RULE = _make_rule(
    ConditionGroup(missing_data_policy=MissingDataPolicy.no_fire),
    rule_id="conflict_of_interest_risk.test_family.v1",
)

# A rule that fires only when fact_a >= 10.
_THRESHOLD_RULE = _make_rule(
    _all_of(_cond("fact_a", Operator.gte, 10)),
    rule_id="conflict_of_interest_risk.other_family.v1",
)


# ---------------------------------------------------------------------------
# load_canonical_rules
# ---------------------------------------------------------------------------

class TestLoadCanonicalRules:
    def test_returns_nonempty_list(self):
        rules = load_canonical_rules()
        assert len(rules) > 0

    def test_all_are_rule_definitions(self):
        rules = load_canonical_rules()
        for r in rules:
            assert isinstance(r, RuleDefinition)

    def test_sorted_by_rule_id(self):
        rules = load_canonical_rules()
        ids = [r.rule_id for r in rules]
        assert ids == sorted(ids)

    def test_known_rule_ids_present(self):
        rules = load_canonical_rules()
        ids = {r.rule_id for r in rules}
        assert "conflict_of_interest_risk.committee_sector_trade.v1" in ids

    def test_all_rules_have_conditions(self):
        rules = load_canonical_rules()
        for r in rules:
            assert r.conditions is not None


# ---------------------------------------------------------------------------
# filter_rules
# ---------------------------------------------------------------------------

class TestFilterRules:
    def _sample_rules(self) -> list[RuleDefinition]:
        """Three rules across two families."""
        r1 = _make_rule(
            _all_of(_cond("x", Operator.gte, 1)),
            rule_id="conflict_of_interest_risk.alpha.v1",
        )
        r2 = _make_rule(
            _all_of(_cond("x", Operator.gte, 1)),
            rule_id="conflict_of_interest_risk.beta.v1",
        )
        r3 = _make_rule(
            _all_of(_cond("x", Operator.gte, 1)),
            rule_id="conflict_of_interest_risk.alpha.v2",
        )
        return [r1, r2, r3]

    def test_no_filters_returns_all(self):
        rules = self._sample_rules()
        assert filter_rules(rules) == rules

    def test_filter_by_family(self):
        rules = self._sample_rules()
        result = filter_rules(rules, family="alpha")
        assert len(result) == 2
        assert all("alpha" in r.rule_id for r in result)

    def test_filter_by_exact_rule_id(self):
        rules = self._sample_rules()
        result = filter_rules(rules, rule_id="conflict_of_interest_risk.beta.v1")
        assert len(result) == 1
        assert result[0].rule_id == "conflict_of_interest_risk.beta.v1"

    def test_filter_by_family_and_rule_id_combined(self):
        rules = self._sample_rules()
        # Both filters: family=alpha AND rule_id=alpha.v1
        result = filter_rules(
            rules,
            family="alpha",
            rule_id="conflict_of_interest_risk.alpha.v1",
        )
        assert len(result) == 1
        assert result[0].rule_id == "conflict_of_interest_risk.alpha.v1"

    def test_filter_nonexistent_family_returns_empty(self):
        rules = self._sample_rules()
        assert filter_rules(rules, family="does_not_exist") == []

    def test_filter_nonexistent_rule_id_returns_empty(self):
        rules = self._sample_rules()
        assert filter_rules(rules, rule_id="x.y.v99") == []

    def test_filter_empty_input_returns_empty(self):
        assert filter_rules([], family="alpha") == []

    def test_filter_canonical_rules_by_family(self):
        rules = load_canonical_rules()
        result = filter_rules(rules, family="committee_sector_trade")
        assert len(result) >= 1
        for r in result:
            assert "committee_sector_trade" in r.rule_id


# ---------------------------------------------------------------------------
# run_member_batch
# ---------------------------------------------------------------------------

class TestRunMemberBatch:
    def test_empty_contexts_returns_empty(self):
        fires = run_member_batch([_VACUOUS_RULE], MEMBER_A, [], RUN)
        assert fires == []

    def test_empty_rules_returns_empty(self):
        fires = run_member_batch([], MEMBER_A, [{"fact_a": 1}], RUN)
        assert fires == []

    def test_vacuous_rule_fires_on_every_context(self):
        contexts = [{}, {}, {}]
        fires = run_member_batch([_VACUOUS_RULE], MEMBER_A, contexts, RUN)
        assert len(fires) == 3

    def test_threshold_rule_fires_only_when_met(self):
        contexts = [{"fact_a": 5}, {"fact_a": 10}, {"fact_a": 15}]
        fires = run_member_batch([_THRESHOLD_RULE], MEMBER_A, contexts, RUN)
        assert len(fires) == 2
        for f in fires:
            assert f.sourced_facts["fact_a"] >= 10

    def test_all_fires_tagged_with_member_id(self):
        contexts = [{"fact_a": 10}, {"fact_a": 20}]
        fires = run_member_batch([_THRESHOLD_RULE], MEMBER_A, contexts, RUN)
        for f in fires:
            assert f.member_bioguide_id == MEMBER_A

    def test_all_fires_tagged_with_run_id(self):
        contexts = [{"fact_a": 10}]
        fires = run_member_batch([_THRESHOLD_RULE], MEMBER_A, contexts, RUN)
        for f in fires:
            assert f.recompute_run_id == RUN

    def test_returns_rule_fire_instances(self):
        contexts = [{"fact_a": 10}]
        fires = run_member_batch([_THRESHOLD_RULE], MEMBER_A, contexts, RUN)
        assert all(isinstance(f, RuleFire) for f in fires)

    def test_multiple_rules_multiple_contexts(self):
        rules = [_VACUOUS_RULE, _THRESHOLD_RULE]
        contexts = [{"fact_a": 10}, {"fact_a": 1}]
        fires = run_member_batch(rules, MEMBER_A, contexts, RUN)
        # _VACUOUS_RULE fires on both contexts (2),
        # _THRESHOLD_RULE fires on {"fact_a": 10} only (1) → total 3
        assert len(fires) == 3

    def test_fire_ids_are_unique(self):
        contexts = [{"fact_a": 10}, {"fact_a": 20}]
        fires = run_member_batch([_THRESHOLD_RULE], MEMBER_A, contexts, RUN)
        ids = [f.fire_id for f in fires]
        assert len(ids) == len(set(ids))

    def test_superseded_filing_id_propagated(self):
        contexts = [{"fact_a": 10}]
        fires = run_member_batch(
            [_THRESHOLD_RULE],
            MEMBER_A,
            contexts,
            RUN,
            superseded_filing_id="fd-superseded-99",
        )
        assert len(fires) == 1
        assert fires[0].superseded_filing_id == "fd-superseded-99"

    def test_no_fire_on_missing_fact(self):
        contexts = [{}]  # fact_a absent
        fires = run_member_batch([_THRESHOLD_RULE], MEMBER_A, contexts, RUN)
        assert fires == []

    def test_context_mutation_after_call_does_not_affect_fires(self):
        ctx: dict[str, Any] = {"fact_a": 10}
        fires = run_member_batch([_THRESHOLD_RULE], MEMBER_A, [ctx], RUN)
        ctx["fact_a"] = 999
        assert fires[0].sourced_facts["fact_a"] == 10


# ---------------------------------------------------------------------------
# run_batch (multi-member)
# ---------------------------------------------------------------------------

class TestRunBatch:
    def test_empty_member_contexts_returns_empty(self):
        fires = run_batch([_VACUOUS_RULE], {}, RUN)
        assert fires == []

    def test_empty_rules_returns_empty(self):
        fires = run_batch([], {MEMBER_A: [{}]}, RUN)
        assert fires == []

    def test_fires_produced_for_each_member(self):
        member_contexts = {
            MEMBER_A: [{"fact_a": 10}],
            MEMBER_B: [{"fact_a": 20}],
        }
        fires = run_batch([_THRESHOLD_RULE], member_contexts, RUN)
        assert len(fires) == 2
        fired_members = {f.member_bioguide_id for f in fires}
        assert fired_members == {MEMBER_A, MEMBER_B}

    def test_member_with_no_firing_contexts_contributes_nothing(self):
        member_contexts = {
            MEMBER_A: [{"fact_a": 1}],   # below threshold, no fire
            MEMBER_B: [{"fact_a": 20}],  # fires
        }
        fires = run_batch([_THRESHOLD_RULE], member_contexts, RUN)
        assert len(fires) == 1
        assert fires[0].member_bioguide_id == MEMBER_B

    def test_member_with_empty_context_list_contributes_nothing(self):
        member_contexts = {MEMBER_A: [], MEMBER_B: [{"fact_a": 10}]}
        fires = run_batch([_VACUOUS_RULE], member_contexts, RUN)
        assert len(fires) == 1
        assert fires[0].member_bioguide_id == MEMBER_B

    def test_all_fires_tagged_with_run_id(self):
        member_contexts = {MEMBER_A: [{"fact_a": 10}], MEMBER_B: [{"fact_a": 10}]}
        fires = run_batch([_THRESHOLD_RULE], member_contexts, RUN)
        for f in fires:
            assert f.recompute_run_id == RUN

    def test_many_members_many_contexts(self):
        members = {f"M{i:04d}": [{"fact_a": 10 + i}] for i in range(5)}
        fires = run_batch([_THRESHOLD_RULE], members, RUN)
        # All 5 members have fact_a >= 10, so all should fire
        assert len(fires) == 5
        fired_members = {f.member_bioguide_id for f in fires}
        assert fired_members == set(members.keys())

    def test_returns_flat_list_of_rule_fires(self):
        member_contexts = {MEMBER_A: [{"fact_a": 10}]}
        fires = run_batch([_THRESHOLD_RULE], member_contexts, RUN)
        assert isinstance(fires, list)
        assert all(isinstance(f, RuleFire) for f in fires)

    def test_aggregates_across_multiple_rules(self):
        rules = [_VACUOUS_RULE, _THRESHOLD_RULE]
        member_contexts = {MEMBER_A: [{"fact_a": 10}]}
        fires = run_batch(rules, member_contexts, RUN)
        # vacuous fires + threshold fires = 2
        assert len(fires) == 2
        rule_ids = {f.rule_id for f in fires}
        assert _VACUOUS_RULE.rule_id in rule_ids
        assert _THRESHOLD_RULE.rule_id in rule_ids
