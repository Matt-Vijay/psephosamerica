# Development Guide

Use checks proportionate to the area changed. The large historical backend CI
is documented separately below; it is not the local loop for RegPatch work.

## RegPatch verification

```bash
.venv/bin/pip install -e . pytest ruff mypy types-defusedxml
.venv/bin/pytest -q tests/test_regpatch_compiler.py tests/test_regpatch_grader.py tests/test_regpatch_runner.py tests/test_regpatch_cli.py
.venv/bin/ruff check src/regpatch tests/test_regpatch_*.py
.venv/bin/ruff format --check src/regpatch tests/test_regpatch_*.py
.venv/bin/mypy --strict src/regpatch
```

The runner tests require the trusted Deno path described in the
[operator guide](../src/regpatch/README.md). Run `psephos-regpatch demo` in a
fresh temporary directory to check public end-to-end behavior. Changes to
projection or scoring require remeasuring affected scores, not changing tests
to fit them. Corpus checks use the already-retained ignored evaluator store.

## Legacy backend CI

### Environment

- Python 3.12.
- Install with dev tooling: `pip install -e ".[dev]"`.
- The package lives under `src/` and is importable as `src.*`
  (`pythonpath = ["src"]` in `pyproject.toml`).

### Quality gates

Run all of these before pushing; CI runs the same set and blocks merge on any
failure.

| Gate | Command | Notes |
|---|---|---|
| Lint | `python -m ruff check src/ tests/` | |
| Format | `python -m ruff format --check src/ tests/` | run `ruff format` to fix |
| Security | `python -m bandit -q -c bandit.yml -r src/` | suppressions must be justified `# nosec <ID>` |
| Compile | `python -m compileall -q src/ tests/` | |
| Types | `python -m mypy --config-file pyproject.toml` | strict mode |
| Unit tests + coverage | `python -m coverage run -m pytest tests/ -q --ignore=tests/integration && python -m coverage report --fail-under=89` | branch coverage; gate fails below the threshold |
| Integration tests | `python -m pytest tests/integration/ -q` | requires Postgres (see below) |

### Coverage

Coverage is configured in `[tool.coverage.*]` (`pyproject.toml`): `source = src`,
branch coverage on, `__main__` entry glue excluded. The unit-test CI job enforces
`--fail-under=89`. When you raise overall coverage, ratchet the threshold up so it
keeps protecting the gain. To see what is missing locally:

```
python -m coverage run -m pytest tests/ -q --ignore=tests/integration
python -m coverage report --sort=cover --skip-covered
```

### Integration tests and Postgres

Integration tests under `tests/integration/` are skipped unless
`PSEPHOS_TEST_POSTGRES_DSN` is set. CI provides a `postgres:16` service; locally:

```
export PSEPHOS_TEST_POSTGRES_DSN="postgresql://psephosamerica:psephosamerica@localhost:5432/psephosamerica_test"
python -m pytest tests/integration/ -q
```

## Conventions

- **Tests before behavior changes.** Add a focused failing test, then make it pass.
- **SQL.** Queries are parameterized (`%s`); shared SQL fragments come from helpers
  such as `member_active_on_sql`. Multi-line f-string SQL that interpolates only
  trusted static fragments is annotated `# nosec B608` on the closing `"""` line.
  `try/except/continue` is annotated `# nosec B112` when intentional.
- **Determinism & cutoff safety.** Prediction features must never use data dated
  after the feature cutoff; see [ADR 0001](adr/0001-cutoff-safe-no-leakage-prediction.md).
- **Provenance.** Raw inputs and published snapshots are immutable; see
  `METHODOLOGY.md`.
