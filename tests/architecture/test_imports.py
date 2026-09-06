"""Library imports must not depend on the caller's directory or acquire data."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

_SOURCE = Path(__file__).resolve().parents[2] / "src"


def test_all_library_modules_import_from_an_empty_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for path in sorted(_SOURCE.rglob("*.py")):
        if path.name == "__main__.py":
            continue
        parts = path.relative_to(_SOURCE).with_suffix("").parts
        module = ".".join(("src", *parts)).removesuffix(".__init__")
        importlib.import_module(module)
    assert list(tmp_path.iterdir()) == []


def test_declared_argparse_entrypoints_offer_help_without_data_or_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    checked = []
    for path in sorted(_SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        main = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main"
            ),
            None,
        )
        if main is None or not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "parse_args"
            for node in ast.walk(main)
        ):
            continue
        parts = path.relative_to(_SOURCE).with_suffix("").parts
        module = importlib.import_module(".".join(("src", *parts)))
        with pytest.raises(SystemExit) as caught:
            module.main(["--help"])
        assert caught.value.code == 0, path
        assert "usage:" in capsys.readouterr().out, path
        checked.append(path)
    assert len(checked) >= 30
    assert list(tmp_path.iterdir()) == []
