"""Tests that release-tooling config files exist and contain expected content."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# .pre-commit-config.yaml
# ---------------------------------------------------------------------------


class TestPreCommitConfig:
    config_path = REPO_ROOT / ".pre-commit-config.yaml"

    def test_file_exists(self) -> None:
        assert self.config_path.is_file(), ".pre-commit-config.yaml missing"

    def test_parses_as_yaml(self) -> None:
        data = yaml.safe_load(self.config_path.read_text())
        assert isinstance(data, dict)
        assert "repos" in data

    def test_has_ruff_hook(self) -> None:
        data = yaml.safe_load(self.config_path.read_text())
        hook_ids = _collect_hook_ids(data)
        assert "ruff" in hook_ids
        assert "ruff-format" in hook_ids

    def test_has_mypy_hook(self) -> None:
        data = yaml.safe_load(self.config_path.read_text())
        hook_ids = _collect_hook_ids(data)
        assert "mypy" in hook_ids

    def test_has_bandit_hook(self) -> None:
        data = yaml.safe_load(self.config_path.read_text())
        hook_ids = _collect_hook_ids(data)
        assert "bandit" in hook_ids


# ---------------------------------------------------------------------------
# bandit.yml
# ---------------------------------------------------------------------------


class TestBanditConfig:
    config_path = REPO_ROOT / "bandit.yml"

    def test_file_exists(self) -> None:
        assert self.config_path.is_file(), "bandit.yml missing"

    def test_parses_as_yaml(self) -> None:
        data = yaml.safe_load(self.config_path.read_text())
        assert isinstance(data, dict)

    def test_excludes_tests_directory(self) -> None:
        data = yaml.safe_load(self.config_path.read_text())
        assert "tests" in data.get("exclude_dirs", [])

    def test_has_skips_section(self) -> None:
        data = yaml.safe_load(self.config_path.read_text())
        assert "skips" in data
        assert isinstance(data["skips"], list)
        assert len(data["skips"]) > 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _collect_hook_ids(data: dict) -> set[str]:
    ids: set[str] = set()
    for repo in data.get("repos", []):
        for hook in repo.get("hooks", []):
            hook_id = hook.get("id")
            if hook_id:
                ids.add(hook_id)
    return ids
