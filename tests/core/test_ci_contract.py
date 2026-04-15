"""Contract tests: the CI workflow file exists and declares expected jobs/steps."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load_ci() -> dict:
    assert CI_PATH.exists(), f"CI workflow not found at {CI_PATH}"
    return yaml.safe_load(CI_PATH.read_text())


class TestCIWorkflowExists:
    def test_file_exists(self) -> None:
        assert CI_PATH.is_file()

    def test_valid_yaml(self) -> None:
        data = _load_ci()
        assert isinstance(data, dict)


class TestCIJobs:
    def test_expected_jobs_present(self) -> None:
        data = _load_ci()
        jobs = set(data.get("jobs", {}).keys())
        expected = {"lint", "compile", "typecheck", "test-unit", "test-integration"}
        assert expected.issubset(jobs), f"Missing jobs: {expected - jobs}"

    def test_lint_runs_ruff(self) -> None:
        data = _load_ci()
        steps = data["jobs"]["lint"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert any("ruff" in n.lower() for n in step_names)

    def test_compile_runs_compileall(self) -> None:
        data = _load_ci()
        steps = data["jobs"]["compile"]["steps"]
        runs = " ".join(s.get("run", "") for s in steps)
        assert "compileall" in runs

    def test_typecheck_runs_mypy(self) -> None:
        data = _load_ci()
        steps = data["jobs"]["typecheck"]["steps"]
        runs = " ".join(s.get("run", "") for s in steps)
        assert "mypy" in runs

    def test_unit_runs_pytest(self) -> None:
        data = _load_ci()
        steps = data["jobs"]["test-unit"]["steps"]
        runs = " ".join(s.get("run", "") for s in steps)
        assert "pytest" in runs

    def test_integration_has_postgres_service(self) -> None:
        data = _load_ci()
        job = data["jobs"]["test-integration"]
        services = job.get("services", {})
        assert "postgres" in services

    def test_integration_sets_dsn_env(self) -> None:
        data = _load_ci()
        job = data["jobs"]["test-integration"]
        env = job.get("env", {})
        assert "OPENPACT_POSTGRES_DSN" in env

    def test_integration_runs_pytest(self) -> None:
        data = _load_ci()
        steps = data["jobs"]["test-integration"]["steps"]
        runs = " ".join(s.get("run", "") for s in steps)
        assert "pytest" in runs
        assert "integration" in runs
