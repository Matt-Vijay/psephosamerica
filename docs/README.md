# Psephos America Documentation

Start with the [repository README](../README.md). This index distinguishes
runnable work from historical proposals; it is not an additional roadmap.

## Runnable work

- [RegPatch](../src/regpatch/README.md): public demo, source acquisition, private
  suites, scoring, and isolation. The candidate contract is
  [TASK.md](../src/regpatch/pilot/TASK.md); measurements are alongside it.
- [Time Machine](time-machine-v1.md): local legislative inventory, canonical
  build, integrity report, and point-in-time queries.
- [Transfer Eval](../src/transfer_eval/README.md): bounded executable
  OpenStates normalization pilot.
- [Development](development.md): focused checks and the separate legacy CI gates.

## Research evidence

- [Legislature replay](legislature-replay-feasibility.md).
- [Harvey/Legora signal audit](harvey-legora-signal-audit.md).
- [Legislative amendment patch audit](legislative-patch-feasibility.md).
- [Earlier environment comparison](flagship-environment-decision.md) and
  [incident prototype findings](incident-lab-research.md).

These reports retain their original measured scope and negative findings. They
are not new implementation instructions or claims about RegPatch difficulty.

## Historical ledger and graph reference

- [`/METHODOLOGY.md`](../METHODOLOGY.md) — launch methodology, source set,
  rule philosophy, fact/inference/judgment separation, snapshot policy,
  known limitations.
- [`/ENGINEERING_SPEC_V1.md`](../ENGINEERING_SPEC_V1.md) — earlier ledger spec.
- [`/OVERALL_GOAL.md`](../OVERALL_GOAL.md) — archived universal-prediction proposal.
- [`scoring.md`](scoring.md) — public scoring formulas (baseline, severity
  → delta, aggregate, per-rule severities).
- [`/CHANGELOG.md`](../CHANGELOG.md) — Keep-a-Changelog record of changes.
- [`corrections.md`](corrections.md) — public correction channel, what
  counts as a correction, SLA, processing flow.

## Operating the legacy system

- [`operations.md`](operations.md) — operator runbook: `bootstrap-db →
  ingest → recompute → publish → verify → aggregate`, the prediction
  operator surface, and integrity/cadence rules.

## Architecture & ontology

- [`ontology-contract.md`](ontology-contract.md) — stable website /
  LLM-facing ontology artifact contract.
- [`graph-data-layer.md`](graph-data-layer.md) — the `src/graph/`
  universal data layer: bitemporal provenance, the entity-resolution
  pipeline (records → scoring → linker → canonical → persistent IDs),
  the heterogeneous knowledge graph + ingestion adapters, and the
  `EntityResolutionOutput` contract + CDC delta feed.
- [`ingestion-credentials.md`](ingestion-credentials.md) — exact env-var
  names for every credential-gated source (OpenStates, LegiScan,
  OpenSecrets, CourtListener, Vote Smart, X, …) and what each unblocks.
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
| Legacy operator CLI dispatch | `src/runtime/commands/`, `src/runtime/main.py` |
| Canonical schema | `db/schema.sql`, `db/migrations/` |
| Conflict rule engine | `src/rules/`, rule YAMLs in `src/rules/conflict_of_interest/` |
| Disclosure parser | `src/parse/disclosures/` |
| Prediction subsystem | `src/prediction/`, `src/runtime/prediction_*` |
| Graph data layer / entity resolution | `src/graph/` (see [`graph-data-layer.md`](graph-data-layer.md)) |
| Ontology | `src/ontology/`, `data/taxonomy/` |
| Read API | `src/api/`, `src/export/` |
| Architecture enforcement | `tests/architecture/test_layering.py` |
