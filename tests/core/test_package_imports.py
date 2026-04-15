from __future__ import annotations

import importlib
import pathlib


def test_top_level_package_imports():
    """src is importable as a package."""
    mod = importlib.import_module("src")
    assert hasattr(mod, "__file__")


def test_py_typed_marker_exists():
    """PEP 561 py.typed marker is present so typed consumers can rely on it."""
    src_dir = pathlib.Path(__file__).resolve().parents[2] / "src"
    marker = src_dir / "py.typed"
    assert marker.exists(), f"py.typed not found at {marker}"


def test_core_subpackage_imports():
    """Core subpackages import without error."""
    importlib.import_module("src.core")
    importlib.import_module("src.db")
    importlib.import_module("src.runtime")


def test_package_has_init():
    """Top-level src/__init__.py exists."""
    src_dir = pathlib.Path(__file__).resolve().parents[2] / "src"
    init = src_dir / "__init__.py"
    assert init.exists(), f"__init__.py not found at {init}"
