# Changelog

All notable changes to OpenPact land here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — `consolidate-prediction-and-quality-pass`

### Added

- Legislative vote-prediction subsystem (`src/prediction/`, `src/ontology/`,
  `src/runtime/prediction_*`, `src/query/prediction_inventory.py`) wired to
  real Congress archive/member/vote/bill data with cutoff-safe feature
  snapshots, source-anchor audits, eval reports with calibration bins +
  Brier/log-loss, and rolling-window plans.
- CI **coverage gate** (`python -m coverage report --fail-under=90`) with
  `coverage` added to dev deps and `[tool.coverage.*]` config in
  `pyproject.toml`; gate ratcheted from 89 → **90** as coverage rose.
- Comprehensive **property / fuzz test suite** for the safety-critical core:
  cutoff-safety invariant (signal rows, ontology edges, `_validate_no_leakage`),
  House and Senate parser robustness, recompute determinism, source-anchor
  dedup, eval-window planner, and member feature↔label match keys.
- **Architecture-enforcement tests** (`tests/architecture/test_layering.py`):
  17 foundation/domain packages never import the orchestration layer
  (runtime/api/pipeline), `src.core` is pure foundation, and the prediction
  and conflict-scoring subsystems stay decoupled from each other's engines.
- Developer guide (`docs/development.md`), operator runbook
  (`docs/operations.md`), and ADRs **0001** (cutoff-safe / no-leakage
  prediction), **0002** (artifact-first immutable publishing), and **0003**
  (conservative source-anchor availability).

### Changed

- Raised module-level coverage to 88–100% across ~18 foundational/domain
  modules (`core/logging`, `provenance/{events,models}`, `export/{__init__,builders}`,
  `runtime/member_fec_crosswalk`, `ingest/fec/bulk`,
  `ingest/congress/{house_votes,senate_votes,primary_sponsors}`,
  `parse/disclosures/download`, `scoring/semantics`,
  `normalize/taxonomy_validator`, `query/{prediction_inventory,conflict}`,
  `db/repositories`, plus large gains in `api/http` 76 → 91% and
  `ontology/static_schema` 85 → 90%).
- `dedupe_prediction_source_anchors` now sorts identity tuples with a
  None-safe key (`_identity_sort_key`), making the canonical order total and
  deterministic even when legislative context fields are mixed null/string.
- Integration fixtures (`tests/integration/test_recompute_publish_integration.py`,
  `tests/integration/test_roundtrip_verify_integration.py`) carry HTTPS source
  URLs for the disclosure and committee-membership anchors that production
  recompute threads in, so the full DB → recompute → publish → verify
  roundtrip is green against real Postgres.
- Refactored `normalize/taxonomy_validator`'s CLI `__main__` block into a
  testable `main(data_root) -> int`.

### Fixed

- **Found by fuzz testing:** real reachable `TypeError` in
  `dedupe_prediction_source_anchors` when two legislative anchors share a
  `source_id` but one carries portable jurisdiction context and the other
  does not (None vs string in the identity-tuple sort).
- `TypeError` crash in `verify_prediction_input_inventory_command` on
  schema-valid artifacts with an unpaired vote-history member count; guard
  now mirrors the contract validator (`numerator is None or denominator is None`).
- Two mis-coded bandit suppressions: `# nosec B110` → `# nosec B112` on the
  two `try/except/continue` sites in `runtime/publish_verify_prediction.py`.
- Strict-mypy None-handling in `runtime/prediction_input_inventory`,
  `runtime/prediction_operator_resume_run` (loop hoist + `TypeGuard[list[str]]`),
  and `runtime/prediction_backtest` (`TypeGuard[list[str]]`).
- Two newly-added test assertions that contradicted their own success-path
  bodies (`test_dispatch_recompute_loads_statement_rows_jsonl`,
  `test_dispatch_materializes_statement_rows`).

### Removed

- Six unreferenced private functions: `_sponsor_context_key`,
  `_ensure_cutoff_pair`, `_sample_vote_event_ids_for_unavailable_signal_predictions`,
  `_is_numeric_non_bool`, `_audit_sample_context_counts`,
  `_audit_source_family_ids`. Repo-wide scan confirms no other dead private
  functions, classes, or constants remain.

### Security

- Annotated parameterized f-string SQL in `src/query/{published_rows,prediction_inventory,member_terms}.py`
  with justified `# nosec B608` comments; bandit clean.
- DB-integration suite (37/37) runs against real Postgres 16 in CI, exercising
  the recompute → publish → verify roundtrip including tamper detection.
