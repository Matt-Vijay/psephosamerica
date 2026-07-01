# Operator Guide

Psephos America is a **batch publishing system**, not a live service: operators load
official source data into Postgres, run a deterministic recompute, and publish
immutable, manifest-verified snapshots that the public read API serves. This
guide documents the operator-facing CLI (`python -m src.runtime.main <command>`)
and the end-to-end run order.

## Configuration

Settings come from environment variables with the `PSEPHOS_` prefix
(`src/core/settings.py`):

| Variable | Purpose |
|---|---|
| `PSEPHOS_POSTGRES_DSN` | Canonical Postgres connection string |
| `PSEPHOS_CONGRESS_API_KEY` | Congress.gov API key (ingestion) |
| `PSEPHOS_FEC_API_KEY` | FEC API key (ingestion) |
| `PSEPHOS_SNAPSHOT_PREFIX` | Object-storage key prefix for published snapshots |

Every command emits a single JSON result object (`{"ok": true, ...}`) so runs
are scriptable and auditable.

## End-to-end run order

```text
bootstrap-db                # 1. apply db/schema.sql to the target database
materialize-congress-archive# 2a. fetch Congress.gov archive (members/committees/bills/votes)
load-congress[-local]       # 2b. load the archive into canonical tables
materialize-fec-bulk-files  # 3a. stage FEC bulk files
load-fec-local              # 3b. load committees/linkage/contributions
process-disclosures[-local] # 4. parse House/Senate disclosures + PTRs
recompute                   # 5. deterministic rule fires + evidence cards + score snapshots
publish                     # 6. export an immutable snapshot + SHA-256 manifest
verify-publish              # 7. verify the published snapshot's integrity
verify-publish-roundtrip    #    verify the DB->publish roundtrip
aggregate-history           # 8. merge per-snapshot roots into a history-serving root
status                      #    operator summary (run counts, latest ingestion/parse/artifact)
```

Each stage is idempotent and records provenance (`data_source`, `ingestion_run`,
`source_artifact`, `parse_run`). Source artifacts are stored immutably; see
`METHODOLOGY.md`.

## Recompute and publish

- `recompute` runs the four conflict-of-interest rule families against canonical
  data and writes `rule_fire`, `evidence_card`, and `score_snapshot` rows. It is
  deterministic: same inputs produce the same fires. See
  [ADR 0001](adr/0001-cutoff-safe-no-leakage-prediction.md) for the prediction
  side and [ADR 0002](adr/0002-artifact-first-immutable-publishing.md) for the
  publish contract.
- `publish` exports a dated, frozen snapshot tree (member profiles, ZIP feeds,
  evidence cards) plus a `SnapshotManifest` with a SHA-256 per file and a root
  hash. Published snapshots are immutable.
- `verify-publish` recomputes the manifest hashes and detects any tampering;
  `verify-publish-roundtrip` re-derives the published artifacts from the database
  and confirms they match.

## Prediction operator surface

The legislative vote-prediction pipeline is a separate, cutoff-safe operator flow:

```text
prediction-eval-window-plan     # plan rolling, cutoff-safe eval windows
prediction-input-inventory      # report input readiness for a window (+ verify-*)
prediction-backtest             # no-leakage backtest (baseline / ontology models)
prediction-eval-report          # benchmark models; calibration bins + Brier/log-loss
prediction-source-url-audit     # audit source-URL / official-source coverage
```

Each produces a JSON artifact and has a paired `verify-*` command that enforces
quality gates (run metadata, source coverage, cutoff safety). See
[ADR 0001](adr/0001-cutoff-safe-no-leakage-prediction.md).

## Integrity & cadence

- Recompute cadence is a weekly full recompute (with a clean path to daily).
- Published snapshots and raw source files are immutable; corrections are made at
  the data/mapping layer and reflected in a new snapshot, never by mutating a
  released one.
- Run all CI gates locally before publishing operator changes (see
  [development.md](development.md)).
