from __future__ import annotations

import pathlib

try:
    if True:  # avoid mypy thinking this is unreachable
        import tomllib
except ModuleNotFoundError:
    tomllib = None  # type: ignore[assignment]

ROOT = pathlib.Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"


def _load_mypy_config() -> dict:
    """Load [tool.mypy] from pyproject.toml."""
    assert PYPROJECT.exists(), f"pyproject.toml not found at {PYPROJECT}"
    if tomllib is not None:
        with open(PYPROJECT, "rb") as f:
            data = tomllib.load(f)
        return data.get("tool", {}).get("mypy", {})
    # fallback: should not happen on 3.12+
    raise RuntimeError("tomllib unavailable")


def test_mypy_config_exists():
    """pyproject.toml has a [tool.mypy] section."""
    cfg = _load_mypy_config()
    assert cfg, "[tool.mypy] section is empty or missing"


def test_mypy_targets_src():
    """mypy is configured to check the src package."""
    cfg = _load_mypy_config()
    packages = cfg.get("packages", [])
    mypy_path = cfg.get("mypy_path", [])
    assert "src" in packages, f"'src' not in mypy packages: {packages}"
    assert "src" in mypy_path, f"'src' not in mypy_path: {mypy_path}"


def test_mypy_python_version():
    """mypy python_version matches project requires-python."""
    cfg = _load_mypy_config()
    assert cfg.get("python_version") == "3.12"


def test_mypy_strict_mode():
    """mypy is configured in strict mode."""
    cfg = _load_mypy_config()
    assert cfg.get("strict") is True


def test_mypy_in_dev_dependencies():
    """mypy is listed in dev dependencies."""
    assert PYPROJECT.exists()
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    dev_deps = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    assert any("mypy" in dep for dep in dev_deps), f"mypy not in dev deps: {dev_deps}"
