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

The complete legacy command list comes from the parser, not a second hand-maintained
inventory. These read-only examples do not require a database:

```bash
python3 -m src.runtime.main --help
python3 -m src.runtime.main bootstrap-db --dry-run
```

### Environment

- Python 3.12.
- Install with dev tooling: `pip install -e ".[dev]"`.
- The pinned local semantic model is optional: `pip install -e ".[dev,enrichment]"`.
  Only the full unit-test CI job installs it; lint, compilation, typing and
  integration checks do not need Torch or Sentence Transformers. Tests of the
  real library-loading boundary require this extra even though they mock model inference.
- The package lives under `src/` and is importable as `src.*`
  (`pythonpath = ["src"]` in `pyproject.toml`).

### Quality gates

CI runs these gates. Locally, run the affected suites and broaden the check only
when the change crosses package boundaries.

| Gate | Command | Notes |
|---|---|---|
| Lint | `python -m ruff check src/ tests/` | |
| Format | `python -m ruff format --check src/ tests/` | run `ruff format` to fix |
| Security | `python -m bandit -q -c bandit.yml -r src/` | suppressions must be justified `# nosec <ID>` |
| Compile | `python -m compileall -q src/ tests/` | |
| Types | `python -m mypy --config-file pyproject.toml` | strict mode |
| Unit tests + coverage | `python -m coverage run -m pytest tests/ -q --ignore=tests/integration && python -m coverage report --fail-under=90` | full environment, including enrichment; branch coverage |
| Integration tests | `python -m pytest tests/integration/ -q` | requires Postgres (see below) |

### Coverage

Coverage is configured in `[tool.coverage.*]` (`pyproject.toml`): `source = src`,
branch coverage on, `__main__` entry glue excluded. The unit-test CI job enforces
`--fail-under=90`. When you raise overall coverage, ratchet the threshold up so it
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

- **Small explicit surfaces.** Public runtime commands are ordinary re-exports;
  tests patch collaborators in the command's owning module, never a forwarding facade.
- **Stable tooling.** Ruff is pinned and its rule set is explicit: error/undefined-name
  checks, import sorting, Python modernization, and Bugbear. Runtime enum and generic
  type migrations are excluded because they can change APIs. Strict mypy checks our
  code; only optional external imports and PyArrow's untyped Parquet calls are exempt.

- **Tests before behavior changes.** Add a focused failing test, then make it pass.
- **SQL.** Queries are parameterized (`%s`); shared SQL fragments come from helpers
  such as `member_active_on_sql`. Multi-line f-string SQL that interpolates only
  trusted static fragments is annotated `# nosec B608` on the closing `"""` line.
  `try/except/continue` is annotated `# nosec B112` when intentional.
- **Determinism & cutoff safety.** Prediction features must never use data dated
  after the feature cutoff; see [ADR 0001](adr/0001-cutoff-safe-no-leakage-prediction.md).
- **Provenance.** Raw inputs and published snapshots are immutable; see
  `METHODOLOGY.md`.
