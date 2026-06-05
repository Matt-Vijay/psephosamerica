# OpenPact Documentation

Project-level docs live alongside the code. Start here, then go to the
file that matches what you need.

## Methodology and product surface

- [`/METHODOLOGY.md`](../METHODOLOGY.md) — launch methodology, source set,
  rule philosophy, fact/inference/judgment separation, snapshot policy,
  known limitations.
- [`/ENGINEERING_SPEC_V1.md`](../ENGINEERING_SPEC_V1.md) — locked v1 spec.
- [`scoring.md`](scoring.md) — public scoring formulas (baseline, severity
  → delta, aggregate, per-rule severities).
- [`/CHANGELOG.md`](../CHANGELOG.md) — Keep-a-Changelog record of changes.
- [`corrections.md`](corrections.md) — public correction channel, what
  counts as a correction, SLA, processing flow.

## Operating the system

- [`development.md`](development.md) — environment setup and the exact
  quality gates every change must pass (ruff, format, bandit, compileall,
  mypy --strict, pytest unit + coverage gate, integration).
- [`operations.md`](operations.md) — operator runbook: `bootstrap-db →
  ingest → recompute → publish → verify → aggregate`, the prediction
  operator surface, and integrity/cadence rules.

## Architecture & ontology

- [`ontology-contract.md`](ontology-contract.md) — stable website /
  LLM-facing ontology artifact contract.
- Architecture decision records ([`adr/`](adr/)):
  - [`0001`](adr/0001-cutoff-safe-no-leakage-prediction.md) — cutoff-safe,
    no-leakage legislative prediction.
  - [`0002`](adr/0002-artifact-first-immutable-publishing.md) — artifact-
    first immutable publishing (canonical vs read-path separation,
    frozen manifest-verified snapshots).
  - [`0003`](adr/0003-conservative-source-anchor-availability.md) —
    conservative source-anchor availability policy for prediction
    features.

## Where to look in code

| Concern | Path |
|---|---|
| Operator CLI dispatch | `src/runtime/commands.py`, `src/runtime/main.py` |
| Canonical schema | `db/schema.sql`, `db/migrations/` |
| Conflict rule engine | `src/rules/`, rule YAMLs in `src/rules/conflict_of_interest/` |
| Disclosure parser | `src/parse/disclosures/` |
| Prediction subsystem | `src/prediction/`, `src/runtime/prediction_*` |
| Ontology | `src/ontology/`, `data/taxonomy/` |
| Read API | `src/api/`, `src/export/` |
| Architecture enforcement | `tests/architecture/test_layering.py` |
