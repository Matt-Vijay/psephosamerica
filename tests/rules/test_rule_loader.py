"""Tests for the rule loader and model validation."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from src.rules.loader import (
    RuleLoadError,
    load_rule,
    load_rules_from_directory,
    validate_rule_set,
)
from src.rules.models import RuleDefinition, Severity

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RULES_DIR = Path(__file__).resolve().parents[2] / "src" / "rules" / "conflict_of_interest"

MINIMAL_RULE_YAML = dedent("""\
    rule_id: test.minimal.v1
    dimension: test
    version: 1
    inputs:
      - name: some_input
        required: true
    conditions:
      missing_data_policy: no_fire
      all_of:
        - fact: x
          operator: gte
          value: 1
    parameters:
      threshold: 10
    severity: low
    source_types_required:
      - test_source
    explanation_template: "Test rule fired."
""")


@pytest.fixture()
def tmp_rule(tmp_path: Path) -> Path:
    """Write a minimal valid rule YAML and return its path."""
    p = tmp_path / "test_rule.yaml"
    p.write_text(MINIMAL_RULE_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def tmp_rules_dir(tmp_path: Path) -> Path:
    """Create a temp directory with two valid rule YAMLs."""
    for name in ("rule_a.yaml", "rule_b.yaml"):
        data = yaml.safe_load(MINIMAL_RULE_YAML)
        data["rule_id"] = f"test.{name.removesuffix('.yaml')}.v1"
        (tmp_path / name).write_text(yaml.dump(data), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# load_rule
# ---------------------------------------------------------------------------


class TestLoadRule:
    def test_load_minimal(self, tmp_rule: Path) -> None:
        rule = load_rule(tmp_rule)
        assert isinstance(rule, RuleDefinition)
        assert rule.rule_id == "test.minimal.v1"
        assert rule.severity == Severity.low
        assert rule.version == 1

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(RuleLoadError, match="does not exist"):
            load_rule(tmp_path / "nope.yaml")

    def test_bad_extension(self, tmp_path: Path) -> None:
        p = tmp_path / "rule.json"
        p.write_text("{}", encoding="utf-8")
        with pytest.raises(RuleLoadError, match="unexpected extension"):
            load_rule(p)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text(":::\n  - ][", encoding="utf-8")
        with pytest.raises(RuleLoadError, match="YAML parse error"):
            load_rule(p)

    def test_validation_error(self, tmp_path: Path) -> None:
        p = tmp_path / "incomplete.yaml"
        p.write_text("rule_id: missing_fields\n", encoding="utf-8")
        with pytest.raises(RuleLoadError, match="validation error"):
            load_rule(p)


# ---------------------------------------------------------------------------
# load_rules_from_directory
# ---------------------------------------------------------------------------


class TestLoadRulesFromDirectory:
    def test_loads_all(self, tmp_rules_dir: Path) -> None:
        rules = load_rules_from_directory(tmp_rules_dir)
        assert len(rules) == 2
        ids = [r.rule_id for r in rules]
        assert ids == sorted(ids)

    def test_not_a_directory(self, tmp_path: Path) -> None:
        p = tmp_path / "file.txt"
        p.write_text("hi", encoding="utf-8")
        with pytest.raises(RuleLoadError, match="not a directory"):
            load_rules_from_directory(p)

    def test_empty_dir(self, tmp_path: Path) -> None:
        rules = load_rules_from_directory(tmp_path)
        assert rules == []


# ---------------------------------------------------------------------------
# validate_rule_set
# ---------------------------------------------------------------------------


class TestValidateRuleSet:
    def test_no_warnings_for_unique_ids(self, tmp_rules_dir: Path) -> None:
        rules = load_rules_from_directory(tmp_rules_dir)
        warnings = validate_rule_set(rules)
        assert warnings == []

    def test_duplicate_ids_warned(self, tmp_rule: Path) -> None:
        rule = load_rule(tmp_rule)
        warnings = validate_rule_set([rule, rule])
        assert any("duplicate" in w for w in warnings)


# ---------------------------------------------------------------------------
# Real YAML rule files (integration)
# ---------------------------------------------------------------------------


class TestRealRules:
    """Load the actual checked-in YAML files and verify structural properties."""

    @pytest.mark.skipif(
        not RULES_DIR.is_dir(),
        reason="conflict_of_interest rules directory not present",
    )
    def test_load_all_conflict_of_interest_rules(self) -> None:
        rules = load_rules_from_directory(RULES_DIR)
        assert len(rules) == 4
        for rule in rules:
            assert rule.dimension == "conflict_of_interest_risk"
            assert rule.version == 1
            assert len(rule.inputs) >= 1
            assert len(rule.source_types_required) >= 1
            assert rule.explanation_template

    @pytest.mark.skipif(
        not RULES_DIR.is_dir(),
        reason="conflict_of_interest rules directory not present",
    )
    def test_no_duplicate_rule_ids(self) -> None:
        rules = load_rules_from_directory(RULES_DIR)
        warnings = validate_rule_set(rules)
        assert warnings == []

    @pytest.mark.skipif(
        not RULES_DIR.is_dir(),
        reason="conflict_of_interest rules directory not present",
    )
    def test_repeated_trading_is_threshold_based(self) -> None:
        """Locked v1 decision: threshold-based, not statistical."""
        rules = load_rules_from_directory(RULES_DIR)
        repeated = [r for r in rules if "repeated" in r.rule_id]
        assert len(repeated) == 1
        r = repeated[0]
        assert "minimum_matching_transactions" in r.parameters
        assert r.severity == Severity.high
