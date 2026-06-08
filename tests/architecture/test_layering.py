"""Architecture test: enforce dependency layering (pillar 13).

Foundation/domain packages must never depend on the orchestration/product
layer (runtime/api/pipeline), and src.core must be pure foundation. This guards
the layering as the codebase grows.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+(src\.[a-zA-Z0-9_.]+)", re.MULTILINE)

_ORCHESTRATION = {"runtime", "api", "pipeline"}
_GUARDED = sorted(
    {
        "core",
        "db",
        "provenance",
        "rules",
        "scoring",
        "normalize",
        "ingest",
        "parse",
        "evidence",
        "graph",
        "identity",
        "ontology",
        "zip",
        "query",
        "load",
        "prediction",
        "homepage",
        "feed",
    }
)
# Note: src.export intentionally shares contract types with src.api (export
# imports src.api.contracts), so it is not guarded against the orchestration
# layer here.


def _imported_src_packages(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    packages: set[str] = set()
    for match in _IMPORT_RE.finditer(text):
        parts = match.group(1).split(".")
        if len(parts) >= 2:
            packages.add(parts[1])
    return packages


@pytest.mark.parametrize("package", _GUARDED)
def test_guarded_package_does_not_depend_on_orchestration(package: str) -> None:
    violations: list[str] = []
    for file in (_SRC / package).rglob("*.py"):
        offending = _imported_src_packages(file) & _ORCHESTRATION
        if offending:
            violations.append(f"{file.relative_to(_SRC)} imports {sorted(offending)}")
    assert not violations, "Layering violation (domain depends on orchestration):\n" + "\n".join(
        violations
    )


def test_core_is_pure_foundation() -> None:
    for file in (_SRC / "core").rglob("*.py"):
        for package in _imported_src_packages(file):
            assert package == "core", (
                f"{file.relative_to(_SRC)} imports src.{package}; core must be foundation-only"
            )


def test_prediction_does_not_depend_on_conflict_rule_engine() -> None:
    # Prediction and conflict-scoring are separate products. Prediction may share
    # the cross-cutting source-anchor policy utility (src.evidence.source_anchor_policy)
    # but must not depend on the conflict rule engine.
    for file in (_SRC / "prediction").rglob("*.py"):
        assert "rules" not in _imported_src_packages(file), (
            f"{file.relative_to(_SRC)} imports src.rules; prediction must stay decoupled "
            "from the conflict rule engine"
        )


def test_conflict_scoring_does_not_depend_on_prediction() -> None:
    for package in ("rules", "evidence"):
        for file in (_SRC / package).rglob("*.py"):
            assert "prediction" not in _imported_src_packages(file), (
                f"{file.relative_to(_SRC)} imports src.prediction; conflict scoring must "
                "stay decoupled from prediction"
            )
