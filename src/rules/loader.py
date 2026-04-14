"""Load and validate rule YAML files from disk.

The loader reads YAML files, validates them against the RuleDefinition
model, and returns typed objects ready for the execution engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import yaml
from pydantic import ValidationError

from src.rules.models import RuleDefinition


class RuleLoadError(Exception):
    """Raised when a rule file cannot be loaded or fails validation."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


def load_rule(path: Path) -> RuleDefinition:
    """Load a single rule YAML file and return a validated RuleDefinition.

    Raises RuleLoadError on I/O or validation failure.
    """
    if not path.exists():
        raise RuleLoadError(path, "file does not exist")
    if path.suffix not in (".yaml", ".yml"):
        raise RuleLoadError(path, f"unexpected extension: {path.suffix}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuleLoadError(path, f"YAML parse error: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuleLoadError(path, "expected a YAML mapping at the top level")

    try:
        return RuleDefinition.model_validate(raw)
    except ValidationError as exc:
        raise RuleLoadError(path, f"validation error: {exc}") from exc


def load_rules_from_directory(directory: Path) -> list[RuleDefinition]:
    """Recursively discover and load all .yaml rule files under *directory*.

    Returns a list of validated RuleDefinition objects sorted by rule_id.
    Raises RuleLoadError on the first file that fails.
    """
    if not directory.is_dir():
        raise RuleLoadError(directory, "not a directory")

    paths = sorted(directory.rglob("*.yaml"))
    rules: list[RuleDefinition] = []
    for p in paths:
        rules.append(load_rule(p))
    return sorted(rules, key=lambda r: r.rule_id)


def validate_rule_set(rules: Sequence[RuleDefinition]) -> list[str]:
    """Run cross-rule consistency checks.

    Returns a list of warning strings (empty if all checks pass).
    This does *not* raise — callers decide whether warnings are fatal.
    """
    warnings: list[str] = []

    # Check for duplicate rule_ids
    seen_ids: dict[str, int] = {}
    for rule in rules:
        seen_ids[rule.rule_id] = seen_ids.get(rule.rule_id, 0) + 1
    for rid, count in seen_ids.items():
        if count > 1:
            warnings.append(f"duplicate rule_id: {rid} appears {count} times")

    return warnings
