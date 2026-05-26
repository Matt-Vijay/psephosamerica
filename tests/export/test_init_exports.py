"""Tests for the lazy ``__getattr__`` export mechanism in src/export/__init__.py."""

from __future__ import annotations

import importlib

import pytest

import src.export as export_pkg
from src.export import _LAZY_EXPORTS


@pytest.mark.parametrize("name", sorted(_LAZY_EXPORTS))
def test_lazy_export_resolves_to_target_module_attribute(name: str) -> None:
    target = importlib.import_module(_LAZY_EXPORTS[name])
    assert getattr(export_pkg, name) is getattr(target, name)


def test_every_lazy_export_is_listed_in_dunder_all() -> None:
    # Guards against a lazy export being added without a public __all__ entry.
    assert set(_LAZY_EXPORTS).issubset(set(export_pkg.__all__))


def test_unknown_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="has no attribute 'does_not_exist'"):
        _ = export_pkg.does_not_exist  # type: ignore[attr-defined]


def test_lazy_export_is_cached_into_module_globals_after_first_access() -> None:
    # The first access binds the name into the module globals so subsequent
    # lookups bypass __getattr__ entirely.
    name = "plan_snapshot"
    vars(export_pkg).pop(name, None)
    resolved = getattr(export_pkg, name)
    assert vars(export_pkg)[name] is resolved
