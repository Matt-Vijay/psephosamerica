from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence, cast

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from src.rules.models import RuleDefinition


class RuleLoadError(Exception):
    """Raised when a rule file cannot be loaded or fails validation."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


def load_rule(path: Path) -> RuleDefinition:
    """Load and validate one rule YAML file. Raises RuleLoadError on any failure."""
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
        return RuleDefinition.model_validate(cast(dict[str, Any], raw))
    except ValidationError as exc:
        raise RuleLoadError(path, f"validation error: {exc}") from exc


def load_rules_from_directory(directory: Path) -> list[RuleDefinition]:
    """Recursively load all .yaml rule files under *directory*, sorted by rule_id.

    Raises RuleLoadError on the first file that fails.
    """
    if not directory.is_dir():
        raise RuleLoadError(directory, "not a directory")

    rules: list[RuleDefinition] = [
        load_rule(p) for p in sorted(directory.rglob("*.yaml"))
    ]
    return sorted(rules, key=lambda r: r.rule_id)


def validate_rule_set(rules: Sequence[RuleDefinition]) -> list[str]:
    """Return cross-rule warning strings. Does not raise — callers decide if fatal."""
    warnings: list[str] = []
    seen_ids: dict[str, int] = {}
    for rule in rules:
        seen_ids[rule.rule_id] = seen_ids.get(rule.rule_id, 0) + 1
    for rid, count in seen_ids.items():
        if count > 1:
            warnings.append(f"duplicate rule_id: {rid} appears {count} times")
    return warnings
